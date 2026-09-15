"""Atomic Output Packager for Step 22 exports with deterministic safe naming."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from .models import FinalRenderGateResult, FinalRenderMetadata


class OutputPackager:
    """Manages deterministic directory structure, filenames, and atomic publishing packages."""

    @staticmethod
    def slugify_name(name: str, max_length: int = 40) -> str:
        """Sanitizes text to safe filesystem slug without path traversal characters."""
        # Strip path traversal characters
        cleaned = name.replace("..", "").replace("/", "").replace("\\", "").strip()
        slug = re.sub(r"[^\w\s-]", "", cleaned, flags=re.UNICODE).strip().lower()
        slug = re.sub(r"[\s_-]+", "_", slug).strip("_")
        return slug[:max_length] or "clip"

    @staticmethod
    def get_package_dir(exports_base_dir: Path, job_id: str, rank: int, clip_id: str, title: str = "") -> Path:
        """Returns deterministic, collision-free directory for clip package: exports/<job_id>/clip_<rank:03d>_<safe_slug>/."""
        safe_job = OutputPackager.slugify_name(job_id, max_length=50)
        safe_title = OutputPackager.slugify_name(title or clip_id, max_length=30)
        clip_dir_name = f"clip_{rank:03d}_{safe_title}"
        return exports_base_dir / safe_job / clip_dir_name

    @staticmethod
    def package_clip(
        package_dir: Path,
        temp_video_path: Path,
        metadata: FinalRenderMetadata,
        quality_result: FinalRenderGateResult,
        sidecar_srt_path: Path | None = None,
    ) -> Path:
        """Atomically packages the rendered video and accompanying provenance metadata."""
        package_dir.mkdir(parents=True, exist_ok=True)
        final_mp4_path = package_dir / "final.mp4"

        # Atomic move from temp to final
        if temp_video_path.resolve() != final_mp4_path.resolve():
            if temp_video_path.is_file():
                shutil.move(str(temp_video_path), str(final_mp4_path))

        # Write metadata.json
        metadata_file = package_dir / "metadata.json"
        metadata_file.write_text(
            json.dumps(metadata.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Write quality.json
        quality_file = package_dir / "quality.json"
        quality_file.write_text(
            json.dumps(quality_result.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Copy sidecar SRT if available
        if sidecar_srt_path and sidecar_srt_path.is_file():
            shutil.copy2(sidecar_srt_path, package_dir / "captions.srt")

        return final_mp4_path
