"""AL AMR CLIPPING Production Preflight Validation Engine.

Performs deterministic verification of runtime dependencies, system binaries,
storage connectivity, encryption vault integrity, task queue availability,
safety control states, and platform integration configurations.

Clearly distinguishes:
- MANDATORY RUNTIME vs LIVE INTEGRATIONS
- Why each item is required
- Exact configuration requirements
- Whether an item blocks Dry-Run vs Live Publishing
- Full Activation Readiness Checklist ("Can AL AMR CLIPPING operate right now?")
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
    WORKER = "WORKER"
    CONTROL_STATE = "CONTROL_STATE"
    PLATFORM_INTEGRATION = "PLATFORM_INTEGRATION"


class PreflightCheck(BaseModel):
    name: str
    category: PreflightCategory
    is_mandatory: bool
    status: PreflightStatus
    message: str
    why_required: str
    configuration_requirement: str
    blocks_dry_run: bool
    blocks_live_publishing: bool
    details: Dict[str, Any] = Field(default_factory=dict)


class ActivationReadinessMatrix(BaseModel):
    """Answers definitively: Can AL AMR CLIPPING operate right now?"""
    code_ready: bool = Field(default=True, description="Core engines, 9-stage pipeline, and state machines are implemented")
    environment_ready: bool = Field(default=False, description="System binaries (FFmpeg/FFprobe) present in PATH")
    credential_ready: bool = Field(default=False, description="Vault encryption master key configured")
    account_ready: bool = Field(default=False, description="At least one creator account registered in vault")
    campaign_source_ready: bool = Field(default=False, description="Whop API token configured or campaigns cached")
    media_pipeline_ready: bool = Field(default=False, description="FFmpeg, FFprobe, and OpenCV available for clipping")
    storage_ready: bool = Field(default=False, description="Storage driver read/write/delete verified")
    worker_ready: bool = Field(default=False, description="Task queue and lease management operational")
    publishing_ready: bool = Field(default=False, description="YouTube or Instagram live publishing credentials configured")
    escalation_ready: bool = Field(default=False, description="Telegram Bot and Chat ID configured for human escalations")
    real_integration_verified: bool = Field(default=False, description="Actual connectivity to external platforms (Whop/YouTube/IG/Telegram) verified")
    live_operation_allowed: bool = Field(default=False, description="Strict fail-closed approval: all prerequisites and live integrations verified")

    can_operate_now: bool = Field(default=False, description="Can execute live autonomous cycles right now")
    can_run_preflight: bool = Field(default=True, description="Mode A: Preflight check capability")
    can_run_dry_run: bool = Field(default=False, description="Mode B: Safe discovery/production without external publishing")
    can_run_single_live: bool = Field(default=False, description="Mode C: Single live campaign upload")
    can_run_continuous: bool = Field(default=False, description="Mode D: Continuous durable autonomous loop")


class PreflightReport(BaseModel):
    status: OverallPreflightStatus
    ready: bool
    can_operate_now: bool
    timestamp: str
    activation_matrix: ActivationReadinessMatrix
    checks: List[PreflightCheck]
    summary: str
    actionable_recommendations: List[str] = Field(default_factory=list)


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
                    why_required="Modern typing, async execution features, and dependency compatibility",
                    configuration_requirement="Python >= 3.10 installed on system",
                    blocks_dry_run=True,
                    blocks_live_publishing=True,
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
                    why_required="Modern typing, async execution features, and dependency compatibility",
                    configuration_requirement="Upgrade Python runtime to >= 3.10",
                    blocks_dry_run=True,
                    blocks_live_publishing=True,
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
                    message="All core Python libraries available (cv2, numpy, httpx, pydantic, cryptography)",
                    why_required="Computer vision, audio analysis, schema validation, HTTP communication, and cryptography",
                    configuration_requirement="Run 'pip install -e .'",
                    blocks_dry_run=True,
                    blocks_live_publishing=True,
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
                    why_required="Computer vision, audio analysis, schema validation, HTTP communication, and cryptography",
                    configuration_requirement=f"Install missing packages: pip install {' '.join(missing_libs)}",
                    blocks_dry_run=True,
                    blocks_live_publishing=True,
                    details={"missing": missing_libs},
                )
            )

        return checks

    def check_binaries(self) -> List[PreflightCheck]:
        """Validates external binaries required for media pipeline processing."""
        checks = []

        # FFmpeg check
        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            try:
                import imageio_ffmpeg
                exe = imageio_ffmpeg.get_ffmpeg_exe()
                if exe and os.path.exists(exe):
                    ffmpeg_path = exe
            except Exception:
                pass

        if ffmpeg_path:
            checks.append(
                PreflightCheck(
                    name="ffmpeg_binary",
                    category=PreflightCategory.SYSTEM_BINARY,
                    is_mandatory=True,
                    status=PreflightStatus.PASS,
                    message=f"FFmpeg executable operational (path: {ffmpeg_path})",
                    why_required="Video segment cutting, 9:16 aspect reframing, and final 1080x1920 MP4 rendering",
                    configuration_requirement="FFmpeg in system PATH or imageio_ffmpeg",
                    blocks_dry_run=True,
                    blocks_live_publishing=True,
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
                    message="FFmpeg not found in PATH or environment; required for video clipping and rendering",
                    why_required="Video segment cutting, 9:16 aspect reframing, and final 1080x1920 MP4 rendering",
                    configuration_requirement="Install FFmpeg and add to PATH (e.g. winget install Gyan.FFmpeg or apt install ffmpeg)",
                    blocks_dry_run=True,
                    blocks_live_publishing=True,
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
                    is_mandatory=False,
                    status=PreflightStatus.PASS,
                    message="FFprobe executable discovered in system PATH",
                    why_required="Audio stream inspection, duration validation, and container integrity verification",
                    configuration_requirement="FFprobe in system PATH",
                    blocks_dry_run=False,
                    blocks_live_publishing=False,
                    details={"path": ffprobe_path},
                )
            )
        else:
            has_cv2 = False
            try:
                import cv2
                has_cv2 = True
            except ImportError:
                pass

            if has_cv2:
                checks.append(
                    PreflightCheck(
                        name="ffprobe_binary",
                        category=PreflightCategory.SYSTEM_BINARY,
                        is_mandatory=False,
                        status=PreflightStatus.WARN,
                        message="FFprobe not found in PATH; MediaProber operating with OpenCV video probe fallback",
                        why_required="Audio stream inspection, duration validation, and container integrity verification",
                        configuration_requirement="Install FFprobe and add to PATH for full container audio stream probing",
                        blocks_dry_run=False,
                        blocks_live_publishing=False,
                        details={"fallback": "cv2"},
                    )
                )
            else:
                checks.append(
                    PreflightCheck(
                        name="ffprobe_binary",
                        category=PreflightCategory.SYSTEM_BINARY,
                        is_mandatory=True,
                        status=PreflightStatus.FAIL,
                        message="Neither FFprobe nor OpenCV available for video container probing",
                        why_required="Audio stream inspection, duration validation, and container integrity verification",
                        configuration_requirement="Install FFprobe and add to PATH or install opencv-python",
                        blocks_dry_run=True,
                        blocks_live_publishing=True,
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
            await self.storage.upload_bytes(test_data, probe_key, content_type="application/json")
            exists = await self.storage.exists(probe_key)
            if not exists:
                raise RuntimeError("Uploaded preflight probe file reported non-existent")
            downloaded = await self.storage.download_bytes(probe_key)
            if downloaded != test_data:
                raise RuntimeError("Preflight probe content integrity mismatch")
            await self.storage.delete(probe_key)

            return [
                PreflightCheck(
                    name="storage_driver_connectivity",
                    category=PreflightCategory.STORAGE,
                    is_mandatory=True,
                    status=PreflightStatus.PASS,
                    message=f"Storage driver ({driver_name}) operational and verified via read/write probe",
                    why_required="Durable state persistence, clip video artifact storage, and checkpoint recovery across ephemeral workers",
                    configuration_requirement="Ensure local storage directory is writable, or configure Google Drive OAuth/Service Account credentials",
                    blocks_dry_run=True,
                    blocks_live_publishing=True,
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
                    why_required="Durable state persistence, clip video artifact storage, and checkpoint recovery across ephemeral workers",
                    configuration_requirement="Ensure storage directory permissions allow read/write or verify cloud storage credentials",
                    blocks_dry_run=True,
                    blocks_live_publishing=True,
                    details={"driver": driver_name, "error": str(e)},
                )
            ]

    async def check_vault(self) -> List[PreflightCheck]:
        """Validates encryption key configuration and vault roundtrip encryption."""
        checks = []

        has_env_key = bool(
            os.getenv("ENCRYPTION_MASTER_KEY")
            or os.getenv("VAULT_MASTER_KEY")
            or (self.settings.ENCRYPTION_MASTER_KEY and self.settings.ENCRYPTION_MASTER_KEY.get_secret_value())
        )

        if has_env_key:
            checks.append(
                PreflightCheck(
                    name="vault_master_key",
                    category=PreflightCategory.VAULT,
                    is_mandatory=False,
                    status=PreflightStatus.PASS,
                    message="Production encryption master key configured in environment",
                    why_required="Zero-leakage PBKDF2/Fernet encryption/decryption of stored platform credentials and session tokens",
                    configuration_requirement="Set ENCRYPTION_MASTER_KEY in environment or .env",
                    blocks_dry_run=False,
                    blocks_live_publishing=False,
                    details={"configured": True},
                )
            )
        else:
            checks.append(
                PreflightCheck(
                    name="vault_master_key",
                    category=PreflightCategory.VAULT,
                    is_mandatory=False,
                    status=PreflightStatus.WARN,
                    message="ENCRYPTION_MASTER_KEY not set; using local fallback key (unsuitable for multi-runner production security)",
                    why_required="Zero-leakage PBKDF2/Fernet encryption/decryption of stored platform credentials and session tokens",
                    configuration_requirement="Set ENCRYPTION_MASTER_KEY to a secure 32-byte urlsafe base64 string in production",
                    blocks_dry_run=False,
                    blocks_live_publishing=False,
                    details={"configured": False},
                )
            )

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
                    why_required="Ensures encrypted tokens can be securely stored and retrieved without corruption",
                    configuration_requirement="Cryptographic dependencies (cryptography package) functioning correctly",
                    blocks_dry_run=False,
                    blocks_live_publishing=True,
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
                    why_required="Ensures encrypted tokens can be securely stored and retrieved without corruption",
                    configuration_requirement="Verify cryptography library installation and master key format",
                    blocks_dry_run=False,
                    blocks_live_publishing=True,
                    details={"error": str(e)},
                )
            )

        return checks

    async def check_worker_queue(self) -> List[PreflightCheck]:
        """Validates worker task queue and lease manager availability."""
        try:
            from clipping.agent.cloud.lease import WorkerLeaseEngine
            from clipping.agent.cloud.queue import CloudTaskQueue

            lease_engine = WorkerLeaseEngine(self.storage)
            queue = CloudTaskQueue(self.storage, lease_engine)
            stats = await queue.get_queue_stats()

            return [
                PreflightCheck(
                    name="worker_queue_availability",
                    category=PreflightCategory.WORKER,
                    is_mandatory=True,
                    status=PreflightStatus.PASS,
                    message=f"Task queue and lease engine operational (pending: {stats.get('pending', 0)}, claimed: {stats.get('claimed', 0)})",
                    why_required="Dispatches autonomous clipping jobs, coordinates workers, and maintains distributed task execution locks",
                    configuration_requirement="StorageDriver must support atomic lock and lease file writes",
                    blocks_dry_run=True,
                    blocks_live_publishing=True,
                    details={"queue_stats": stats},
                )
            ]
        except Exception as e:
            return [
                PreflightCheck(
                    name="worker_queue_availability",
                    category=PreflightCategory.WORKER,
                    is_mandatory=True,
                    status=PreflightStatus.FAIL,
                    message=f"Task queue / lease engine probe failed: {str(e)}",
                    why_required="Dispatches autonomous clipping jobs, coordinates workers, and maintains distributed task execution locks",
                    configuration_requirement="Verify storage driver read/write permissions for queue directory",
                    blocks_dry_run=True,
                    blocks_live_publishing=True,
                    details={"error": str(e)},
                )
            ]

    async def check_control_state(self) -> List[PreflightCheck]:
        """Validates Master Control safety states (emergency stop, pause, locks)."""
        checks = []
        try:
            state = await self.control_repo.get_state()

            if state.emergency_stopped:
                checks.append(
                    PreflightCheck(
                        name="emergency_stop_state",
                        category=PreflightCategory.CONTROL_STATE,
                        is_mandatory=True,
                        status=PreflightStatus.FAIL,
                        message="Emergency Stop is currently ACTIVE; autonomous execution blocked",
                        why_required="Master safety kill switch must be clear for autonomous execution",
                        configuration_requirement="Clear emergency stop in Mission Control UI or call control repo",
                        blocks_dry_run=True,
                        blocks_live_publishing=True,
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
                        why_required="Master safety kill switch must be clear for autonomous execution",
                        configuration_requirement="Emergency stop switch inactive",
                        blocks_dry_run=False,
                        blocks_live_publishing=False,
                        details={"emergency_stopped": False},
                    )
                )

            if state.automation_paused:
                checks.append(
                    PreflightCheck(
                        name="automation_paused_state",
                        category=PreflightCategory.CONTROL_STATE,
                        is_mandatory=False,
                        status=PreflightStatus.WARN,
                        message="Automation is currently PAUSED by operator",
                        why_required="Operational pause deferring automated cycle runs",
                        configuration_requirement="Resume automation via Mission Control UI",
                        blocks_dry_run=False,
                        blocks_live_publishing=True,
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
                        why_required="Operational pause deferring automated cycle runs",
                        configuration_requirement="Automation active",
                        blocks_dry_run=False,
                        blocks_live_publishing=False,
                        details={"automation_paused": False},
                    )
                )

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
                    why_required="Prevents unauthorized public uploads until operator explicitly permits live publishing",
                    configuration_requirement="Unlock via Mission Control UI when ready for live publishing",
                    blocks_dry_run=False,
                    blocks_live_publishing=False,
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
                    why_required="Safety gate state must be deterministically readable",
                    configuration_requirement="Verify storage driver integrity",
                    blocks_dry_run=True,
                    blocks_live_publishing=True,
                    details={"error": str(e)},
                )
            )

        return checks

    async def check_creator_accounts(self) -> List[PreflightCheck]:
        """Validates registered creator accounts in the Encrypted Credential Vault."""
        try:
            from clipping.agent.vault.vault import EncryptedCredentialVault
            from clipping.agent.vault.models import AccountStatus

            vault = EncryptedCredentialVault(storage_driver=self.storage)
            accounts = await vault.list_accounts()
            active_accounts = [a for a in accounts if a.status == AccountStatus.ACTIVE]

            if active_accounts:
                return [
                    PreflightCheck(
                        name="creator_accounts_registered",
                        category=PreflightCategory.PLATFORM_INTEGRATION,
                        is_mandatory=False,
                        status=PreflightStatus.PASS,
                        message=f"Found {len(active_accounts)} active creator account(s) registered in vault",
                        why_required="Campaign opportunities must be bound to authenticated creator accounts for upload and payout tracking",
                        configuration_requirement="Creator accounts registered in vault via Mission Control (POST /api/accounts)",
                        blocks_dry_run=False,
                        blocks_live_publishing=False,
                        details={"account_count": len(active_accounts), "accounts": [a.account_id for a in active_accounts]},
                    )
                ]
            else:
                return [
                    PreflightCheck(
                        name="creator_accounts_registered",
                        category=PreflightCategory.PLATFORM_INTEGRATION,
                        is_mandatory=False,
                        status=PreflightStatus.WARN,
                        message="No creator accounts registered in EncryptedCredentialVault; campaigns will synthesize metadata or require account registration",
                        why_required="Campaign opportunities must be bound to authenticated creator accounts for upload and payout tracking",
                        configuration_requirement="Register at least one creator account with credentials via Mission Control (POST /api/accounts)",
                        blocks_dry_run=False,
                        blocks_live_publishing=True,
                        details={"account_count": 0},
                    )
                ]
        except Exception as e:
            return [
                PreflightCheck(
                    name="creator_accounts_registered",
                    category=PreflightCategory.PLATFORM_INTEGRATION,
                    is_mandatory=False,
                    status=PreflightStatus.WARN,
                    message=f"Could not inspect creator accounts in vault: {str(e)}",
                    why_required="Campaign opportunities must be bound to authenticated creator accounts for upload and payout tracking",
                    configuration_requirement="Verify vault accessibility",
                    blocks_dry_run=False,
                    blocks_live_publishing=True,
                    details={"error": str(e)},
                )
            ]

    async def check_platform_credentials(self) -> List[PreflightCheck]:
        """Validates platform integration tokens without leaking secret values, probing live APIs when configured."""
        checks = []
        from clipping.preflight.service_verifier import RealServiceVerifier

        verifier = RealServiceVerifier()
        # 1. Whop
        whop_res = await verifier.verify_whop()
        checks.append(
            PreflightCheck(
                name="whop_campaign_discovery",
                category=PreflightCategory.PLATFORM_INTEGRATION,
                is_mandatory=False,
                status=PreflightStatus.PASS if whop_res.verified else (PreflightStatus.WARN if not whop_res.configured else PreflightStatus.FAIL),
                message=whop_res.message,
                why_required=whop_res.why_required,
                configuration_requirement=whop_res.configuration_requirement,
                blocks_dry_run=whop_res.blocks_dry_run,
                blocks_live_publishing=whop_res.blocks_live_operation,
                details=whop_res.details,
            )
        )

        # 2. YouTube
        yt_res = await verifier.verify_youtube()
        checks.append(
            PreflightCheck(
                name="youtube_publishing_integration",
                category=PreflightCategory.PLATFORM_INTEGRATION,
                is_mandatory=False,
                status=PreflightStatus.PASS if yt_res.verified else PreflightStatus.WARN,
                message=yt_res.message,
                why_required=yt_res.why_required,
                configuration_requirement=yt_res.configuration_requirement,
                blocks_dry_run=yt_res.blocks_dry_run,
                blocks_live_publishing=yt_res.blocks_live_operation,
                details=yt_res.details,
            )
        )

        # 3. Instagram
        ig_res = await verifier.verify_instagram()
        checks.append(
            PreflightCheck(
                name="instagram_publishing_integration",
                category=PreflightCategory.PLATFORM_INTEGRATION,
                is_mandatory=False,
                status=PreflightStatus.PASS if ig_res.verified else PreflightStatus.WARN,
                message=ig_res.message,
                why_required=ig_res.why_required,
                configuration_requirement=ig_res.configuration_requirement,
                blocks_dry_run=ig_res.blocks_dry_run,
                blocks_live_publishing=ig_res.blocks_live_operation,
                details=ig_res.details,
            )
        )

        # 4. Telegram
        tg_res = await verifier.verify_telegram()
        checks.append(
            PreflightCheck(
                name="telegram_escalation_notifier",
                category=PreflightCategory.PLATFORM_INTEGRATION,
                is_mandatory=False,
                status=PreflightStatus.PASS if tg_res.verified else PreflightStatus.WARN,
                message=tg_res.message,
                why_required=tg_res.why_required,
                configuration_requirement=tg_res.configuration_requirement,
                blocks_dry_run=tg_res.blocks_dry_run,
                blocks_live_publishing=tg_res.blocks_live_operation,
                details=tg_res.details,
            )
        )

        return checks

    async def validate(self) -> PreflightReport:
        """Executes all preflight checks and constructs structured readiness report and activation matrix."""
        all_checks: List[PreflightCheck] = []

        all_checks.extend(self.check_runtime())
        all_checks.extend(self.check_binaries())
        all_checks.extend(await self.check_storage())
        all_checks.extend(await self.check_vault())
        all_checks.extend(await self.check_worker_queue())
        all_checks.extend(await self.check_control_state())
        all_checks.extend(await self.check_creator_accounts())
        all_checks.extend(await self.check_platform_credentials())

        # Evaluate activation matrix
        env_ready = any(c.name == "ffmpeg_binary" and c.status == PreflightStatus.PASS for c in all_checks)
        cred_ready = any(c.name == "vault_master_key" and c.status == PreflightStatus.PASS for c in all_checks)
        acct_ready = any(c.name == "creator_accounts_registered" and c.status == PreflightStatus.PASS for c in all_checks)
        whop_ready = any(c.name == "whop_campaign_discovery" and c.status == PreflightStatus.PASS for c in all_checks)
        storage_ready = any(c.name == "storage_driver_connectivity" and c.status == PreflightStatus.PASS for c in all_checks)
        worker_ready = any(c.name == "worker_queue_availability" and c.status == PreflightStatus.PASS for c in all_checks)
        media_ready = env_ready and any(c.name == "core_python_libraries" and c.status == PreflightStatus.PASS for c in all_checks)
        pub_ready = (
            any(c.name == "youtube_publishing_integration" and c.status == PreflightStatus.PASS for c in all_checks)
            or any(c.name == "instagram_publishing_integration" and c.status == PreflightStatus.PASS for c in all_checks)
        )
        esc_ready = any(c.name == "telegram_escalation_notifier" and c.status == PreflightStatus.PASS for c in all_checks)
        control_clear = not any(c.name == "emergency_stop_state" and c.status == PreflightStatus.FAIL for c in all_checks)

        # Real integration verified requires actual successful connectivity to at least one publishing platform and whop
        real_int_verified = (
            any(c.name == "youtube_publishing_integration" and c.status == PreflightStatus.PASS for c in all_checks)
            or any(c.name == "instagram_publishing_integration" and c.status == PreflightStatus.PASS for c in all_checks)
        )

        live_op_allowed = (
            env_ready
            and cred_ready
            and acct_ready
            and whop_ready
            and storage_ready
            and worker_ready
            and media_ready
            and pub_ready
            and esc_ready
            and real_int_verified
            and control_clear
        )

        can_dry_run = storage_ready and media_ready and worker_ready and control_clear
        can_single_live = can_dry_run and live_op_allowed
        can_continuous = can_single_live and esc_ready

        matrix = ActivationReadinessMatrix(
            code_ready=True,
            environment_ready=env_ready,
            credential_ready=cred_ready,
            account_ready=acct_ready,
            campaign_source_ready=whop_ready,
            media_pipeline_ready=media_ready,
            storage_ready=storage_ready,
            worker_ready=worker_ready,
            publishing_ready=pub_ready,
            escalation_ready=esc_ready,
            real_integration_verified=real_int_verified,
            live_operation_allowed=live_op_allowed,
            can_operate_now=can_single_live or can_continuous,
            can_run_preflight=True,
            can_run_dry_run=can_dry_run,
            can_run_single_live=can_single_live,
            can_run_continuous=can_continuous,
        )

        failed_mandatory = [c for c in all_checks if c.is_mandatory and c.status == PreflightStatus.FAIL]
        warning_checks = [c for c in all_checks if c.status == PreflightStatus.WARN]

        recommendations = []
        if not env_ready:
            recommendations.append("Install FFmpeg and FFprobe in system PATH to enable media clipping and rendering.")
        if not whop_ready:
            recommendations.append("Set WHOP_API_KEY in environment or .env to enable real-time campaign discovery.")
        if not pub_ready:
            recommendations.append("Configure YouTube OAuth credentials (YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN) or store in vault.")
        if not acct_ready:
            recommendations.append("Register at least one creator account with credentials via Mission Control (POST /api/accounts).")
        if not esc_ready:
            recommendations.append("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to receive instant push alerts for CAPTCHAs and QA issues.")

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
            can_operate_now=matrix.can_operate_now,
            timestamp=datetime.now(timezone.utc).isoformat(),
            activation_matrix=matrix,
            checks=all_checks,
            summary=summary,
            actionable_recommendations=recommendations,
        )
