"""Telegram Escalation Notifier for Real-Time Human-in-the-Loop Operator Alerts.

Dispatches actionable operator alerts to Telegram when autonomous operations
encounter security challenges (CAPTCHA / Turnstile / MFA), QA gating failures,
contradictory brief terms, account lockouts, or unrecoverable platform errors.
"""

from typing import Optional
from clipping.agent.escalation import EscalationReason, EscalationRecord, EscalationSeverity
from clipping.approval.transport import HttpTelegramTransport, TelegramTransport
from clipping.config.settings import Settings, get_settings
from clipping.logging.logger import get_logger

logger = get_logger("clipping.approval.escalation_notifier")


class TelegramEscalationNotifier:
    """
    Formats rich diagnostic markdown alerts for human operators and dispatches
    them via Telegram Bot API to designated emergency chat IDs.
    """

    def __init__(
        self,
        transport: Optional[TelegramTransport] = None,
        chat_id: Optional[int] = None,
        settings: Optional[Settings] = None,
    ):
        cfg = settings or get_settings()
        self._chat_id = chat_id or cfg.TELEGRAM_CHAT_ID
        if transport:
            self._transport = transport
        elif cfg.TELEGRAM_BOT_TOKEN:
            self._transport = HttpTelegramTransport(bot_token=cfg.TELEGRAM_BOT_TOKEN.get_secret_value())
        else:
            self._transport = None

    @property
    def is_configured(self) -> bool:
        """Indicates if Telegram transport and target chat ID are configured."""
        return self._transport is not None and self._chat_id is not None

    def format_alert_message(self, record: EscalationRecord) -> str:
        """Constructs concise, professional control room alerts without internal debugging noise."""
        cid = record.campaign_id
        campaign_display = cid if cid and cid not in ("N/A", "None", "") else "Production Campaign"
        ctx = record.context

        # Check for verification / authentication challenge on an operator-provided source
        is_verification = (
            record.reason in (EscalationReason.CAPTCHA_CHALLENGE, EscalationReason.MFA_REQUIRED, EscalationReason.IDENTITY_VERIFICATION, EscalationReason.PLATFORM_BLOCKED)
            or "challenge" in (ctx.what_happened or "").lower()
            or "verification" in (ctx.what_happened or "").lower()
            or "captcha" in (ctx.what_happened or "").lower()
        )

        stage_display = ctx.metadata.get("stage") or ("Source Access" if is_verification else "Production Execution")

        if is_verification:
            challenge_url = ctx.metadata.get("challenge_url") or ctx.metadata.get("url") or ctx.metadata.get("source_url")
            lines = [
                "⚠️ *PRODUCTION ACTION REQUIRED*",
                "",
                f"*Campaign:* {campaign_display}",
                f"*Stage:* {stage_display}",
                "",
                "The source requires verification before production can continue.",
            ]
            if challenge_url:
                lines.extend([
                    "",
                    f"Verification URL: {challenge_url}",
                ])
            lines.extend([
                "",
                "Please complete the verification using the provided link, then return to Mission Control and select RESUME.",
            ])
            return "\n".join(lines)

        # Standard professional operator escalation
        lines = [
            "⚠️ *PRODUCTION ACTION REQUIRED*",
            "",
            f"*Campaign:* {campaign_display}",
            f"*Stage:* {stage_display}",
            "",
            ctx.what_happened or "A production task requires human operator review.",
            "",
            "Open Mission Control to review and resume production.",
        ]
        return "\n".join(lines)

    @staticmethod
    def format_campaign_input_required_message() -> str:
        """Constructs standard campaign input notification."""
        return (
            "⚠️ *CAMPAIGN INPUT REQUIRED*\n\n"
            "No production campaign has been submitted.\n\n"
            "Open Mission Control, select your campaign, upload the campaign brief, and provide the source video.\n\n"
            "The system will wait for your input."
        )

    @staticmethod
    def format_clip_ready_message(
        campaign_name: str,
        clip_num: str = "01",
        duration: float = 32.4,
        format_res: str = "1080 × 1920",
        checks_passed: int = 12,
        total_checks: int = 12,
    ) -> str:
        """Constructs clip review notification."""
        return (
            "✓ *CLIP READY FOR REVIEW*\n\n"
            f"*Campaign:* {campaign_name}\n"
            f"*Clip:* {clip_num}\n"
            f"*Duration:* {duration:.1f}s\n"
            f"*Format:* {format_res}\n\n"
            f"*Compliance:* {checks_passed}/{total_checks} checks passed.\n\n"
            "Review the clip below and approve or request a revision."
        )

    @staticmethod
    def format_publishing_message(
        campaign_name: str,
        yt_status: str = "Uploading",
        ig_status: str = "Uploading",
    ) -> str:
        """Constructs publishing progress notification."""
        return (
            "◉ *PUBLISHING*\n\n"
            f"*Campaign:* {campaign_name}\n\n"
            f"*YouTube Shorts:* {yt_status}\n"
            f"*Instagram Reels:* {ig_status}\n\n"
            "Publication will be verified after both platforms respond."
        )

    @staticmethod
    def format_published_message(
        campaign_name: str,
        yt_status: str = "Published",
        ig_status: str = "Published",
    ) -> str:
        """Constructs publishing completion notification."""
        return (
            "✓ *PUBLISHED*\n\n"
            f"*Campaign:* {campaign_name}\n\n"
            f"*YouTube Shorts:* {yt_status}\n"
            f"*Instagram Reels:* {ig_status}\n\n"
            "Both publications have been verified."
        )

    async def notify(self, record: EscalationRecord) -> bool:
        """
        Sends formatted escalation alert to configured Telegram chat.
        Suppresses legacy campaign discovery escalations.
        Fails safely and logs diagnostic info without throwing unhandled exceptions.
        """
        if not self.is_configured:
            logger.info(
                "Telegram escalation notification deferred: Telegram Bot or Chat ID not configured",
                escalation_id=record.escalation_id,
                reason=record.reason.value,
            )
            return False

        # HARD ARCHITECTURAL GUARD: Never send campaign discovery alerts to Telegram.
        what_happened_lower = (record.context.what_happened or "").lower()
        if (
            record.task_id.startswith("disc_")
            or "campaign discovery" in what_happened_lower
            or "whop" in what_happened_lower
            or "marketplace" in what_happened_lower
            or "creator rewards" in what_happened_lower
        ):
            logger.info(
                "Suppressed legacy campaign discovery escalation from Telegram alert",
                escalation_id=record.escalation_id,
                reason=record.reason.value,
            )
            return False

        message_text = self.format_alert_message(record)
        try:
            assert self._transport is not None
            assert self._chat_id is not None
            msg_id = await self._transport.send_message(
                chat_id=self._chat_id,
                text=message_text,
            )
            logger.info(
                "Successfully dispatched operator alert to Telegram",
                escalation_id=record.escalation_id,
                telegram_message_id=msg_id,
                chat_id=self._chat_id,
            )
            return True
        except Exception as e:
            logger.error(
                "Failed to dispatch Telegram escalation alert",
                escalation_id=record.escalation_id,
                error=str(e),
            )
            return False
