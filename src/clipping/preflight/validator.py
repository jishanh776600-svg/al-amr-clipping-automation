"""AL AMR CLIPPING Production Preflight Validation Engine.

Performs deterministic verification of runtime dependencies, system binaries,
storage connectivity, encryption vault integrity, safety control states,
and platform integration configurations before autonomous pipeline activation.
"""

import importlib
import os
import shutil
import sys
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from clipping.config.settings import Settings, get_settings
from clipping.control.repository import ControlRepository
from clipping.logging.logger import get_logger
from clipping.storage.base import StorageDriver
from clipping.storage.factory import StorageFactory

logger = get_logger("clipping.preflight.validator")


class PreflightStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class OverallPreflightStatus(str, Enum):
    READY = "READY"
    READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
    NOT_READY = "NOT_READY"


class PreflightCategory(str, Enum):
    RUNTIME = "RUNTIME"
    SYSTEM_BINARY = "SYSTEM_BINARY"
    STORAGE = "STORAGE"
    VAULT = "VAULT"
    CONTROL_STATE = "CONTROL_STATE"
    PLATFORM_INTEGRATION = "PLATFORM_INTEGRATION"


class PreflightCheck(BaseModel):
    name: str
    category: PreflightCategory
    is_mandatory: bool
    status: PreflightStatus
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)


class PreflightReport(BaseModel):
    status: OverallPreflightStatus
    ready: bool
    timestamp: str
    checks: List[PreflightCheck]
    summary: str


class SystemPreflightValidator:
    """
    Validates complete operational environment readiness without leaking credentials.
    Categorizes results into mandatory prerequisites vs optional features.
    """

    def __init__(
        self,
        storage_driver: Optional[StorageDriver] = None,
        control_repository: Optional[ControlRepository] = None,
        settings: Optional[Settings] = None,
    ):
        self.settings = settings or get_settings()
        self.storage = storage_driver or StorageFactory.create()
        self.control_repo = control_repository or ControlRepository(self.storage)

    def check_runtime(self) -> List[PreflightCheck]:
        """Validates Python runtime and core libraries."""
        checks = []

        # Python version check
        py_ver = sys.version_info
        py_ver_str = f"{py_ver.major}.{py_ver.minor}.{py_ver.micro}"
        if py_ver >= (3, 10):
            checks.append(
                PreflightCheck(
                    name="python_version",
                    category=PreflightCategory.RUNTIME,
                    is_mandatory=True,
                    status=PreflightStatus.PASS,
                    message=f"Python version {py_ver_str} satisfies minimum requirement (>=3.10)",
                    details={"version": py_ver_str},
                )
            )
        else:
            checks.append(
                PreflightCheck(
                    name="python_version",
                    category=PreflightCategory.RUNTIME,
                    is_mandatory=True,
                    status=PreflightStatus.FAIL,
                    message=f"Python version {py_ver_str} is below required >=3.10",
                    details={"version": py_ver_str},
                )
            )

        # Core required libraries
        required_libs = ["cv2", "numpy", "httpx", "pydantic", "cryptography"]
        missing_libs = []
        for lib in required_libs:
            try:
                importlib.import_module(lib)
            except ImportError:
                missing_libs.append(lib)

        if not missing_libs:
            checks.append(
                PreflightCheck(
                    name="core_python_libraries",
                    category=PreflightCategory.RUNTIME,
                    is_mandatory=True,
                    status=PreflightStatus.PASS,
                    message="All core Python libraries available",
                    details={"libraries": required_libs},
                )
            )
        else:
            checks.append(
                PreflightCheck(
                    name="core_python_libraries",
                    category=PreflightCategory.RUNTIME,
                    is_mandatory=True,
                    status=PreflightStatus.FAIL,
                    message=f"Missing required Python libraries: {', '.join(missing_libs)}",
                    details={"missing": missing_libs},
                )
            )

        return checks

    def check_binaries(self) -> List[PreflightCheck]:
        """Validates external binaries required for media pipeline processing."""
        checks = []

        # FFmpeg check
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path:
            checks.append(
                PreflightCheck(
                    name="ffmpeg_binary",
                    category=PreflightCategory.SYSTEM_BINARY,
                    is_mandatory=True,
                    status=PreflightStatus.PASS,
                    message="FFmpeg executable discovered in system PATH",
                    details={"path": ffmpeg_path},
                )
            )
        else:
            checks.append(
                PreflightCheck(
                    name="ffmpeg_binary",
                    category=PreflightCategory.SYSTEM_BINARY,
                    is_mandatory=True,
                    status=PreflightStatus.FAIL,
                    message="FFmpeg not found in PATH; required for video clipping and rendering",
                    details={},
                )
            )

        # FFprobe check
        ffprobe_path = shutil.which("ffprobe")
        if ffprobe_path:
            checks.append(
                PreflightCheck(
                    name="ffprobe_binary",
                    category=PreflightCategory.SYSTEM_BINARY,
                    is_mandatory=True,
                    status=PreflightStatus.PASS,
                    message="FFprobe executable discovered in system PATH",
                    details={"path": ffprobe_path},
                )
            )
        else:
            checks.append(
                PreflightCheck(
                    name="ffprobe_binary",
                    category=PreflightCategory.SYSTEM_BINARY,
                    is_mandatory=True,
                    status=PreflightStatus.FAIL,
                    message="FFprobe not found in PATH; required for video metadata probing",
                    details={},
                )
            )

        return checks

    async def check_storage(self) -> List[PreflightCheck]:
        """Validates storage driver connectivity with write-read-delete probe."""
        driver_name = type(self.storage).__name__
        probe_key = "system/.preflight_probe.tmp"
        test_data = b'{"preflight_probe": true}'

        try:
            # Probe write
            await self.storage.upload_bytes(test_data, probe_key, content_type="application/json")
            # Probe verify exists
            exists = await self.storage.exists(probe_key)
            if not exists:
                raise RuntimeError("Uploaded preflight probe file reported non-existent")
            # Probe read
            downloaded = await self.storage.download_bytes(probe_key)
            if downloaded != test_data:
                raise RuntimeError("Preflight probe content integrity mismatch")
            # Probe cleanup
            await self.storage.delete(probe_key)

            return [
                PreflightCheck(
                    name="storage_driver_connectivity",
                    category=PreflightCategory.STORAGE,
                    is_mandatory=True,
                    status=PreflightStatus.PASS,
                    message=f"Storage driver ({driver_name}) operational and verified via read/write probe",
                    details={"driver": driver_name},
                )
            ]
        except Exception as e:
            logger.error("Preflight storage check failed", driver=driver_name, error=str(e))
            return [
                PreflightCheck(
                    name="storage_driver_connectivity",
                    category=PreflightCategory.STORAGE,
                    is_mandatory=True,
                    status=PreflightStatus.FAIL,
                    message=f"Storage driver ({driver_name}) probe failed: {str(e)}",
                    details={"driver": driver_name, "error": str(e)},
                )
            ]

    async def check_vault(self) -> List[PreflightCheck]:
        """Validates encryption key configuration and vault roundtrip encryption."""
        checks = []

        # Master key configuration check
        has_env_key = bool(
            os.getenv("ENCRYPTION_MASTER_KEY")
            or os.getenv("VAULT_MASTER_KEY")
            or self.settings.ENCRYPTION_MASTER_KEY
        )

        if has_env_key:
            checks.append(
                PreflightCheck(
                    name="vault_master_key",
                    category=PreflightCategory.VAULT,
                    is_mandatory=False,
                    status=PreflightStatus.PASS,
                    message="Production encryption master key configured in environment",
                    details={"source": "environment_or_settings"},
                )
            )
        else:
            checks.append(
                PreflightCheck(
                    name="vault_master_key",
                    category=PreflightCategory.VAULT,
                    is_mandatory=False,
                    status=PreflightStatus.WARN,
                    message="ENCRYPTION_MASTER_KEY not set; using local fallback key (unsuitable for production security)",
                    details={"source": "fallback_default"},
                )
            )

        # Vault encryption probe
        try:
            from clipping.agent.vault.vault import EncryptedCredentialVault

            vault = EncryptedCredentialVault(storage_driver=self.storage)
            payload = {"probe_id": "test_preflight_vault", "test": True}
            ciphertext = vault._encrypt(payload)
            decrypted = vault._decrypt(ciphertext)
            if decrypted != payload:
                raise RuntimeError("Vault roundtrip decryption output did not match original plaintext")

            checks.append(
                PreflightCheck(
                    name="vault_encryption_integrity",
                    category=PreflightCategory.VAULT,
                    is_mandatory=True,
                    status=PreflightStatus.PASS,
                    message="Credential vault cryptographic encryption/decryption roundtrip verified",
                    details={},
                )
            )
        except Exception as e:
            checks.append(
                PreflightCheck(
                    name="vault_encryption_integrity",
                    category=PreflightCategory.VAULT,
                    is_mandatory=True,
                    status=PreflightStatus.FAIL,
                    message=f"Vault cryptographic roundtrip failed: {str(e)}",
                    details={"error": str(e)},
                )
            )

        return checks

    async def check_control_state(self) -> List[PreflightCheck]:
        """Validates Master Control safety states (emergency stop, pause, locks)."""
        checks = []
        try:
            state = await self.control_repo.get_state()

            # Emergency stop check
            if state.emergency_stopped:
                checks.append(
                    PreflightCheck(
                        name="emergency_stop_state",
                        category=PreflightCategory.CONTROL_STATE,
                        is_mandatory=True,
                        status=PreflightStatus.FAIL,
                        message="Emergency Stop is currently ACTIVE; autonomous execution blocked",
                        details={"emergency_stopped": True},
                    )
                )
            else:
                checks.append(
                    PreflightCheck(
                        name="emergency_stop_state",
                        category=PreflightCategory.CONTROL_STATE,
                        is_mandatory=True,
                        status=PreflightStatus.PASS,
                        message="Emergency Stop is inactive (Clear to run)",
                        details={"emergency_stopped": False},
                    )
                )

            # Automation pause check
            if state.automation_paused:
                checks.append(
                    PreflightCheck(
                        name="automation_paused_state",
                        category=PreflightCategory.CONTROL_STATE,
                        is_mandatory=False,
                        status=PreflightStatus.WARN,
                        message="Automation is currently PAUSED by operator",
                        details={"automation_paused": True},
                    )
                )
            else:
                checks.append(
                    PreflightCheck(
                        name="automation_paused_state",
                        category=PreflightCategory.CONTROL_STATE,
                        is_mandatory=False,
                        status=PreflightStatus.PASS,
                        message="Automation state is ACTIVE",
                        details={"automation_paused": False},
                    )
                )

            # Publishing lock check
            checks.append(
                PreflightCheck(
                    name="publishing_lock_state",
                    category=PreflightCategory.CONTROL_STATE,
                    is_mandatory=False,
                    status=PreflightStatus.PASS,
                    message=(
                        "Publishing Lock is ACTIVE (Safe Mode — no live video uploads will occur)"
                        if state.publishing_locked
                        else "Publishing Lock is INACTIVE (Live Mode — external uploads permitted)"
                    ),
                    details={"publishing_locked": state.publishing_locked},
                )
            )

        except Exception as e:
            checks.append(
                PreflightCheck(
                    name="control_state_access",
                    category=PreflightCategory.CONTROL_STATE,
                    is_mandatory=True,
                    status=PreflightStatus.FAIL,
                    message=f"Failed to query Master Control state: {str(e)}",
                    details={"error": str(e)},
                )
            )

        return checks

    def check_platform_credentials(self) -> List[PreflightCheck]:
        """Validates platform integration tokens without leaking secret values."""
        checks = []

        # Whop configuration
        has_whop = bool(
            os.getenv("WHOP_API_KEY")
            or os.getenv("WHOP_API_TOKEN")
            or (self.settings.WHOP_API_KEY and self.settings.WHOP_API_KEY.get_secret_value())
        )
        if has_whop:
            checks.append(
                PreflightCheck(
                    name="whop_integration",
                    category=PreflightCategory.PLATFORM_INTEGRATION,
                    is_mandatory=False,
                    status=PreflightStatus.PASS,
                    message="Whop API token configured for real-time campaign discovery",
                    details={"configured": True},
                )
            )
        else:
            checks.append(
                PreflightCheck(
                    name="whop_integration",
                    category=PreflightCategory.PLATFORM_INTEGRATION,
                    is_mandatory=False,
                    status=PreflightStatus.WARN,
                    message="WHOP_API_KEY not configured; campaign discovery will fall back to browser exploration or cached data",
                    details={"configured": False},
                )
            )

        # YouTube configuration
        has_yt_id = bool(os.getenv("YOUTUBE_CLIENT_ID") or self.settings.YOUTUBE_CLIENT_ID)
        has_yt_sec = bool(os.getenv("YOUTUBE_CLIENT_SECRET") or self.settings.YOUTUBE_CLIENT_SECRET)
        has_yt_ref = bool(os.getenv("YOUTUBE_REFRESH_TOKEN") or self.settings.YOUTUBE_REFRESH_TOKEN)
        if has_yt_id and has_yt_sec and has_yt_ref:
            checks.append(
                PreflightCheck(
                    name="youtube_integration",
                    category=PreflightCategory.PLATFORM_INTEGRATION,
                    is_mandatory=False,
                    status=PreflightStatus.PASS,
                    message="YouTube OAuth credentials configured for automated Shorts publishing",
                    details={"configured": True},
                )
            )
        else:
            missing_parts = []
            if not has_yt_id:
                missing_parts.append("CLIENT_ID")
            if not has_yt_sec:
                missing_parts.append("CLIENT_SECRET")
            if not has_yt_ref:
                missing_parts.append("REFRESH_TOKEN")
            checks.append(
                PreflightCheck(
                    name="youtube_integration",
                    category=PreflightCategory.PLATFORM_INTEGRATION,
                    is_mandatory=False,
                    status=PreflightStatus.WARN,
                    message=f"YouTube OAuth credentials incomplete (missing: {', '.join(missing_parts)}); automated YouTube uploads disabled",
                    details={"configured": False, "missing": missing_parts},
                )
            )

        # Instagram configuration
        has_ig = bool(
            os.getenv("INSTAGRAM_ACCESS_TOKEN")
            or (self.settings.INSTAGRAM_ACCESS_TOKEN and self.settings.INSTAGRAM_ACCESS_TOKEN.get_secret_value())
        )
        if has_ig:
            checks.append(
                PreflightCheck(
                    name="instagram_integration",
                    category=PreflightCategory.PLATFORM_INTEGRATION,
                    is_mandatory=False,
                    status=PreflightStatus.PASS,
                    message="Instagram Graph API access token configured for Reels publishing",
                    details={"configured": True},
                )
            )
        else:
            checks.append(
                PreflightCheck(
                    name="instagram_integration",
                    category=PreflightCategory.PLATFORM_INTEGRATION,
                    is_mandatory=False,
                    status=PreflightStatus.WARN,
                    message="INSTAGRAM_ACCESS_TOKEN not configured; automated Instagram Reels publishing disabled",
                    details={"configured": False},
                )
            )

        # Telegram Escalation configuration
        has_tg_tok = bool(
            os.getenv("TELEGRAM_BOT_TOKEN")
            or (self.settings.TELEGRAM_BOT_TOKEN and self.settings.TELEGRAM_BOT_TOKEN.get_secret_value())
        )
        has_tg_chat = bool(os.getenv("TELEGRAM_CHAT_ID") or self.settings.TELEGRAM_CHAT_ID)
        if has_tg_tok and has_tg_chat:
            checks.append(
                PreflightCheck(
                    name="telegram_escalation",
                    category=PreflightCategory.PLATFORM_INTEGRATION,
                    is_mandatory=False,
                    status=PreflightStatus.PASS,
                    message="Telegram Bot and Chat ID configured for instant operator escalation alerts",
                    details={"configured": True},
                )
            )
        else:
            checks.append(
                PreflightCheck(
                    name="telegram_escalation",
                    category=PreflightCategory.PLATFORM_INTEGRATION,
                    is_mandatory=False,
                    status=PreflightStatus.WARN,
                    message="Telegram escalation credentials incomplete; human operator alerts will be logged locally only",
                    details={"configured": False},
                )
            )

        return checks

    async def validate(self) -> PreflightReport:
        """Executes all preflight checks and constructs structured readiness report."""
        all_checks: List[PreflightCheck] = []

        all_checks.extend(self.check_runtime())
        all_checks.extend(self.check_binaries())
        all_checks.extend(await self.check_storage())
        all_checks.extend(await self.check_vault())
        all_checks.extend(await self.check_control_state())
        all_checks.extend(self.check_platform_credentials())

        failed_mandatory = [c for c in all_checks if c.is_mandatory and c.status == PreflightStatus.FAIL]
        warning_checks = [c for c in all_checks if c.status == PreflightStatus.WARN]

        if failed_mandatory:
            overall_status = OverallPreflightStatus.NOT_READY
            ready = False
            summary = (
                f"SYSTEM NOT READY: {len(failed_mandatory)} mandatory prerequisite check(s) failed: "
                f"{', '.join(c.name for c in failed_mandatory)}."
            )
        elif warning_checks:
            overall_status = OverallPreflightStatus.READY_WITH_WARNINGS
            ready = True
            summary = (
                f"SYSTEM READY WITH WARNINGS: All mandatory prerequisites met. "
                f"{len(warning_checks)} optional integration warning(s): {', '.join(c.name for c in warning_checks)}."
            )
        else:
            overall_status = OverallPreflightStatus.READY
            ready = True
            summary = "SYSTEM FULLY READY: All mandatory and optional operational prerequisites verified."

        return PreflightReport(
            status=overall_status,
            ready=ready,
            timestamp=datetime.now(timezone.utc).isoformat(),
            checks=all_checks,
            summary=summary,
        )
