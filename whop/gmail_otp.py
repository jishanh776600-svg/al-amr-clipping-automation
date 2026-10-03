"""Whop Autonomous Gmail OTP Retrieval Engine.

Connects securely via IMAP SSL to Gmail to autonomously fetch Whop 6-digit login verification codes.
Zero human intervention: allows the pipeline to auto-login and refresh expired sessions indefinitely.
"""

from __future__ import annotations

import email
import imaplib
import logging
import os
import re
import time
from email.header import decode_header
from typing import Optional

from .config import sanitize_text

log = logging.getLogger(__name__)


class GmailOTPError(Exception):
    """Raised when OTP retrieval from Gmail fails or times out."""
    pass


class GmailOTPReader:
    """Fetches Whop authentication codes directly from Gmail via IMAP SSL."""

    def __init__(
        self,
        email_address: Optional[str] = None,
        app_password: Optional[str] = None,
        imap_server: str = "imap.gmail.com",
        imap_port: int = 993,
    ):
        self.email_address = (email_address or os.getenv("WHOP_EMAIL", "")).strip()
        self.app_password = (
            app_password
            or os.getenv("GMAIL_APP_PASSWORD", "")
            or os.getenv("WHOP_GMAIL_APP_PASSWORD", "")
        ).strip().replace(" ", "")
        self.imap_server = imap_server
        self.imap_port = imap_port

    def is_configured(self) -> bool:
        """Returns True if email and app password credentials are configured."""
        return bool(self.email_address and self.app_password)

    def fetch_latest_whop_otp(
        self,
        timeout_seconds: float = 60.0,
        poll_interval_seconds: float = 2.0,
        min_timestamp_epoch: Optional[float] = None,
    ) -> str:
        """Polls Gmail inbox via IMAP SSL to retrieve the newest 6-digit Whop OTP code.
        
        Args:
            timeout_seconds: Maximum time to wait for the verification email.
            poll_interval_seconds: Delay between inbox checks.
            min_timestamp_epoch: Only accept emails received after this timestamp.
        """
        if not self.is_configured():
            raise GmailOTPError(
                "Gmail OTP reader is not configured: GMAIL_APP_PASSWORD or WHOP_EMAIL is missing. "
                "Generate a 16-letter App Password at https://myaccount.google.com/apppasswords"
            )

        cutoff_time = min_timestamp_epoch or (time.time() - 120.0)
        start_wait = time.time()
        log.info(
            "Polling Gmail (%s) for Whop verification code (timeout: %.0fs)...",
            sanitize_text(self.email_address),
            timeout_seconds,
        )

        while time.time() - start_wait < timeout_seconds:
            try:
                code = self._check_inbox_for_otp(cutoff_time)
                if code:
                    log.info("Successfully retrieved Whop OTP verification code from Gmail!")
                    return code
            except Exception as e:
                log.debug("IMAP poll attempt notice: %s", sanitize_text(str(e)))

            time.sleep(poll_interval_seconds)

        raise GmailOTPError(
            f"Timed out waiting {timeout_seconds:.0f}s for Whop verification email in {sanitize_text(self.email_address)}."
        )

    def _check_inbox_for_otp(self, cutoff_time: float) -> Optional[str]:
        """Connects to IMAP, scans recent messages, and extracts the 6-digit code."""
        mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
        try:
            mail.login(self.email_address, self.app_password)
            mail.select("INBOX")

            # Search recent emails
            status, messages = mail.search(None, '(OR FROM "whop.com" SUBJECT "Whop")')
            if status != "OK" or not messages or not messages[0]:
                # Fallback: search ALL recent messages
                status, messages = mail.search(None, "ALL")

            if status != "OK" or not messages or not messages[0]:
                return None

            msg_ids = messages[0].split()
            # Inspect the latest 5 messages
            for msg_id in reversed(msg_ids[-5:]):
                _, msg_data = mail.fetch(msg_id, "(RFC822)")
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        sender = msg.get("From", "").lower()
                        subject = msg.get("Subject", "").lower()

                        # Check if from Whop or subject contains Whop / code / verify
                        is_relevant = (
                            "whop" in sender
                            or "whop" in subject
                            or "verification code" in subject
                            or "verify" in subject
                        )
                        if not is_relevant:
                            continue

                        # Extract body text
                        body = self._extract_body(msg)
                        code = self._parse_otp_from_text(body)
                        if code:
                            return code

            return None
        finally:
            try:
                mail.close()
            except Exception:
                pass
            try:
                mail.logout()
            except Exception:
                pass

    @staticmethod
    def _extract_body(msg: email.message.Message) -> str:
        """Extracts plain text content from email message parts."""
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                if content_type in ("text/plain", "text/html") and "attachment" not in content_disposition:
                    payload = part.get_payload(decode=True)
                    if payload:
                        body += payload.decode("utf-8", errors="replace") + "\n"
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                body = payload.decode("utf-8", errors="replace")
        return body

    @staticmethod
    def _parse_otp_from_text(text: str) -> Optional[str]:
        """Extracts the 6-digit numeric verification code using contextual regex."""
        if not text:
            return None

        # Pattern 1: Explicit labels like "verification code: 123456" or "code is 123456"
        m1 = re.search(r'(?:verification\s*code|code\s*is|your\s*code|enter\s*code)[:\s*]+([0-9]{6})\b', text, re.IGNORECASE)
        if m1:
            return m1.group(1)

        # Pattern 2: Standalone 6-digit number enclosed in bold/headings/spans
        m2 = re.search(r'>\s*([0-9]{6})\s*<', text)
        if m2:
            return m2.group(1)

        # Pattern 3: Any standalone 6-digit number
        m3 = re.findall(r'\b([0-9]{6})\b', text)
        if m3:
            return m3[0]

        return None
