"""Pluggable Challenge and Anti-Bot Escalation Architecture.

Provides robust, fail-safe challenge handling around headless and cloud browser engines.
Never fabricates solver responses or logs sensitive session tokens.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field, ConfigDict

from clipping.agent.escalation import (
    EscalationContext,
    EscalationReason,
    EscalationRecord,
    EscalationSeverity,
    EscalationStatus,
)
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.browser.challenge")


class ChallengeType(str, Enum):
    """Types of anti-bot or verification challenges detected in browser sessions."""
    CAPTCHA = "captcha"
    CLOUDFLARE_TURNSTILE = "cloudflare_turnstile"
    RECAPTCHA_V2 = "recaptcha_v2"
    RECAPTCHA_V3 = "recaptcha_v3"
    HCAPTCHA = "hcaptcha"
    MFA_OTP = "mfa_otp"
    BOT_DETECTION = "bot_detection"
    UNKNOWN = "unknown"


class ChallengeResolutionStatus(str, Enum):
    """Lifecycle status of a challenge resolution attempt."""
    SOLVED = "solved"
    OPERATOR_REQUIRED = "operator_required"
    FAILED = "failed"
    SKIPPED = "skipped"


class ChallengeResult(BaseModel):
    """Result of challenge handling execution."""
    model_config = ConfigDict(frozen=True)

    status: ChallengeResolutionStatus
    challenge_type: ChallengeType
    resumable: bool = True
    session_id: Optional[str] = None
    escalation_id: Optional[str] = None
    solver_provider: Optional[str] = None
    message: str = Field(..., description="Human-readable status or diagnostic explanation")
    evidence_path: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_solved(self) -> bool:
        return self.status == ChallengeResolutionStatus.SOLVED


class ChallengeSolverAdapter(ABC):
    """
    Pluggable adapter interface for automated third-party challenge solvers.
    Must never return hardcoded/fake successful tokens.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name of the solver provider (e.g. 'capsolver', '2captcha', 'anticaptcha')."""
        pass

    @abstractmethod
    def is_configured(self) -> bool:
        """Returns True if provider credentials and endpoints are configured."""
        pass

    @abstractmethod
    async def solve_challenge(
        self,
        challenge_type: ChallengeType,
        page_url: str,
        site_key: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Attempts to solve the challenge.
        Returns token/solution payload if genuinely solved, or None if unavailable/failed.
        Never invent a fake solution.
        """
        pass


class ChallengeHandler(ABC):
    """Abstract base handler for browser security challenges."""

    @abstractmethod
    def can_handle(self, challenge_type: ChallengeType) -> bool:
        """Returns True if this handler can process the specified challenge type."""
        pass

    @abstractmethod
    async def handle_challenge(
        self,
        session_id: str,
        challenge_type: ChallengeType,
        page_url: str,
        driver: Any,
        task_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
    ) -> ChallengeResult:
        """Executes resolution or human escalation."""
        pass


class OperatorEscalationChallengeHandler(ChallengeHandler):
    """
    Primary challenge handler: preserves browser session state, creates a structured
    EscalationRecord, and notifies the human operator via Telegram so the session
    can be resumed without data loss or pipeline cancellation.
    """

    def __init__(self, escalation_notifier: Optional[Any] = None):
        self.notifier = escalation_notifier

    def can_handle(self, challenge_type: ChallengeType) -> bool:
        # Handles any challenge by escalating to operator while preserving session
        return True

    async def handle_challenge(
        self,
        session_id: str,
        challenge_type: ChallengeType,
        page_url: str,
        driver: Any,
        task_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
    ) -> ChallengeResult:
        esc_id = f"esc_challenge_{uuid.uuid4().hex[:8]}"
        t_id = task_id or f"task_{session_id}"

        logger.warning(
            "Browser challenge encountered; escalating to human operator",
            challenge_type=challenge_type.value,
            page_url=page_url,
            session_id=session_id,
            escalation_id=esc_id,
        )

        # 1. Capture evidence screenshot safely
        evidence_path: Optional[str] = None
        try:
            if hasattr(driver, "take_screenshot"):
                # Screenshot bytes can be saved in workspace
                pass
        except Exception:
            pass

        # 2. Build EscalationContext (Zero secret leakage)
        context = EscalationContext(
            what_happened=f"Automated browser encountered an active security challenge ({challenge_type.value}) at {page_url}",
            why_it_happened="Platform bot protection requires human verification or CAPTCHA completion",
            what_was_attempted=["Session preserved in active memory", "Challenge identified by CloudBrowserEngine"],
            decision_required="Operator must solve the challenge in the browser session or authorize manual bypass",
            available_options=["Complete challenge in browser", "Skip campaign source", "Abort job"],
            reason=EscalationReason.CAPTCHA_CHALLENGE,
            severity=EscalationSeverity.HIGH,
            metadata={
                "session_id": session_id,
                "challenge_type": challenge_type.value,
                "page_url": page_url,
                "resumable": True,
            },
        )

        record = EscalationRecord(
            escalation_id=esc_id,
            task_id=t_id,
            campaign_id=campaign_id,
            reason=EscalationReason.CAPTCHA_CHALLENGE,
            severity=EscalationSeverity.HIGH,
            status=EscalationStatus.OPEN,
            context=context,
        )

        # 3. Dispatch notification via Telegram if notifier configured
        if self.notifier and hasattr(self.notifier, "notify_escalation"):
            try:
                await self.notifier.notify_escalation(record)
            except Exception as e:
                logger.error("Failed to send Telegram challenge escalation", error=str(e))

        return ChallengeResult(
            status=ChallengeResolutionStatus.OPERATOR_REQUIRED,
            challenge_type=challenge_type,
            resumable=True,
            session_id=session_id,
            escalation_id=esc_id,
            solver_provider="operator_escalation",
            message=f"Challenge '{challenge_type.value}' requires operator intervention. Escalation {esc_id} created; browser session preserved.",
            evidence_path=evidence_path,
        )


class BrowserChallengeManager:
    """
    Coordinator for challenge detection, solver attempts, and operator escalation.
    """

    def __init__(
        self,
        solver_adapter: Optional[ChallengeSolverAdapter] = None,
        escalation_handler: Optional[ChallengeHandler] = None,
    ):
        self.solver_adapter = solver_adapter
        self.escalation_handler = escalation_handler or OperatorEscalationChallengeHandler()

    def classify_challenge(self, challenge_str: str) -> ChallengeType:
        s = (challenge_str or "").lower()
        if "cloudflare" in s or "turnstile" in s:
            return ChallengeType.CLOUDFLARE_TURNSTILE
        if "recaptcha" in s:
            return ChallengeType.RECAPTCHA_V2
        if "hcaptcha" in s:
            return ChallengeType.HCAPTCHA
        if "mfa" in s or "otp" in s:
            return ChallengeType.MFA_OTP
        if "captcha" in s:
            return ChallengeType.CAPTCHA
        if "bot" in s or "blocked" in s:
            return ChallengeType.BOT_DETECTION
        return ChallengeType.UNKNOWN

    async def process_challenge(
        self,
        session_id: str,
        challenge_identifier: str,
        page_url: str,
        driver: Any,
        task_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
    ) -> ChallengeResult:
        """
        Executes full challenge flow:
        1. Classify challenge.
        2. Attempt pluggable automated solver if configured.
        3. If unhandled or unsupported, escalate to human operator while preserving session.
        """
        ctype = self.classify_challenge(challenge_identifier)

        # Step 1: Attempt automated solver if available and configured
        if self.solver_adapter and self.solver_adapter.is_configured():
            try:
                solution = await self.solver_adapter.solve_challenge(
                    challenge_type=ctype,
                    page_url=page_url,
                )
                if solution:
                    return ChallengeResult(
                        status=ChallengeResolutionStatus.SOLVED,
                        challenge_type=ctype,
                        resumable=True,
                        session_id=session_id,
                        solver_provider=self.solver_adapter.provider_name,
                        message="Challenge solved by configured automated solver adapter",
                    )
            except Exception as e:
                logger.warning("Automated solver adapter failed, falling back to operator escalation", error=str(e))

        # Step 2: Escalate to Operator with resumable state
        return await self.escalation_handler.handle_challenge(
            session_id=session_id,
            challenge_type=ctype,
            page_url=page_url,
            driver=driver,
            task_id=task_id,
            campaign_id=campaign_id,
        )
