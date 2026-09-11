"""Production health and readiness probes.

Guarantees that deployment infrastructure (Docker, Kubernetes, load balancers,
systemd) can accurately determine application liveness and operational readiness
without exposing internal credentials, secrets, or host-specific paths.
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any

from . import __version__, db, paths
from .jobs.queue import queue
from .pipeline import ffmpeg

log = logging.getLogger(__name__)


def check_liveness() -> dict[str, Any]:
    """Lightweight liveness probe confirming the Python process is responsive."""
    return {
        "status": "ok",
        "service": "autoclip",
        "version": __version__,
    }


def check_readiness() -> tuple[bool, dict[str, Any]]:
    """Readiness probe checking essential local subsystems.

    Checks:
    1. Database connectivity and integrity.
    2. Persistent storage layout writability.
    3. FFmpeg and ffprobe binary availability.
    4. Worker queue status.

    Returns (is_ready, details_dict).
    """
    checks: dict[str, Any] = {}
    ready = True

    # 1. Database check
    try:
        with db.connection() as conn:
            row = conn.execute("PRAGMA quick_check").fetchone()
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            if row and row[0] == "ok" and "jobs" in tables:
                checks["database"] = "ok"
            else:
                checks["database"] = "not_initialized"
                ready = False
    except Exception as exc:
        log.error("Readiness check: database unreachable: %s", exc)
        checks["database"] = "unreachable"
        ready = False

    # 2. Storage check (verifies root and required directories exist and are writable)
    try:
        root_dir = paths.root()
        if not root_dir.exists():
            paths.ensure_layout()

        test_file = paths.work_dir() / ".readiness_probe"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
        checks["storage"] = "ok"
    except Exception as exc:
        log.error("Readiness check: storage not writable: %s", exc)
        checks["storage"] = "write_failed"
        ready = False

    # 3. FFmpeg binary check
    try:
        ff_bin = shutil.which("ffmpeg")
        fp_bin = shutil.which("ffprobe")
        if ff_bin and fp_bin:
            checks["ffmpeg"] = "ok"
        else:
            checks["ffmpeg"] = "missing_binary"
            ready = False
    except Exception as exc:
        log.error("Readiness check: ffmpeg probe failed: %s", exc)
        checks["ffmpeg"] = "error"
        ready = False

    # 4. Worker queue check
    try:
        status = queue.status()
        checks["worker"] = {
            "status": "running" if queue._task is not None and not queue._task.done() else "stopped",
            "queued_jobs": status.queued,
            "has_active_job": status.running_job_id is not None,
        }
    except Exception as exc:
        log.error("Readiness check: queue status failed: %s", exc)
        checks["worker"] = "error"
        ready = False

    result = {
        "status": "ok" if ready else "degraded",
        "ready": ready,
        "service": "autoclip",
        "version": __version__,
        "checks": checks,
    }
    return ready, result
