"""Comprehensive Quality Assurance Evaluation Engine."""

import os
import tempfile
from typing import List, Optional
from clipping.contracts.qa import (
    QACheck,
    QACheckStatus,
    QASeverity,
    QAReport,
    MediaValidationResult,
)
from clipping.contracts.director import ReframePlan
from clipping.contracts.clip import RankedCandidate
from clipping.contracts.rendering import RenderOutput
from clipping.qa.prober import MediaProber
from clipping.qa.checks import (
    check_media_integrity,
    check_subtitle_integrity,
    check_reframe_plan_integrity,
    check_artifact_consistency,
)
from clipping.storage.base import StorageDriver
from clipping.logging.logger import get_logger

logger = get_logger("clipping.qa.engine")


class QAEngine:
    """
    Evaluates rendered short-form vertical video against layered QA standards (L1-L5).
    Enforces strict gating policy before publishing eligibility.
    """

    def __init__(self, media_prober: Optional[MediaProber] = None):
        self.media_prober = media_prober or MediaProber()

    async def evaluate_rendered_clip(
        self,
        clip_id: str,
        source_video_id: str,
        storage_driver: StorageDriver,
        expected_duration: float,
        reframe_plan: Optional[ReframePlan] = None,
        selected_clip: Optional[RankedCandidate] = None,
    ) -> QAReport:
        final_video_key = f"clips/{clip_id}/final_1080x1920.mp4"
        subtitles_key = f"clips/{clip_id}/subtitles.ass"
        output_meta_key = f"clips/{clip_id}/render_output.json"
        qa_report_key = f"clips/{clip_id}/qa_report.json"

        checks: List[QACheck] = []
        media_val: Optional[MediaValidationResult] = None

        # 1. Media File Probing
        if not await storage_driver.exists(final_video_key):
            checks.append(
                QACheck(
                    check_id="media_file_exists",
                    name="Final Rendered MP4 Existence",
                    status=QACheckStatus.FAIL,
                    severity=QASeverity.CRITICAL,
                    message=f"Rendered video artifact not found in storage: {final_video_key}",
                )
            )
        else:
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_video_path = os.path.join(tmp_dir, "probe_clip.mp4")
                await storage_driver.download(final_video_key, tmp_video_path)
                media_val = await self.media_prober.probe_media(tmp_video_path)
                checks.extend(check_media_integrity(media_val, expected_duration=expected_duration))

        # 2. Subtitle Script Validation
        if await storage_driver.exists(subtitles_key):
            sub_bytes = await storage_driver.download_bytes(subtitles_key)
            ass_content = sub_bytes.decode("utf-8", errors="replace")
            checks.extend(check_subtitle_integrity(ass_content, clip_duration=expected_duration))
        else:
            checks.append(
                QACheck(
                    check_id="subtitles_file_exists",
                    name="Subtitles Script Existence",
                    status=QACheckStatus.WARN,
                    severity=QASeverity.WARNING,
                    message="No subtitle script found for clip",
                )
            )

        # 3. Reframe Plan Geometry Validation
        if reframe_plan:
            checks.extend(
                check_reframe_plan_integrity(
                    reframe_plan,
                    source_w=reframe_plan.source_width,
                    source_h=reframe_plan.source_height,
                )
            )

        # 4. Cross-Artifact Consistency Validation
        render_output = None
        if await storage_driver.exists(output_meta_key):
            meta_bytes = await storage_driver.download_bytes(output_meta_key)
            render_output = RenderOutput.model_validate_json(meta_bytes.decode("utf-8"))

        checks.extend(
            check_artifact_consistency(
                clip_id=clip_id,
                source_video_id=source_video_id,
                selected_clip=selected_clip,
                reframe_plan=reframe_plan,
                render_output=render_output,
            )
        )

        # 5. Determine Gating Verdict
        has_critical_failure = any(
            c.status == QACheckStatus.FAIL and c.severity == QASeverity.CRITICAL
            for c in checks
        )
        has_warning = any(c.status in (QACheckStatus.WARN, QACheckStatus.FAIL) for c in checks)

        if has_critical_failure:
            overall_status = QACheckStatus.FAIL
            can_publish = False
            summary = "CRITICAL QA GATING FAILURE: Video cannot proceed to approval/publishing."
        elif has_warning:
            overall_status = QACheckStatus.WARN
            can_publish = True
            summary = "QA Passed with Warnings: Review recommended before publishing."
        else:
            overall_status = QACheckStatus.PASS
            can_publish = True
            summary = "QA Passed: All structural and media checks verified successfully."

        report = QAReport(
            clip_id=clip_id,
            source_video_id=source_video_id,
            overall_status=overall_status,
            can_publish=can_publish,
            checks=checks,
            media_validation=media_val,
            summary=summary,
        )

        # Persist QA report to storage vault
        await storage_driver.upload_bytes(
            data=report.model_dump_json(indent=2).encode("utf-8"),
            storage_key=qa_report_key,
            content_type="application/json",
        )

        logger.info(
            "QA evaluation completed",
            clip_id=clip_id,
            overall_status=overall_status.value,
            can_publish=can_publish,
        )
        return report
