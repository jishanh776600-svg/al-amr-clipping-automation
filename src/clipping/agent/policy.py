"""Policy Engine governing autonomous execution permissions, risk tiers, and escalation boundaries."""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict


class PolicyDecisionType(str, Enum):
    """Authoritative verdict rendered by the Policy Engine."""
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"
    ESCALATE = "escalate"


class ActionRiskTier(str, Enum):
    """Inherent risk classification of an agent action."""
    ROUTINE_READ = "routine_read"              # Read-only operations, queries, inspection
    ROUTINE_COMPUTE = "routine_compute"        # Local processing, transcription, vision inference
    MUTATING_REVERSIBLE = "mutating_reversible"# Uploading drafts, creating scratch assets, staging
    MUTATING_IRREVERSIBLE = "mutating_irreversible" # Public publishing, account deletion, unrecoverable data modifications


class ActionScope(BaseModel):
    """Contextual description of an action submitted for policy authorization."""
    model_config = ConfigDict(frozen=True)

    capability_name: str
    action_name: str
    target_resource: str
    is_reversible: bool = True
    risk_tier: ActionRiskTier = ActionRiskTier.ROUTINE_COMPUTE
    parameters: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PolicyRule(BaseModel):
    """Declarative authorization rule matching an action pattern."""
    model_config = ConfigDict(frozen=True)

    rule_id: str
    description: str
    capability_pattern: str = "*"  # Glob or exact name
    action_pattern: str = "*"
    decision: PolicyDecisionType = PolicyDecisionType.ALLOW
    requires_human_confirmation: bool = False
    priority: int = Field(default=100, description="Higher priority rules evaluate first")
    reason: Optional[str] = None


class PolicyEvaluationResult(BaseModel):
    """Auditable result of a policy evaluation."""
    model_config = ConfigDict(frozen=True)

    decision: PolicyDecisionType
    allowed: bool
    requires_human_confirmation: bool
    matched_rule_id: Optional[str] = None
    reason: str
    action_scope: ActionScope


class PolicyEngine:
    """
    Guards autonomous execution boundaries.
    Enforces the core operational principle:
    - Routine authorized actions: execute automatically.
    - Genuine exceptions & irreversible actions: escalate to human operator.
    """

    def __init__(
        self,
        rules: Optional[List[PolicyRule]] = None,
        default_decision: PolicyDecisionType = PolicyDecisionType.DENY,
        require_confirmation_for_irreversible: bool = True,
    ):
        self._rules = sorted(rules or self._default_rules(), key=lambda r: r.priority, reverse=True)
        self._default_decision = default_decision
        self._require_confirmation_for_irreversible = require_confirmation_for_irreversible

    @classmethod
    def _default_rules(cls) -> List[PolicyRule]:
        return [
            # 1. Allow routine media processing & intelligence
            PolicyRule(
                rule_id="RULE_ALLOW_MEDIA_CLIPPING",
                description="Allow routine video ingestion, transcription, framing, and rendering",
                capability_pattern="media_clipping",
                action_pattern="*",
                decision=PolicyDecisionType.ALLOW,
                priority=100,
                reason="Routine autonomous media clipping is pre-authorized",
            ),
            # 2. Allow routine document and campaign analysis
            PolicyRule(
                rule_id="RULE_ALLOW_CAMPAIGN_READ",
                description="Allow reading and analyzing campaign briefs",
                capability_pattern="campaign_*",
                action_pattern="read_*",
                decision=PolicyDecisionType.ALLOW,
                priority=90,
                reason="Read-only campaign analysis is permitted",
            ),
            # 3. Require human confirmation before public publishing
            PolicyRule(
                rule_id="RULE_GATE_PUBLIC_PUBLISHING",
                description="Public distribution requires human approval",
                capability_pattern="*publish*",
                action_pattern="*public*",
                decision=PolicyDecisionType.REQUIRE_CONFIRMATION,
                requires_human_confirmation=True,
                priority=200,
                reason="Direct-to-public publishing is an irreversible action requiring human verification",
            ),
            # 4. Allow routine autonomous publishing (drafts, scheduled releases, campaign submissions)
            PolicyRule(
                rule_id="RULE_ALLOW_ROUTINE_PUBLISHING",
                description="Allow routine draft preparation, scheduled publication, and campaign submissions",
                capability_pattern="*publish*",
                action_pattern="*",
                decision=PolicyDecisionType.ALLOW,
                requires_human_confirmation=False,
                priority=120,
                reason="Routine draft preparation and scheduled publishing is pre-authorized by policy",
            ),

            # 4. Escalate credential/account deletions
            PolicyRule(
                rule_id="RULE_BLOCK_DESTRUCTIVE_ACCOUNT_OPS",
                description="Account deletion or critical credential revocation requires escalation",
                capability_pattern="account_*",
                action_pattern="delete_*",
                decision=PolicyDecisionType.ESCALATE,
                priority=300,
                reason="Destructive account mutations require explicit escalation to human owner",
            ),
            # 5. Allow routine autonomous channel and account creation within policy limits
            PolicyRule(
                rule_id="RULE_ALLOW_CHANNEL_CREATION",
                description="Allow routine autonomous channel and account creation within policy limits",
                capability_pattern="account_*",
                action_pattern="create_*",
                decision=PolicyDecisionType.ALLOW,
                requires_human_confirmation=False,
                priority=150,
                reason="Routine channel creation is pre-authorized by policy",
            ),
            # 6. Allow routine account configuration and campaign association
            PolicyRule(
                rule_id="RULE_ALLOW_ACCOUNT_MANAGEMENT",
                description="Allow routine channel configuration, metadata updates, and campaign association",
                capability_pattern="account_*",
                action_pattern="*",
                decision=PolicyDecisionType.ALLOW,
                requires_human_confirmation=False,
                priority=110,
                reason="Routine account configuration is pre-authorized",
            ),
        ]


    def evaluate(self, scope: ActionScope) -> PolicyEvaluationResult:
        """Evaluates whether an action is permitted according to configured policy rules."""
        import fnmatch

        # Irreversible guardrail check
        if not scope.is_reversible and self._require_confirmation_for_irreversible:
            if scope.risk_tier == ActionRiskTier.MUTATING_IRREVERSIBLE:
                # Check if there is an explicit override rule allowing it, otherwise require confirmation
                for rule in self._rules:
                    if fnmatch.fnmatch(scope.capability_name, rule.capability_pattern) and fnmatch.fnmatch(scope.action_name, rule.action_pattern):
                        if rule.decision == PolicyDecisionType.DENY:
                            return PolicyEvaluationResult(
                                decision=PolicyDecisionType.DENY,
                                allowed=False,
                                requires_human_confirmation=False,
                                matched_rule_id=rule.rule_id,
                                reason=rule.reason or "Explicitly denied by policy",
                                action_scope=scope,
                            )
                        if rule.requires_human_confirmation:
                            return PolicyEvaluationResult(
                                decision=PolicyDecisionType.REQUIRE_CONFIRMATION,
                                allowed=False,
                                requires_human_confirmation=True,
                                matched_rule_id=rule.rule_id,
                                reason=rule.reason or "Irreversible operation requires human confirmation",
                                action_scope=scope,
                            )

                return PolicyEvaluationResult(
                    decision=PolicyDecisionType.REQUIRE_CONFIRMATION,
                    allowed=False,
                    requires_human_confirmation=True,
                    matched_rule_id="DEFAULT_IRREVERSIBLE_GUARD",
                    reason="Potentially irreversible mutating action requires human confirmation",
                    action_scope=scope,
                )

        # Standard rule evaluation
        for rule in self._rules:
            if fnmatch.fnmatch(scope.capability_name, rule.capability_pattern) and fnmatch.fnmatch(scope.action_name, rule.action_pattern):
                allowed = rule.decision == PolicyDecisionType.ALLOW
                return PolicyEvaluationResult(
                    decision=rule.decision,
                    allowed=allowed,
                    requires_human_confirmation=rule.requires_human_confirmation,
                    matched_rule_id=rule.rule_id,
                    reason=rule.reason or f"Matched rule {rule.rule_id}",
                    action_scope=scope,
                )

        # Fallback to default decision
        return PolicyEvaluationResult(
            decision=self._default_decision,
            allowed=self._default_decision == PolicyDecisionType.ALLOW,
            requires_human_confirmation=self._default_decision == PolicyDecisionType.REQUIRE_CONFIRMATION,
            matched_rule_id="DEFAULT_FALLBACK",
            reason=f"No matching policy rule found; applied default: {self._default_decision.value}",
            action_scope=scope,
        )
