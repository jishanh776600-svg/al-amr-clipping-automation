"""Publishing Safety Gate integrating MasterControlService and PolicyEngine."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field

from clipping.agent.policy import ActionRiskTier, ActionScope, PolicyDecisionType, PolicyEngine
from clipping.agent.publishing.models import PublishingMode
from clipping.agent.vault.models import AccountPlatform
from clipping.control.models import SystemControlState
from clipping.control.repository import ControlRepository
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.publishing.safety_gate")


class SafetyGateResult(BaseModel):
    """Result of publishing safety evaluation."""
    model_config = ConfigDict(frozen=True)

    can_proceed: bool
    reason: str
    is_globally_locked: bool = False
    is_paused: bool = False
    is_emergency_stopped: bool = False
    requires_human_confirmation: bool = False
    matched_rule_id: Optional[str] = None


class PublishingSafetyGate:
    """
    Guards publishing operations against global emergency stops, automation pauses,
    operator locks, and PolicyEngine boundaries.
    """

    def __init__(
        self,
        control_repository: ControlRepository,
        policy_engine: PolicyEngine,
    ):
        self.control_repo = control_repository
        self.policy = policy_engine

    async def evaluate_safety(
        self,
        account_id: str,
        platform: AccountPlatform,
        publishing_mode: PublishingMode = PublishingMode.DRAFT,
    ) -> SafetyGateResult:
        """
        Evaluates whether a publishing operation is permitted to proceed right now.
        Returns can_proceed=True only if Master Control permits publishing and PolicyEngine authorizes it.
        """
        # 1. Master Control State Evaluation
        control_state = await self.control_repo.get_state()

        if control_state.emergency_stopped:
            logger.warning("Publishing blocked by Master Control: EMERGENCY STOPPED")
            return SafetyGateResult(
                can_proceed=False,
                reason="Global execution blocked: EMERGENCY STOP is active",
                is_emergency_stopped=True,
            )

        if control_state.automation_paused:
            logger.info("Publishing paused by Master Control: AUTOMATION PAUSED")
            return SafetyGateResult(
                can_proceed=False,
                reason="Global automation is currently paused by operator",
                is_paused=True,
            )

        if control_state.publishing_locked:
            logger.info("Publishing locked by Master Control: PUBLISHING LOCKED")
            return SafetyGateResult(
                can_proceed=False,
                reason="Publishing is globally locked by operator",
                is_globally_locked=True,
            )

        # 2. Policy Engine Authorization
        # Immediate public publishing is MUTATING_IRREVERSIBLE, draft/scheduled is MUTATING_REVERSIBLE
        is_immediate = publishing_mode == PublishingMode.IMMEDIATE
        risk_tier = ActionRiskTier.MUTATING_IRREVERSIBLE if is_immediate else ActionRiskTier.MUTATING_REVERSIBLE
        is_reversible = not is_immediate

        mode_action = "public" if is_immediate else publishing_mode.value
        scope = ActionScope(
            capability_name="publishing",
            action_name=f"publish_{platform.value}_{mode_action}",
            target_resource=f"account:{account_id}",
            is_reversible=is_reversible,
            risk_tier=risk_tier,
            parameters={"mode": publishing_mode.value, "platform": platform.value},
        )


        policy_eval = self.policy.evaluate(scope)

        if not policy_eval.allowed or policy_eval.requires_human_confirmation:
            logger.warning(
                "Publishing gated by PolicyEngine",
                decision=policy_eval.decision.value,
                reason=policy_eval.reason,
            )
            return SafetyGateResult(
                can_proceed=False,
                reason=policy_eval.reason,
                requires_human_confirmation=policy_eval.requires_human_confirmation,
                matched_rule_id=policy_eval.matched_rule_id,
            )

        return SafetyGateResult(
            can_proceed=True,
            reason="Authorized by Master Control and PolicyEngine",
            matched_rule_id=policy_eval.matched_rule_id,
        )
