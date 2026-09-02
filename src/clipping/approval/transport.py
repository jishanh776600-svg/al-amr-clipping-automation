"""Telegram Transport Layer with secure HTTP and Mock implementations."""

import asyncio
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
import httpx
from clipping.logging.logger import get_logger

logger = get_logger("clipping.approval.transport")


def mask_bot_token(url_or_text: str) -> str:
    """Masks bot token in URLs and error messages to prevent leakage into logs."""
    return re.sub(r"/bot[0-9]+:[A-Za-z0-9_-]+/", "/bot<MASKED_TOKEN>/", url_or_text)


class TelegramTransportError(Exception):
    """Raised when Telegram Bot API requests fail."""
    pass


class TelegramTransport(ABC):
    """Abstract interface for Telegram Bot API communication."""

    @abstractmethod
    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Sends a text message with optional inline keyboard. Returns message_id."""
        pass

    @abstractmethod
    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Edits an existing message's text and inline keyboard."""
        pass

    @abstractmethod
    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: Optional[str] = None,
        show_alert: bool = False,
    ) -> bool:
        """Acknowledges an incoming callback query button tap."""
        pass

    @abstractmethod
    async def get_updates(
        self,
        offset: Optional[int] = None,
        limit: int = 100,
        timeout: int = 0,
    ) -> List[Dict[str, Any]]:
        """Polls new incoming updates via getUpdates."""
        pass


class HttpTelegramTransport(TelegramTransport):
    """Production Telegram transport using asynchronous HTTP with token masking and retries."""

    def __init__(self, bot_token: str, base_url: str = "https://api.telegram.org"):
        self._bot_token = bot_token
        self._base_url = f"{base_url.rstrip('/')}/bot{bot_token}"

    async def _post_with_retry(
        self,
        endpoint: str,
        payload: Dict[str, Any],
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        url = f"{self._base_url}/{endpoint}"
        masked_url = mask_bot_token(url)

        async with httpx.AsyncClient(timeout=30.0) as client:
            for attempt in range(1, max_retries + 1):
                try:
                    resp = await client.post(url, json=payload)
                    if resp.status_code == 429:
                        retry_after = int(resp.json().get("parameters", {}).get("retry_after", 2))
                        logger.warning("Telegram rate limit encountered", retry_after=retry_after, attempt=attempt)
                        await asyncio.sleep(retry_after)
                        continue

                    resp.raise_for_status()
                    data = resp.json()
                    if not data.get("ok"):
                        desc = data.get("description", "Unknown Telegram error")
                        raise TelegramTransportError(f"Telegram API error on {endpoint}: {desc}")
                    return data

                except httpx.RequestError as e:
                    logger.warning("Telegram network request failed", url=masked_url, attempt=attempt, error=str(e))
                    if attempt == max_retries:
                        raise TelegramTransportError(f"Telegram network failure: {mask_bot_token(str(e))}")
                    await asyncio.sleep(attempt * 1.5)

        raise TelegramTransportError(f"Max retries exceeded for Telegram endpoint: {endpoint}")

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> int:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        result = await self._post_with_retry("sendMessage", payload)
        message_id = result.get("result", {}).get("message_id")
        if not message_id:
            raise TelegramTransportError("Missing message_id in sendMessage response")
        return int(message_id)

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> bool:
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        await self._post_with_retry("editMessageText", payload)
        return True

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: Optional[str] = None,
        show_alert: bool = False,
    ) -> bool:
        payload = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert,
        }
        if text:
            payload["text"] = text

        await self._post_with_retry("answerCallbackQuery", payload)
        return True

    async def get_updates(
        self,
        offset: Optional[int] = None,
        limit: int = 100,
        timeout: int = 0,
    ) -> List[Dict[str, Any]]:
        payload = {
            "limit": limit,
            "timeout": timeout,
            "allowed_updates": ["callback_query", "message"],
        }
        if offset is not None:
            payload["offset"] = offset

        result = await self._post_with_retry("getUpdates", payload)
        return result.get("result", [])


class MockTelegramTransport(TelegramTransport):
    """In-memory mock transport for testing without network calls or bot tokens."""

    def __init__(self):
        self.sent_messages: List[Dict[str, Any]] = []
        self.edited_messages: List[Dict[str, Any]] = []
        self.answered_callbacks: List[Dict[str, Any]] = []
        self.queued_updates: List[Dict[str, Any]] = []
        self._next_message_id = 1000

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> int:
        msg_id = self._next_message_id
        self._next_message_id += 1
        self.sent_messages.append({
            "message_id": msg_id,
            "chat_id": chat_id,
            "text": text,
            "reply_markup": reply_markup,
        })
        return msg_id

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> bool:
        self.edited_messages.append({
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "reply_markup": reply_markup,
        })
        return True

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: Optional[str] = None,
        show_alert: bool = False,
    ) -> bool:
        self.answered_callbacks.append({
            "callback_query_id": callback_query_id,
            "text": text,
            "show_alert": show_alert,
        })
        return True

    async def get_updates(
        self,
        offset: Optional[int] = None,
        limit: int = 100,
        timeout: int = 0,
    ) -> List[Dict[str, Any]]:
        if not self.queued_updates:
            return []
        updates = self.queued_updates[:limit]
        self.queued_updates = self.queued_updates[limit:]
        return updates
