"""Ephemeral Worker Scratch Workspace Abstraction."""

import os
import shutil
import tempfile
from typing import Optional
from clipping.logging.logger import get_logger

logger = get_logger("clipping.core.workspace")


class WorkerScratchWorkspace:
    """
    Manages isolated, ephemeral scratch directories on cloud/local workers.
    Ensures safe path containment and automatic cleanup on job completion.
    """

    def __init__(
        self,
        job_id: str,
        base_dir: Optional[str] = None,
        max_size_bytes: int = 15 * 1024 * 1024 * 1024,  # 15 GB default threshold
    ):
        self.job_id = job_id
        # Use RUNNER_TEMP if set by GitHub Actions, otherwise system tempdir or custom base_dir
        runner_temp = os.environ.get("RUNNER_TEMP")
        parent_dir = base_dir or runner_temp or tempfile.gettempdir()
        self.workspace_dir = os.path.abspath(os.path.join(parent_dir, "clipping_scratch", self.job_id))
        self.max_size_bytes = max_size_bytes
        self._is_active = False

    def enter(self) -> str:
        """Initializes the scratch workspace directory."""
        os.makedirs(self.workspace_dir, exist_ok=True)
        self._is_active = True
        logger.info("Initialized worker scratch workspace", job_id=self.job_id, path=self.workspace_dir)
        return self.workspace_dir

    def cleanup(self) -> None:
        """Removes the ephemeral scratch workspace directory and all contents."""
        if os.path.exists(self.workspace_dir):
            try:
                shutil.rmtree(self.workspace_dir)
                logger.info("Cleaned up worker scratch workspace", job_id=self.job_id, path=self.workspace_dir)
            except Exception as e:
                logger.warning("Failed to clean scratch workspace", path=self.workspace_dir, error=str(e))
        self._is_active = False

    def get_path(self, relative_name: str) -> str:
        """
        Safely resolves a file path inside the workspace, preventing directory traversal.
        """
        if not relative_name or relative_name.startswith("/") or relative_name.startswith("\\") or os.path.isabs(relative_name):
            raise ValueError(f"Absolute path override not permitted in scratch workspace: '{relative_name}'")

        if not self._is_active:
            self.enter()

        # Sanitize relative name
        clean_rel = os.path.normpath(relative_name)
        if clean_rel.startswith("..") or ".." in clean_rel.split(os.sep) or ".." in clean_rel.split("/"):
            raise ValueError(f"Directory traversal detected in relative path: '{relative_name}'")

        target_path = os.path.abspath(os.path.join(self.workspace_dir, clean_rel))

        # Defense against path traversal outside scratch root
        if not target_path.startswith(self.workspace_dir):
            raise ValueError(f"Directory traversal detected: '{relative_name}' resolves outside workspace root")

        # Ensure parent directory exists
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        return target_path

    def get_current_size(self) -> int:
        """Calculates total disk usage of the scratch workspace in bytes."""
        total_size = 0
        if not os.path.exists(self.workspace_dir):
            return 0
        for dirpath, _, filenames in os.walk(self.workspace_dir):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total_size += os.path.getsize(fp)
                except OSError:
                    pass
        return total_size

    def __enter__(self) -> "WorkerScratchWorkspace":
        self.enter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    async def __aenter__(self) -> "WorkerScratchWorkspace":
        self.enter()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
