"""Settings, secrets, provider health, and system status."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from .. import config, models, system
from ..providers import PROVIDERS, build_provider
from ..providers.base import ProviderStatus
from .schemas import (
    CredentialStatusOut,
    ProviderStatusOut,
    SecretIn,
    SettingsIn,
    SettingsOut,
    SystemOut,
    ValidateSecretOut,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["settings"])


def _settings_out(settings: config.Settings) -> SettingsOut:
    payload = settings.model_dump(mode="json")
    all_keys = (*config.KEYED_PROVIDERS, config.HF_TOKEN_KEY, config.GITHUB_PAT_KEY)
    
    from ..security.vault import get_vault
    vault = get_vault()
    cred_status: dict[str, CredentialStatusOut] = {}
    for k in all_keys:
        st = vault.get_secret_status(k)
        has_secret = config.get_secret(k, settings) is not None
        if not st["configured"] and has_secret:
            st = {"configured": True, "masked": "•••••••• Configured", "updated_at": ""}
        cred_status[k] = CredentialStatusOut(**st)

    return SettingsOut(
        active_provider=payload["active_provider"],
        providers=payload["providers"],
        whisper=payload["whisper"],
        clips=payload["clips"],
        ingest=payload["ingest"],
        export=payload["export"],
        insecure_secret_storage=payload["insecure_secret_storage"],
        keys_present={
            name: config.get_secret(name, settings) is not None for name in config.KEYED_PROVIDERS
        }
        | {
            config.HF_TOKEN_KEY: config.get_secret(config.HF_TOKEN_KEY, settings) is not None,
            config.GITHUB_PAT_KEY: config.get_secret(config.GITHUB_PAT_KEY, settings) is not None,
        },
        credentials_status=cred_status,
    )


@router.get("/settings", response_model=SettingsOut)
async def get_settings() -> SettingsOut:
    return _settings_out(config.load())


@router.put("/settings", response_model=SettingsOut)
async def put_settings(payload: SettingsIn) -> SettingsOut:
    """Merge a partial settings update and persist it."""
    settings = config.load()
    updates = payload.model_dump(exclude_none=True)

    if "active_provider" in updates:
        if updates["active_provider"] not in PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown provider. Available: {', '.join(PROVIDERS)}",
            )
        settings.active_provider = updates["active_provider"]

    for section in ("whisper", "clips", "ingest", "export"):
        if section in updates:
            current = getattr(settings, section)
            try:
                setattr(
                    settings,
                    section,
                    current.model_copy(update=updates[section]).model_validate(
                        current.model_dump() | updates[section]
                    ),
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=400, detail=f"Invalid {section} settings: {exc}"
                ) from exc

    if "providers" in updates:
        for name, values in updates["providers"].items():
            provider_settings = settings.provider(name)
            settings.providers[name] = provider_settings.model_copy(update=values)

    if settings.clips.min_duration_s >= settings.clips.max_duration_s:
        raise HTTPException(
            status_code=400, detail="Minimum clip length must be below the maximum."
        )

    if "github_pat" in updates:
        pat_val = updates["github_pat"]
        if pat_val is not None:
            raw = str(pat_val).strip()
            if raw == "__CLEAR__":
                config.delete_secret(config.GITHUB_PAT_KEY)
                log.info("GitHub PAT explicitly cleared via Settings PUT.")
            elif raw and not config.is_masked_secret(raw):
                config.set_secret(config.GITHUB_PAT_KEY, raw)
                log.info("GitHub PAT explicitly updated via Settings PUT.")
            else:
                log.debug("GitHub PAT in Settings PUT is empty or masked; preserving existing stored PAT.")

    config.save(settings)
    return _settings_out(settings)


@router.put("/settings/secrets", status_code=204)
async def put_secret(payload: SecretIn) -> None:
    """Store an API key or token. Values are write-only — never read back."""
    valid = (*config.KEYED_PROVIDERS, config.HF_TOKEN_KEY, config.GITHUB_PAT_KEY)
    if payload.key not in valid:
        raise HTTPException(
            status_code=400, detail=f"Unknown secret. Expected one of: {', '.join(valid)}"
        )
    val = payload.value.strip()
    if not val:
        raise HTTPException(status_code=400, detail="The value cannot be empty.")

    if config.is_masked_secret(val):
        log.info("Ignoring update for secret '%s' because value is a masked placeholder.", payload.key)
        return

    if val == "__CLEAR__":
        config.delete_secret(payload.key)
        log.info("Secret '%s' explicitly deleted via put_secret __CLEAR__ marker.", payload.key)
        return

    config.set_secret(payload.key, val)


@router.delete("/settings/secrets/{key}", status_code=204)
async def delete_secret(key: str) -> None:
    config.delete_secret(key)


@router.post("/settings/secrets/{key}/validate", response_model=ValidateSecretOut)
async def validate_secret(key: str, payload: SecretIn | None = None) -> ValidateSecretOut:
    """Validate a secret against its upstream provider.

    Does NOT expose the secret. Tests connectivity and permissions.
    """
    valid = (*config.KEYED_PROVIDERS, config.HF_TOKEN_KEY, config.GITHUB_PAT_KEY)
    if key not in valid:
        raise HTTPException(status_code=400, detail=f"Unknown secret '{key}'.")

    # If payload provided, test provided value; otherwise test stored value
    token = payload.value.strip() if (payload and payload.value and payload.value.strip()) else config.get_secret(key)
    if not token:
        return ValidateSecretOut(valid=False, message="No token is currently configured.")

    if key == config.GITHUB_PAT_KEY:
        import httpx

        url = "https://api.github.com/user"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "AL-AMR-AutoClip-PAT-Validator/1.0",
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    username = data.get("login", "")
                    scopes_header = resp.headers.get("x-oauth-scopes", "")
                    scopes = [s.strip() for s in scopes_header.split(",") if s.strip()]
                    return ValidateSecretOut(
                        valid=True,
                        message=f"Connected successfully to GitHub as @{username}.",
                        username=username,
                        scopes=scopes,
                    )
                elif resp.status_code == 401:
                    return ValidateSecretOut(
                        valid=False,
                        message="GitHub authentication failed (HTTP 401: Bad credentials). Check that the token has not expired or been revoked.",
                    )
                else:
                    return ValidateSecretOut(
                        valid=False,
                        message=f"GitHub API returned unexpected status HTTP {resp.status_code}.",
                    )
        except Exception as exc:
            return ValidateSecretOut(
                valid=False,
                message=f"Network error connecting to GitHub API: {exc}",
            )

    return ValidateSecretOut(valid=True, message=f"Secret '{key}' is present.")


@router.get("/providers/status", response_model=list[ProviderStatusOut])
async def providers_status() -> list[ProviderStatusOut]:
    """Live health for every provider, checked concurrently."""
    settings = config.load()

    async def check(name: str) -> ProviderStatusOut:
        provider_cls = PROVIDERS[name]
        has_key = (
            config.get_secret(name, settings) is not None if provider_cls.requires_key else True
        )
        try:
            provider = build_provider(name, settings)
            status: ProviderStatus = await provider.health_check()
        except Exception as exc:
            status = ProviderStatus(name=name, available=False, detail=str(exc)[:200])

        return ProviderStatusOut(
            name=name,
            available=status.available,
            detail=status.detail,
            models=status.models,
            requires_key=provider_cls.requires_key,
            has_key=has_key,
        )

    return list(await asyncio.gather(*(check(name) for name in PROVIDERS)))


@router.get("/system", response_model=SystemOut)
async def system_status() -> SystemOut:
    report = await asyncio.to_thread(system.refresh)
    return SystemOut(
        ready=report.ready,
        python_version=report.python_version,
        platform=report.platform,
        ffmpeg_version=report.ffmpeg.version,
        has_libass=report.ffmpeg.has_libass,
        nvenc_works=report.ffmpeg.nvenc_works,
        accel=report.gpu.accel,
        gpu_name=report.gpu.name,
        compute_type=report.gpu.compute_type,
        diarization_available=report.deps.whisperx,
    )


@router.post("/system/models", status_code=204)
async def fetch_models() -> None:
    """Download any missing ML model bundles."""
    for key in models.MODELS:
        if not models.is_available(key):
            try:
                await asyncio.to_thread(models.ensure, key)
            except models.ModelDownloadError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
