"""Security and authorization validation for Telegram Approval Gateway."""

from typing import Set, Optional
from clipping.approval.models import TelegramCallbackPayload
from clipping.logging.logger import get_logger

logger = get_logger("clipping.approval.security")


class SecurityError(Exception):
    """Raised when an unauthorized or malformed action occurs."""
    pass


class SecurityValidator:
    """
    Guards the Telegram approval gateway against unauthorized access,
    callback spoofing, and data leakage.
    """

    def __init__(
        self,
        allowed_user_ids: Optional[Set[int]] = None,
        allowed_chat_ids: Optional[Set[int]] = None,
    ):
        self.allowed_user_ids = set(allowed_user_ids or [])
        self.allowed_chat_ids = set(allowed_chat_ids or [])

    def is_user_authorized(self, user_id: int) -> bool:
        """
        Validates if the user is authorized.
        If allowed_user_ids is empty, authorization is restricted (returns False).
        """
        if not self.allowed_user_ids:
            # If not configured, reject by default to prevent open vulnerability
            logger.warning("No allowed_user_ids configured. Rejecting access by default.", user_id=user_id)
            return False
        return user_id in self.allowed_user_ids

    def is_chat_authorized(self, chat_id: int) -> bool:
        """Validates if the message originating chat is authorized."""
        if not self.allowed_chat_ids:
            return True  # If no chat restriction, allow verified authorized users in any chat
        return chat_id in self.allowed_chat_ids

    def validate_callback_payload(self, data: str) -> TelegramCallbackPayload:
        """Parses and cryptographically/structurally validates the compact callback payload."""
        if not data or len(data) > 64:
            raise SecurityError(f"Callback payload invalid length: {len(data) if data else 0} bytes")
        try:
            return TelegramCallbackPayload.parse(data)
        except Exception as e:
            raise SecurityError(f"Malformed callback payload: {str(e)}")

    def sanitize_for_telegram(self, text: str) -> str:
        """Removes internal paths, credentials, and tokens from user-facing text."""
        # Defense-in-depth sanitization
        for token_word in ["token", "secret", "private_key", "password", "bearer"]:
            if token_word in text.lower():
                return "Operation could not be processed due to a security constraint."
        return text
