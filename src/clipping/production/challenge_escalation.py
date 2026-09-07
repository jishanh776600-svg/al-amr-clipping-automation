"""Actionable Telegram Notifications for Human Challenge Escalation & Session Resumption."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from clipping.contracts.production import OperatorInterventionRecord
from clipping.production.repository import ProductionRepository
from clipping.approval.transport import TelegramTransport
from clipping.logging.logger import get_logger

logger = get_logger("clipping.production.challenge_escalation")


class ChallengeEscalationManager:
    """
    Handles pause-and-escalate human interventions when CAPTCHA, Cloudflare,
    or MFA challenges block autonomous execution.
    Preserves checkpoints, delivers actionable instructions, and resumes reliably.
    """

    def __init__(
        self,
        repository: ProductionRepository,
        transport: Optional[TelegramTransport] = None,
    ):
        self.repository = repository
        self.transport = transport

    def format_escalation_message(self, record: OperatorInterventionRecord) -> str:
        """Formats the authoritative action-required notification."""
        url_section = (
            record.actionable_url
            if record.actionable_url and record.actionable_url.strip()
            else "⚠️ No direct challenge URL available (No direct browser URL available). Please open the operator workstation console."
        )

        steps_lines = []
        if record.human_steps:
            for i, step in enumerate(record.human_steps, 1):
                steps_lines.append(f"{i}. {step}")
        else:
            steps_lines = [
                "1. Open the URL or browser session above.",
                "2. Complete the CAPTCHA / verification challenge.",
                "3. Return to Telegram or Mission Control.",
                "4. Press [RESUME] or send /resume.",
            ]
        steps_str = "\n".join(steps_lines)

        msg = (
            f"🚨 ACTION REQUIRED\n\n"
            f"Campaign: {record.campaign_id}\n"
            f"Job ID: <code>{record.job_id}</code>\n"
            f"Stage: {record.checkpoint}\n"
            f"Reason: {record.challenge_type}\n\n"
            f"Open:\n"
            f"{url_section}\n\n"
            f"Steps:\n"
            f"{steps_str}\n\n"
            f"Session preserved: YES\n"
            f"Checkpoint: {record.checkpoint}\n"
            f"Intervention ID: <code>{record.intervention_id}</code>\n\n"
            f"Press [RESUME] below or reply: <code>/resume {record.intervention_id}</code>"
        )
        return msg

    def build_resume_keyboard(self, intervention_id: str) -> Dict[str, Any]:
        """Builds Telegram inline keyboard with RESUME callback (<= 64 bytes)."""
        clean_id = intervention_id[:48]
        return {
            "inline_keyboard": [
                [
                    {"text": "▶️ RESUME EXECUTION", "callback_data": f"res:{clean_id}"},
                ]
            ]
        }

    async def escalate(
        self,
        campaign_id: str,
        job_id: str,
        checkpoint: str = "SOURCE_ACCESS",
        challenge_type: str = "CAPTCHA_CHALLENGE",
        actionable_url: Optional[str] = None,
        human_steps: Optional[List[str]] = None,
        chat_id: Optional[int] = None,
    ) -> OperatorInterventionRecord:
        """
        Pauses autonomous workflow, saves checkpoint record, and sends actionable Telegram alert.
        Never fabricates a URL.
        """
        # Validate actionable URL: only allow genuine http(s) URLs; reject fabricated or placeholder strings
        clean_url = None
        if actionable_url and (actionable_url.startswith("http://") or actionable_url.startswith("https://")):
            clean_url = actionable_url.strip()

        inter_id = f"op_int_{uuid.uuid4().hex[:8]}"
        record = OperatorInterventionRecord(
            intervention_id=inter_id,
            campaign_id=campaign_id,
            job_id=job_id,
            challenge_type=challenge_type,
            checkpoint=checkpoint,
            actionable_url=clean_url,
            human_steps=human_steps or [],
            status="PENDING_OPERATOR",
            session_preserved=True,
            created_at=datetime.now(timezone.utc),
        )
        await self.repository.save_intervention(record)

        if self.transport and chat_id:
            msg_text = self.format_escalation_message(record)
            keyboard = self.build_resume_keyboard(inter_id)
            try:
                await self.transport.send_message(
                    chat_id=chat_id,
                    text=msg_text,
                    reply_markup=keyboard,
                )
            except Exception as e:
                logger.warning("Failed to dispatch Telegram escalation alert", error=str(e))

        logger.info(
            "Created operator intervention checkpoint",
            intervention_id=inter_id,
            campaign_id=campaign_id,
            job_id=job_id,
            checkpoint=checkpoint,
            has_actionable_url=bool(clean_url),
        )
        return record

    async def resume(self, intervention_id: str, chat_id: Optional[int] = None) -> Optional[OperatorInterventionRecord]:
        """Resumes a paused checkpoint after operator confirmation."""
        resumed = await self.repository.resume_intervention(intervention_id)
        if not resumed:
            logger.warning("Attempted to resume nonexistent intervention", intervention_id=intervention_id)
            return None

        if self.transport and chat_id:
            try:
                await self.transport.send_message(
                    chat_id=chat_id,
                    text=f"✅ Checkpoint `{resumed.checkpoint}` resumed for Campaign `{resumed.campaign_id}`. Pipeline continuing.",
                )
            except Exception:
                pass

        logger.info(
            "Resumed execution from preserved checkpoint",
            intervention_id=intervention_id,
            checkpoint=resumed.checkpoint,
            campaign_id=resumed.campaign_id,
        )
        return resumed
