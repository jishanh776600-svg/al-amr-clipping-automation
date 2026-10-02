"""Whop Session Management.

Validates and loads authenticated Playwright session state (cookies and localStorage)
while guaranteeing zero credential exposure in logs or diagnostics.
"""

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from playwright.sync_api import BrowserContext


class WhopSessionError(Exception):
    """Raised when Whop session data or cookies are malformed or invalid."""
    pass


@dataclass(frozen=True)
class ValidatedSessionState:
    """Safe representation of validated session state without exposing raw values."""
    cookie_count: int
    origins_count: int
    domains: List[str]
    raw_storage_dict: Dict[str, Any]


def parse_and_validate_session_state(raw_secret: str) -> ValidatedSessionState:
    """Parses raw JSON string containing cookies or Playwright storage state.

    Ensures credentials are never printed or leaked in error messages.
    """
    if not raw_secret or not raw_secret.strip():
        raise WhopSessionError("Session state is empty.")

    try:
        data = json.loads(raw_secret.strip())
    except Exception:
        raise WhopSessionError(
            "WHOP_COOKIES is malformed JSON. Ensure valid JSON string or Playwright storage state."
        )

    cookies_list: List[Dict[str, Any]] = []
    origins_list: List[Dict[str, Any]] = []

    if isinstance(data, list):
        # Format A: Plain list of cookie dictionaries
        cookies_list = data
    elif isinstance(data, dict):
        # Format B: Full Playwright storage state {"cookies": [...], "origins": [...]}
        if "cookies" in data and isinstance(data["cookies"], list):
            cookies_list = data["cookies"]
        else:
            raise WhopSessionError(
                "Invalid storage state object: missing 'cookies' array."
            )
        if "origins" in data and isinstance(data["origins"], list):
            origins_list = data["origins"]
    else:
        raise WhopSessionError(
            "WHOP_COOKIES root structure must be a JSON array of cookies or storage state object."
        )

    if not cookies_list:
        raise WhopSessionError("WHOP_COOKIES contains zero cookies.")

    # Validate and normalize cookies
    validated_cookies: List[Dict[str, Any]] = []
    domains_seen = set()

    for idx, c in enumerate(cookies_list):
        if not isinstance(c, dict):
            raise WhopSessionError(f"Cookie entry at index {idx} is not a valid JSON object.")
        name = c.get("name")
        value = c.get("value")
        domain = c.get("domain") or ".whop.com"
        path = c.get("path") or "/"

        if not name or value is None:
            raise WhopSessionError(
                f"Cookie entry at index {idx} is missing required 'name' or 'value' field."
            )

        cookie_record: Dict[str, Any] = {
            "name": str(name),
            "value": str(value),
            "domain": str(domain),
            "path": str(path),
        }
        if "secure" in c:
            cookie_record["secure"] = bool(c["secure"])
        if "httpOnly" in c:
            cookie_record["httpOnly"] = bool(c["httpOnly"])
        if "sameSite" in c and c["sameSite"] in ("Strict", "Lax", "None"):
            cookie_record["sameSite"] = c["sameSite"]

        validated_cookies.append(cookie_record)
        domains_seen.add(str(domain))

    # Assemble canonical Playwright storage state dictionary
    canonical_storage: Dict[str, Any] = {
        "cookies": validated_cookies,
        "origins": origins_list,
    }

    return ValidatedSessionState(
        cookie_count=len(validated_cookies),
        origins_count=len(origins_list),
        domains=sorted(list(domains_seen)),
        raw_storage_dict=canonical_storage,
    )


def apply_session_to_context(context: BrowserContext, session_state: ValidatedSessionState) -> None:
    """Attaches validated cookies to the browser context."""
    if session_state.raw_storage_dict.get("cookies"):
        context.add_cookies(session_state.raw_storage_dict["cookies"])
