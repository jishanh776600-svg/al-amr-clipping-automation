"""Telegram Escalation Notifier for Real-Time Human-in-the-Loop Operator Alerts.

Dispatches actionable operator alerts to Telegram when autonomous operations
encounter security challenges (CAPTCHA / Turnstile / MFA), QA gating failures,
contradictory brief terms, account lockouts, or unrecoverable platform errors.
"""

from typing import Optional
from clipping.agent.escalation import EscalationRecord, EscalationSeverity
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
        """Constructs an actionable, zero-guesswork operator alert text."""
        sev_icon = "🚨" if record.severity in (EscalationSeverity.CRITICAL, EscalationSeverity.HIGH) else "⚠️"
        cid = record.campaign_id or "N/A"
        ctx = record.context

        lines = [
            f"{sev_icon} *AL AMR CLIPPING — OPERATOR ESCALATION*",
            "",
            f"*Escalation ID:* `{record.escalation_id}`",
            f"*Severity:* `{record.severity.value.upper()}`",
            f"*Reason:* `{record.reason.value.upper()}`",
            f"*Campaign:* `{cid}`",
            f"*Task ID:* `{record.task_id}`",
            "",
            f"*What Happened:* {ctx.what_happened}",
            f"*Diagnosed Cause:* {ctx.why_it_happened}",
            "",
            f"*Decision Required:* {ctx.decision_required}",
        ]

        if ctx.available_options:
            lines.append("")
            lines.append("*Actionable Options:*")
            for opt in ctx.available_options:
                lines.append(f"• `{opt}`")

        lines.append("")
        lines.append("⚡ _Resolve via Mission Control Console or respond in designated command session._")
        return "\n".join(lines)

    async def notify(self, record: EscalationRecord) -> bool:
        """
        Sends formatted escalation alert to configured Telegram chat.
        Fails safely and logs diagnostic info without throwing unhandled exceptions.
        """
        if not self.is_configured:
            logger.info(
                "Telegram escalation notification deferred: Telegram Bot or Chat ID not configured",
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
