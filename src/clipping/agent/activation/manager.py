"""Activation Session Manager with Ephemeral Challenge Binding and Telegram Integration.

Coordinates the human-in-the-loop verification state machine for service activations.
Strictly isolates raw OTP values to ephemeral in-memory validation:
- Zero raw OTP persistence
- Zero raw OTP logging
- Cryptographic replay prevention
- Single-use challenge consumption
- Telegram chat authorization verification
"""

import json
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import uuid

from clipping.agent.activation.models import ActivationState, ActivationSession, ActivationChallenge
from clipping.agent.escalation import EscalationRecord, EscalationReason, EscalationSeverity, EscalationContext
from clipping.approval.escalation_notifier import TelegramEscalationNotifier
from clipping.approval.security import SecurityValidator
from clipping.config.settings import Settings, get_settings
from clipping.logging.logger import get_logger
from clipping.storage.base import StorageDriver

logger = get_logger("clipping.agent.activation.manager")


class ActivationSessionManager:
    """
    Manages durable activation session state, enforces one-time challenge consumption,
    and bridges operator interactions via Telegram.
    """

    def __init__(
        self,
        storage_driver: StorageDriver,
        settings: Optional[Settings] = None,
        security_validator: Optional[SecurityValidator] = None,
    ):
        self.storage = storage_driver
        self.settings = settings or get_settings()
        self.security = security_validator or SecurityValidator(
            allowed_user_ids=self.settings.get_allowed_telegram_user_ids(),
            allowed_chat_ids=self.settings.get_allowed_telegram_chat_ids(),
        )

    def _get_session_key(self, session_id: str) -> str:
        return f"activation/sessions/{session_id}.json"

    async def _update_index(self, session_id: str) -> None:
        index_key = "activation/sessions_index.json"
        index = []
        if await self.storage.exists(index_key):
            try:
                data = await self.storage.download_bytes(index_key)
                index = json.loads(data.decode("utf-8"))
            except Exception:
                index = []
        if session_id not in index:
            index.insert(0, session_id)
            index = index[:100]
            await self.storage.upload_bytes(json.dumps(index).encode("utf-8"), index_key, content_type="application/json")

    async def save_session(self, session: ActivationSession) -> None:
        key = self._get_session_key(session.session_id)
        data = session.model_dump_json(indent=2).encode("utf-8")
        await self.storage.upload_bytes(data, key, content_type="application/json")
        await self._update_index(session.session_id)

    async def get_session(self, session_id: str) -> Optional[ActivationSession]:
        key = self._get_session_key(session_id)
        if not await self.storage.exists(key):
            return None
        data = await self.storage.download_bytes(key)
        return ActivationSession.model_validate_json(data.decode("utf-8"))

    async def list_sessions(self, limit: int = 20) -> List[ActivationSession]:
        index_key = "activation/sessions_index.json"
        if not await self.storage.exists(index_key):
            return []
        try:
            data = await self.storage.download_bytes(index_key)
            index = json.loads(data.decode("utf-8"))
        except Exception:
            return []

        sessions = []
        for sid in index[:limit]:
            s = await self.get_session(sid)
            if s:
                sessions.append(s)
        return sessions

    async def find_waiting_session(self) -> Optional[ActivationSession]:
        sessions = await self.list_sessions(limit=10)
        for s in sessions:
            if s.state in (ActivationState.WAITING_FOR_OPERATOR, ActivationState.OTP_REQUIRED) and not s.is_expired():
                if s.active_challenge and not s.active_challenge.is_expired() and not s.active_challenge.consumed:
                    return s
        return None

    async def start_session(
        self,
        service: str,
        account_identifier: str,
        ttl_seconds: int = 900,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ActivationSession:
        """Initializes a new activation session with designated TTL (default 15 mins)."""
        session_id = f"act_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl_seconds)

        session = ActivationSession(
            session_id=session_id,
            service=service.lower().strip(),
            account_identifier=account_identifier.strip(),
            state=ActivationState.ACTIVATION_STARTED,
            created_at=now,
            expires_at=expires_at,
            metadata=metadata or {},
        )
        await self.save_session(session)
        logger.info("Initialized new activation session", session_id=session_id, service=session.service, account=session.account_identifier)
        return session

    async def create_otp_challenge(
        self,
        session_id: str,
        challenge_ttl_seconds: int = 300,
        expected_length: int = 6,
    ) -> ActivationSession:
        """
        Creates a short-lived verification challenge bound to the session.
        Raw expected OTP values are NEVER persisted to storage.
        """
        session = await self.get_session(session_id)
        if not session:
            raise ValueError(f"Activation session not found: {session_id}")

        if session.is_expired():
            session = session.transition(
                ActivationState.SESSION_EXPIRED,
                error_message="Activation session expired before challenge was created",
            )
            await self.save_session(session)
            raise ValueError(f"Activation session {session_id} is expired")

        challenge_id = f"chl_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=challenge_ttl_seconds)

        challenge = ActivationChallenge(
            challenge_id=challenge_id,
            session_id=session_id,
            service=session.service,
            expected_length=expected_length,
            created_at=now,
            expires_at=expires_at,
            consumed=False,
        )

        session = session.transition(
            ActivationState.OTP_REQUIRED,
            active_challenge=challenge,
        )
        await self.save_session(session)
        logger.info("Created OTP challenge for session", session_id=session_id, challenge_id=challenge_id, ttl_sec=challenge_ttl_seconds)
        return session

    async def notify_operator_telegram(
        self,
        session_id: str,
        notifier: TelegramEscalationNotifier,
        chat_id: Optional[int] = None,
    ) -> ActivationSession:
        """Dispatches rich diagnostic Telegram alert asking the operator to reply with OTP."""
        session = await self.get_session(session_id)
        if not session:
            raise ValueError(f"Activation session not found: {session_id}")

        if not session.active_challenge or session.active_challenge.is_expired():
            raise ValueError(f"No active, valid challenge for session {session_id}")

        target_chat = chat_id or self.settings.TELEGRAM_CHAT_ID
        if not target_chat:
            logger.warning("Telegram chat ID not configured; operator escalation skipped", session_id=session_id)
            return session

        record = EscalationRecord(
            escalation_id=f"esc_{session.session_id}",
            task_id=f"task_act_{session.service}",
            reason=EscalationReason.MFA_REQUIRED,
            severity=EscalationSeverity.HIGH,
            context=EscalationContext(
                what_happened=f"Authentication challenge required for {session.service.upper()} activation.",
                why_it_happened="Provider requested OTP / two-factor identity verification.",
                decision_required=f"Reply with the verification code (OTP) for session `{session.session_id}`.",
                available_options=[
                    f"Reply with OTP (e.g. /otp {session.session_id} <code>)",
                    f"Cancel via Mission Control",
                ],
                metadata={
                    "session_id": session.session_id,
                    "challenge_id": session.active_challenge.challenge_id,
                    "service": session.service,
                    "account": session.account_identifier,
                },
            ),
        )

        dispatched = await notifier.notify(record)
        new_state = ActivationState.WAITING_FOR_OPERATOR if dispatched else ActivationState.TELEGRAM_ESCALATION_SENT

        session = session.transition(
            new_state,
            metadata_update={"telegram_dispatched": dispatched, "chat_id": target_chat},
        )
        session = session.model_copy(update={"telegram_chat_id": target_chat})
        await self.save_session(session)
        return session

    async def submit_otp(
        self,
        session_id: str,
        otp_code: str,
        sender_user_id: Optional[int] = None,
        sender_chat_id: Optional[int] = None,
    ) -> ActivationSession:
        """
        Validates an operator-submitted OTP for an active challenge:
        - Replay protection: challenge must be unconsumed.
        - Expiration checks on session and challenge.
        - Authorization check on sender if submitted via Telegram.
        - Consumes challenge on success.
        - ZERO raw OTP values are logged or written to disk.
        """
        session = await self.get_session(session_id)
        if not session:
            raise ValueError(f"Activation session not found: {session_id}")

        # 1. Check session expiration
        if session.is_expired():
            session = session.transition(ActivationState.SESSION_EXPIRED, error_message="Activation session has expired")
            await self.save_session(session)
            raise ValueError("Activation session has expired")

        # 2. Check active challenge presence
        challenge = session.active_challenge
        if not challenge:
            raise ValueError("No active verification challenge found on session")

        # 3. Check replay protection (single-use constraint)
        if challenge.consumed:
            logger.warning("Rejected replayed OTP submission for already-consumed challenge", session_id=session_id, challenge_id=challenge.challenge_id)
            session = session.transition(ActivationState.OTP_REJECTED, error_message="Challenge has already been consumed (replay prevented)")
            await self.save_session(session)
            raise ValueError("Verification challenge has already been consumed (replay rejected)")

        # 4. Check challenge expiration
        if challenge.is_expired():
            session = session.transition(ActivationState.OTP_EXPIRED, error_message="OTP challenge expired before submission")
            await self.save_session(session)
            raise ValueError("OTP verification challenge has expired")

        # 5. Check sender authorization if Telegram context provided
        if sender_user_id is not None or sender_chat_id is not None:
            authorized = self.security.is_authorized(user_id=sender_user_id or 0, chat_id=sender_chat_id or 0)
            if not authorized:
                logger.warning(
                    "Unauthorized operator OTP submission rejected",
                    session_id=session_id,
                    sender_user_id=sender_user_id,
                    sender_chat_id=sender_chat_id,
                )
                session = session.transition(ActivationState.OTP_REJECTED, error_message="Unauthorized Telegram sender")
                await self.save_session(session)
                raise PermissionError("Unauthorized sender: Telegram user/chat ID not in authorized operator list")

        # 6. Validate OTP format
        clean_otp = re.sub(r"\s+", "", str(otp_code).strip())
        if not clean_otp.isdigit() or len(clean_otp) < 4 or len(clean_otp) > 12:
            # Increment failed attempt count
            updated_chl = challenge.model_copy(update={"attempts": challenge.attempts + 1})
            new_state = ActivationState.OTP_REJECTED if updated_chl.attempts >= updated_chl.max_attempts else ActivationState.WAITING_FOR_OPERATOR
            session = session.transition(new_state, active_challenge=updated_chl, error_message="Invalid OTP format")
            await self.save_session(session)
            raise ValueError("Invalid verification code format (must be 4-12 digits)")

        # 7. Consume challenge successfully (Zero OTP storage)
        now = datetime.now(timezone.utc)
        consumed_chl = challenge.model_copy(update={"consumed": True, "consumed_at": now})
        session = session.transition(
            ActivationState.OTP_VALIDATED,
            active_challenge=consumed_chl,
            metadata_update={"otp_validated_at": now.isoformat()},
        )
        await self.save_session(session)
        logger.info("Successfully validated and consumed OTP challenge", session_id=session_id, challenge_id=challenge.challenge_id)
        return session

    async def complete_session(
        self,
        session_id: str,
        remote_identity: str,
    ) -> ActivationSession:
        """Marks activation complete after genuine remote provider identity verification."""
        session = await self.get_session(session_id)
        if not session:
            raise ValueError(f"Activation session not found: {session_id}")

        session = session.transition(
            ActivationState.ACTIVATION_COMPLETE,
            remote_identity=remote_identity,
        )
        await self.save_session(session)
        logger.info("Activation session successfully completed", session_id=session_id, remote_identity=remote_identity)
        return session

    async def cancel_session(
        self,
        session_id: str,
        reason: str = "operator_cancelled",
    ) -> ActivationSession:
        """Allows operator to abort an active session."""
        session = await self.get_session(session_id)
        if not session:
            raise ValueError(f"Activation session not found: {session_id}")

        session = session.transition(
            ActivationState.OPERATOR_CANCELLED,
            error_message=f"Cancelled by operator: {reason}",
        )
        await self.save_session(session)
        logger.info("Activation session cancelled", session_id=session_id, reason=reason)
        return session
