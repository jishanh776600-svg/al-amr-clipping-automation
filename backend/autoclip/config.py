"""Settings persistence and secret storage.

Non-secret settings live in ``<root>/config.json`` as plain JSON so users can
hand-edit them. Secrets (LLM API keys, the HuggingFace token) are kept out of
that file and stored in the OS keyring — Credential Manager on Windows, Keychain
on macOS, Secret Service on Linux.

If no keyring backend is available (headless Linux, some Docker images), we fall
back to writing secrets into ``config.json`` with restrictive permissions and
set :attr:`Settings.insecure_secret_storage` so the UI and ``autoclip doctor``
can warn about it. Silently downgrading security without telling the user is the
thing we're trying to avoid.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import stat
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr

from . import paths

log = logging.getLogger(__name__)

KEYRING_SERVICE = "autoclip"

#: Keyring service used before the rename. Read from as a fallback so keys
#: stored under the old name aren't silently lost; writes always use the new one.
LEGACY_KEYRING_SERVICE = "clipforge"

ProviderName = Literal["autonomous", "anthropic", "openai", "gemini", "ollama"]

#: Providers that authenticate with an API key. Ollama and Autonomous need none.
KEYED_PROVIDERS: tuple[str, ...] = ("anthropic", "openai", "gemini")

#: Extra secrets that aren't tied to a provider.
HF_TOKEN_KEY = "huggingface_token"
GITHUB_PAT_KEY = "github_pat"


def canonical_secret_key(key: str) -> str:
    """Normalize secret keys so case differences or naming aliases resolve to a single canonical key."""
    if not key:
        return ""
    k = key.strip().lower()
    if k in ("github_pat", "github_token", "gh_token", "github-pat", "pat", "github"):
        return GITHUB_PAT_KEY
    if k in ("hf_token", "huggingface_token", "huggingface", "hf"):
        return HF_TOKEN_KEY
    return k


class ProviderSettings(BaseModel):
    """Per-provider configuration. The API key itself is never stored here."""

    model: str = ""
    #: Only meaningful for the OpenAI-compatible provider. Pointing this at
    #: OpenRouter, Groq, DeepSeek, or a local LM Studio server is the supported
    #: way to use any other vendor.
    base_url: str | None = None


class WhisperSettings(BaseModel):
    #: tiny | base | small | medium | large-v3 — or a local path.
    model: str = "small"
    #: Force a CTranslate2 compute type. Empty means "let hardware detection pick".
    compute_type: str = ""
    #: ISO 639-1 code, or empty for auto-detection.
    language: str = ""
    #: Run WhisperX diarization to label words by speaker. Needs the
    #: ``diarization`` extra and a HuggingFace token.
    diarization: bool = False


class ClipSettings(BaseModel):
    min_duration_s: float = 20.0
    max_duration_s: float = 30.0
    max_clips: int = 10


class IngestSettings(BaseModel):
    #: yt-dlp format selector. Caps at 1080p and ensures audio+video streams merge cleanly.
    ytdlp_format: str = (
        "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
        "bestvideo[height<=1080]+bestaudio/"
        "bestvideo+bestaudio/"
        "best[height<=1080][ext=mp4]/"
        "best[height<=1080]/"
        "best"
    )
    #: Optional browser to pull cookies from (deprecated; cloud workers run browserless).
    cookies_from_browser: str = ""
    #: Optional path to Netscape-format cookies.txt file for authenticated server sessions.
    cookies_file: str = ""
    #: Offer YouTube's own auto-captions as a fast path, skipping Whisper.
    prefer_youtube_captions: bool = False
    #: Optional egress proxy URL (e.g. socks5://127.0.0.1:1080 for WARP sidecar).
    proxy: str = ""


class ExportSettings(BaseModel):
    ratio: Literal["9:16", "1:1", "16:9"] = "9:16"
    caption_style: str = "classic_professional"
    visual_filter: str = "original"
    bgm_asset_id: str = ""
    #: Integrated loudness target in LUFS. -14 is the de-facto platform standard.
    loudness_lufs: float = -14.0
    #: Use h264_nvenc when the hardware supports it. Falls back to libx264.
    prefer_hardware_encoder: bool = True
    crf: int = 18
    #: Also write a .srt sidecar next to each exported clip.
    write_srt: bool = False


class Settings(BaseModel):
    """Top-level persisted settings."""

    active_provider: ProviderName = "anthropic"
    providers: dict[str, ProviderSettings] = Field(
        default_factory=lambda: {
            "autonomous": ProviderSettings(model="al-amr-autonomous-v1"),
            "anthropic": ProviderSettings(model="claude-sonnet-5"),
            "openai": ProviderSettings(model="gpt-4o"),
            "gemini": ProviderSettings(model="gemini-2.0-flash"),
            "ollama": ProviderSettings(model="", base_url="http://localhost:11434"),
        }
    )
    whisper: WhisperSettings = Field(default_factory=WhisperSettings)
    clips: ClipSettings = Field(default_factory=ClipSettings)
    ingest: IngestSettings = Field(default_factory=IngestSettings)
    export: ExportSettings = Field(default_factory=ExportSettings)

    #: Set when secrets had to be written to config.json because no keyring
    #: backend was usable. Surfaced as a warning in the UI and in `doctor`.
    insecure_secret_storage: bool = False
    #: Plaintext secret fallback. Empty whenever the keyring is working.
    #: A private attribute so it never round-trips through the public schema —
    #: `save()` writes it back explicitly.
    _fallback_secrets: dict[str, str] = PrivateAttr(default_factory=dict)

    model_config = {"extra": "ignore"}

    def provider(self, name: str | None = None) -> ProviderSettings:
        """Return settings for ``name``, defaulting to the active provider."""
        key = name or self.active_provider
        ps = self.providers.setdefault(key, ProviderSettings())
        env_base_url = os.environ.get(f"AUTOCLIP_{key.upper()}_BASE_URL")
        env_model = os.environ.get(f"AUTOCLIP_{key.upper()}_MODEL")
        if env_base_url or env_model:
            return ProviderSettings(
                model=env_model if env_model is not None else ps.model,
                base_url=env_base_url if env_base_url is not None else ps.base_url,
            )
        return ps


def _apply_env_overrides(settings: Settings) -> Settings:
    if "AUTOCLIP_ACTIVE_PROVIDER" in os.environ:
        settings.active_provider = os.environ["AUTOCLIP_ACTIVE_PROVIDER"]
    if "AUTOCLIP_COOKIES_FILE" in os.environ:
        settings.ingest.cookies_file = os.environ["AUTOCLIP_COOKIES_FILE"]
    return settings


def load() -> Settings:
    """Read settings from disk, falling back to defaults on a missing or bad file."""
    path = paths.config_path()
    if not path.exists():
        return _apply_env_overrides(Settings())
    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read %s (%s); falling back to defaults.", path, exc)
        return _apply_env_overrides(Settings())

    fallback = raw.pop("_fallback_secrets", {}) or {}
    settings = Settings.model_validate(raw)
    settings._fallback_secrets = fallback
    return _apply_env_overrides(settings)


def save(settings: Settings) -> None:
    """Write settings to disk atomically, with owner-only permissions."""
    paths.ensure_layout()
    path = paths.config_path()

    payload = settings.model_dump(mode="json")
    if settings._fallback_secrets:
        payload["_fallback_secrets"] = settings._fallback_secrets

    # Write to a sibling temp file then replace, so an interrupted write can
    # never leave a truncated config behind.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _restrict_permissions(tmp)
    os.replace(tmp, path)


def _restrict_permissions(path: Path) -> None:
    """Best-effort chmod 600. A no-op where the platform doesn't support it."""
    with contextlib.suppress(OSError):  # platform dependent
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


# --------------------------------------------------------------------------
# Secrets
# --------------------------------------------------------------------------


def _keyring():
    """Return a usable keyring module, or None if no backend works.

    Importing ``keyring`` succeeds even with no backend; the failure only shows
    up on first use, so we probe explicitly.
    """
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring
    except ImportError:  # pragma: no cover - keyring is a hard dependency
        return None

    try:
        if isinstance(keyring.get_keyring(), FailKeyring):
            return None
    except Exception:  # pragma: no cover - backend probing can raise anything
        return None
    return keyring


def get_secret(key: str, settings: Settings | None = None) -> str | None:
    """Return a stored secret, or None if unset.

    Canonical source of truth hierarchy:
    1. Durable encrypted SQLite vault (canonical primary source of truth).
    2. Environment variable overrides / bootstrap (AUTOCLIP_<KEY>_KEY, or GITHUB_PAT/GH_TOKEN for github_pat).
       If an environment variable is found, it is automatically anchored into the vault.
    3. OS keyring (with automatic migration to SQLite vault).
    4. Plaintext fallback secrets (with automatic migration to SQLite vault).
    """
    canon = canonical_secret_key(key)
    if not canon:
        return None

    # 1. Environment variable overrides / bootstrap
    env_override = os.environ.get(f"AUTOCLIP_{canon.upper()}_KEY")
    if env_override and env_override.strip() and not is_masked_secret(env_override):
        clean_override = env_override.strip()
        try:
            from .security.vault import get_vault
            get_vault().store_secret(canon, clean_override)
        except Exception:
            pass
        return clean_override

    if canon == GITHUB_PAT_KEY:
        token = (
            os.environ.get("GITHUB_PAT")
            or os.environ.get("GH_TOKEN")
            or os.environ.get("GITHUB_TOKEN")
        )
        if token and token.strip() and not is_masked_secret(token):
            clean = token.strip()
            try:
                from .security.vault import get_vault
                get_vault().store_secret(GITHUB_PAT_KEY, clean)
            except Exception:
                pass
            return clean

    # 2. Durable encrypted SQLite vault (canonical source of truth when no env var)
    try:
        from .security.vault import get_vault

        vault_val = get_vault().retrieve_secret(canon)
        if vault_val and vault_val.strip() and not is_masked_secret(vault_val):
            return vault_val.strip()
    except Exception as exc:
        log.debug("Credential vault lookup for %s: %s", canon, exc)

    # 3. OS keyring (with migration path)
    kr = _keyring()
    keyring_val: str | None = None
    if kr is not None:
        for service in (KEYRING_SERVICE, LEGACY_KEYRING_SERVICE):
            for test_k in (canon, key):
                try:
                    value = kr.get_password(service, test_k)
                    if value and value.strip() and not is_masked_secret(value):
                        keyring_val = value.strip()
                        break
                except Exception as exc:  # pragma: no cover - backend dependent
                    log.debug("Keyring read failed for %s: %s", test_k, exc)
            if keyring_val:
                break

    if keyring_val:
        try:
            from .security.vault import get_vault

            get_vault().migrate_from_keyring(canon, keyring_val)
        except Exception as exc:
            log.warning("Failed to migrate %s from keyring to durable vault: %s", canon, exc)
        return keyring_val

    # 4. Fallback secrets migration path
    settings = settings if settings is not None else load()
    fallback_val = settings._fallback_secrets.get(canon) or settings._fallback_secrets.get(key)
    if fallback_val and fallback_val.strip() and not is_masked_secret(fallback_val):
        clean_fb = fallback_val.strip()
        try:
            from .security.vault import get_vault

            get_vault().store_secret(canon, clean_fb)
            settings._fallback_secrets.pop(canon, None)
            settings._fallback_secrets.pop(key, None)
            settings.insecure_secret_storage = bool(settings._fallback_secrets)
            save(settings)
        except Exception as exc:
            log.warning("Failed to migrate %s from fallback secrets to durable vault: %s", canon, exc)
        return clean_fb

    return None


def is_masked_secret(val: str | None) -> bool:
    """True if string represents a masked fingerprint, placeholder, or invalid secret token."""
    if not val:
        return False
    s = val.strip()
    if not s:
        return False
    lower = s.lower()
    # Common literal representations of empty / null / placeholder values
    if lower in (
        "null",
        "none",
        "undefined",
        "nil",
        "empty",
        "unset",
        "masked",
        "(masked)",
        "[masked]",
        "[redacted]",
        "redacted",
        "placeholder",
        "not configured",
        "configured",
        "re-enter token",
        "encryption key mismatch",
    ):
        return True

    # Any string containing bullet characters or mask markers
    if "••••" in s or "•" in s or "Configured" in s or s.startswith("***") or "(…" in s:
        return True

    # Any string composed entirely of masking / placeholder characters (bullets, asterisks, dots, dashes, spaces)
    if set(s).issubset({"•", "*", ".", "-", "_", " "}):
        return True

    # Fingerprint format: "•••••••• Configured (…XXXX)"
    if s.startswith("••••") and (s.endswith(")") or "Configured" in s):
        return True

    return False


def set_secret(key: str, value: str | None, settings: Settings | None = None) -> bool:
    """Store a secret encrypted in the durable database vault and synced with keyring.

    Returns True if stored in durable encrypted storage or keyring.
    NEVER overwrites an existing secret with an empty, null, undefined, or masked value.
    """
    canon = canonical_secret_key(key)
    if not canon:
        return False

    if value is None:
        log.warning("Rejected attempt to set None secret for '%s'. Existing secret preserved.", canon)
        return False

    token = str(value).strip()
    if not token or is_masked_secret(token):
        log.warning(
            "Rejected attempt to overwrite secret '%s' with empty, null, undefined, or masked placeholder (%s). Existing secret preserved.",
            canon,
            token[:12] if token else "empty",
        )
        return False

    from .security.vault import get_vault

    vault = get_vault()
    vault.store_secret(canon, token)

    # Secondary sync to OS keyring for local desktop convenience if available
    kr = _keyring()
    if kr is not None:
        for test_k in (canon, key):
            try:
                kr.set_password(KEYRING_SERVICE, test_k, token)
            except Exception as exc:  # pragma: no cover - backend dependent
                log.debug("Keyring write failed for %s (durable vault is active): %s", test_k, exc)

    # If it was previously stored in plaintext fallback secrets, purge it
    settings = settings if settings is not None else load()
    purged = False
    for test_k in (canon, key):
        if settings._fallback_secrets.pop(test_k, None) is not None:
            purged = True
    if purged:
        settings.insecure_secret_storage = bool(settings._fallback_secrets)
        save(settings)

    return True


def delete_secret(key: str, settings: Settings | None = None) -> None:
    """Remove a secret from durable database vault, keyring, and fallback storage."""
    canon = canonical_secret_key(key)
    if not canon:
        return

    from .security.vault import get_vault

    with contextlib.suppress(Exception):
        get_vault().delete_secret(canon)

    kr = _keyring()
    if kr is not None:
        for service in (KEYRING_SERVICE, LEGACY_KEYRING_SERVICE):
            for test_k in (canon, key):
                with contextlib.suppress(Exception):
                    kr.delete_password(service, test_k)

    settings = settings if settings is not None else load()
    purged = False
    for test_k in (canon, key):
        if settings._fallback_secrets.pop(test_k, None) is not None:
            purged = True
    if purged:
        settings.insecure_secret_storage = bool(settings._fallback_secrets)
        save(settings)

    kr = _keyring()
    if kr is not None:
        # Backends raise when the entry is absent; deleting a missing secret is
        # not an error from the caller's point of view. Both services are cleared
        # so a delete can't leave a pre-rename copy behind to resurface.
        for service in (KEYRING_SERVICE, LEGACY_KEYRING_SERVICE):
            with contextlib.suppress(Exception):
                kr.delete_password(service, key)

    settings = settings if settings is not None else load()
    if settings._fallback_secrets.pop(key, None) is not None:
        settings.insecure_secret_storage = bool(settings._fallback_secrets)
        save(settings)

