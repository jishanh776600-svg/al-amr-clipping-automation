"""Whop Configuration and Environment Handling.

Handles secure environment configuration, dry-run safety enforcement,
and secret redaction for the Whop cloud-browser foundation (Step 1).
"""

import os
import re
import urllib.parse
from dataclasses import dataclass
from typing import Optional


class WhopConfigError(Exception):
    """Raised when Whop configuration is invalid or missing required secrets."""
    pass


class WhopDryRunViolationError(Exception):
    """Raised when an action attempts state mutation while in read-only DRY_RUN mode."""
    pass


# Central registry of mutation actions blocked under WHOP_DRY_RUN=true
FORBIDDEN_MUTATION_ACTIONS = frozenset([
    "join",
    "apply",
    "accept",
    "claim",
    "submit",
    "send",
    "message",
    "upload",
    "download_asset",
    "modify",
    "delete",
])


def sanitize_text(text: str, secret_to_redact: Optional[str] = None) -> str:
    """Redacts sensitive strings and sanitizes authentication parameters from text/URLs."""
    if not text:
        return ""
    sanitized = str(text)

    # Redact explicit secret if provided and long enough to be unique
    if secret_to_redact and len(secret_to_redact) >= 4:
        sanitized = sanitized.replace(secret_to_redact, "[REDACTED_SECRET]")

    # Redact environment tokens
    for env_var in (
        "WHOP_COOKIES",
        "AUTOCLIP_API_KEY",
        "OPERATOR_TOKEN",
        "AL_AMR_MASTER_KEY",
        "WORKER_CALLBACK_SECRET",
        "GITHUB_PAT",
    ):
        env_secret = os.getenv(env_var, "").strip()
        if env_secret and len(env_secret) >= 4:
            sanitized = sanitized.replace(env_secret, f"[REDACTED_{env_var}]")

    # Redact default operator token if present
    for tok in ("al amr jish2#ji", "al amar jish2#ji"):
        sanitized = sanitized.replace(tok, "[REDACTED_TOKEN]")

    # Redact Authorization header values / Bearer tokens
    sanitized = re.sub(r'(Bearer\s+)[A-Za-z0-9_\-\.\#\s]{4,}', r'\1[REDACTED]', sanitized, flags=re.IGNORECASE)

    # Sanitize URLs with sensitive query parameters (e.g. token, session, auth, key)
    def _sanitize_url_match(match: re.Match) -> str:
        raw_url = match.group(0)
        try:
            parsed = urllib.parse.urlparse(raw_url)
            if parsed.query:
                query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                clean_params = []
                for k, v in query_params:
                    if any(s in k.lower() for s in ("token", "auth", "secret", "key", "cookie", "session")):
                        clean_params.append((k, "[REDACTED]"))
                    else:
                        clean_params.append((k, v))
                clean_query = urllib.parse.urlencode(clean_params)
                return urllib.parse.urlunparse(parsed._replace(query=clean_query))
        except Exception:
            pass
        return raw_url

    sanitized = re.sub(r'https?://[^\s"\'>]+', _sanitize_url_match, sanitized)
    return sanitized


@dataclass(frozen=True)
class WhopConfig:
    dry_run: bool = True
    headless: bool = True
    base_url: str = "https://whop.com"
    navigation_timeout_ms: int = 30000
    stabilize_wait_ms: int = 3000

    @classmethod
    def from_env(cls) -> "WhopConfig":
        """Loads configuration from environment variables with safe defaults."""
        # DRY_RUN is strictly enforced to True by default in Step 1
        dry_run_str = os.getenv("WHOP_DRY_RUN", "true").strip().lower()
        dry_run = dry_run_str not in ("false", "0", "no", "off")

        headless_str = os.getenv("WHOP_HEADLESS", "true").strip().lower()
        headless = headless_str not in ("false", "0", "no", "off")

        base_url = os.getenv("WHOP_BASE_URL", "https://whop.com").strip() or "https://whop.com"

        try:
            nav_timeout = int(os.getenv("WHOP_NAVIGATION_TIMEOUT_MS", "30000"))
        except ValueError:
            nav_timeout = 30000

        try:
            stabilize_wait = int(os.getenv("WHOP_STABILIZE_WAIT_MS", "3000"))
        except ValueError:
            stabilize_wait = 3000

        return cls(
            dry_run=dry_run,
            headless=headless,
            base_url=base_url,
            navigation_timeout_ms=nav_timeout,
            stabilize_wait_ms=stabilize_wait,
        )

    def get_cookies_secret(self) -> str:
        """Retrieves the WHOP_COOKIES secret, failing clearly if missing."""
        raw_secret = os.getenv("WHOP_COOKIES", "").strip()
        if not raw_secret:
            raise WhopConfigError(
                "WHOP_COOKIES secret is missing. Please configure WHOP_COOKIES in GitHub Secrets "
                "or environment variables."
            )
        return raw_secret

    def assert_mutation_permitted(self, action_name: str) -> None:
        """Safety guard enforcing read-only behavior in Step 1."""
        if self.dry_run:
            raise WhopDryRunViolationError(
                f"Action '{action_name}' is blocked by Step 1 safety guard. "
                f"WHOP_DRY_RUN is enabled (read-only mode). No campaign mutation allowed."
            )


@dataclass(frozen=True)
class AutoClipConfig:
    base_url: str = "https://al-amr-clipping-automation.onrender.com"
    api_token: str = "al amr jish2#ji"
    timeout_s: float = 15.0
    max_retries: int = 3
    retry_backoff_factor: float = 0.5
    dry_run: bool = True

    @classmethod
    def from_env(cls) -> "AutoClipConfig":
        """Loads configuration from environment variables with safe defaults."""
        base_url = (
            os.getenv("CONTROL_PLANE_URL")
            or os.getenv("AUTOCLIP_BASE_URL")
            or os.getenv("AUTOCLIP_API_URL")
            or os.getenv("RENDER_EXTERNAL_URL")
            or "https://al-amr-clipping-automation.onrender.com"
        ).strip().rstrip("/")

        token = (
            os.getenv("AUTOCLIP_API_KEY")
            or os.getenv("OPERATOR_TOKEN")
            or os.getenv("AL_AMR_MASTER_KEY")
            or os.getenv("WORKER_CALLBACK_SECRET")
            or "al amr jish2#ji"
        ).strip()

        # Check AUTOCLIP_DRY_RUN first so browser protection (WHOP_DRY_RUN=true) does NOT prevent real AutoClip jobs
        autoclip_dry_str = os.getenv("AUTOCLIP_DRY_RUN")
        if autoclip_dry_str is not None:
            dry_run = autoclip_dry_str.strip().lower() not in ("false", "0", "no", "off")
        else:
            dry_run_str = os.getenv("WHOP_DRY_RUN", "true").strip().lower()
            dry_run = dry_run_str not in ("false", "0", "no", "off")

        timeout_str = os.getenv("AUTOCLIP_TIMEOUT_S", "15.0").strip()
        try:
            timeout_s = float(timeout_str)
        except ValueError:
            timeout_s = 15.0

        return cls(
            base_url=base_url,
            api_token=token,
            timeout_s=timeout_s,
            dry_run=dry_run,
        )
