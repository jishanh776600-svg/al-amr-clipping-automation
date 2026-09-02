"""Deterministic Layered Quality Assurance & Validation Checks."""

import re
from typing import List, Optional
from clipping.contracts.qa import (
    QACheck,
    QACheckStatus,
    QASeverity,
    MediaValidationResult,
)
from clipping.contracts.director import ReframePlan
from clipping.contracts.clip import RankedCandidate
from clipping.contracts.rendering import RenderOutput


def check_media_integrity(
    media: MediaValidationResult,
    expected_duration: float,
    duration_tolerance_seconds: float = 1.0,
) -> List[QACheck]:
    checks: List[QACheck] = []

    # 1. File Non-Empty
    checks.append(
        QACheck(
            check_id="media_non_empty",
            name="Media File Size Check",
            status=QACheckStatus.PASS if media.file_size_bytes > 0 else QACheckStatus.FAIL,
            severity=QASeverity.CRITICAL,
            measured_value=media.file_size_bytes,
            expected_value="> 0 bytes",
            message="Media file is non-empty" if media.file_size_bytes > 0 else "Media file is empty (0 bytes)",
        )
    )

    # 2. Container Validity & Stream Existence
    checks.append(
        QACheck(
            check_id="media_container_readable",
            name="Container & Stream Integrity",
            status=QACheckStatus.PASS if media.is_valid else QACheckStatus.FAIL,
            severity=QASeverity.CRITICAL,
            measured_value=media.video_codec,
            expected_value="Valid Video Stream",
            message="Video container decoded successfully" if media.is_valid else f"Corrupt container or missing stream: {media.video_codec}",
        )
    )

    # 3. Resolution (1080x1920 portrait)
    is_res_correct = (media.width == 1080 and media.height == 1920)
    checks.append(
        QACheck(
            check_id="media_resolution_1080x1920",
            name="Portrait Resolution (1080x1920)",
            status=QACheckStatus.PASS if is_res_correct else QACheckStatus.FAIL,
            severity=QASeverity.CRITICAL,
            measured_value=f"{media.width}x{media.height}",
            expected_value="1080x1920",
            message="Resolution is exactly 1080x1920" if is_res_correct else f"Invalid resolution: {media.width}x{media.height}",
        )
    )

    # 4. Frame Rate (24 - 60 FPS)
    is_fps_valid = (23.9 <= media.fps <= 60.5)
    checks.append(
        QACheck(
            check_id="media_fps_valid",
            name="Framerate Check",
            status=QACheckStatus.PASS if is_fps_valid else QACheckStatus.WARN,
            severity=QASeverity.WARNING,
            measured_value=media.fps,
            expected_value="24.0 - 60.0 FPS",
            message=f"FPS is {media.fps:.1f}",
        )
    )

    # 5. Duration Tolerance
    dur_diff = abs(media.duration_seconds - expected_duration)
    is_dur_ok = dur_diff <= duration_tolerance_seconds
    checks.append(
        QACheck(
            check_id="media_duration_tolerance",
            name="Duration Tolerance Check",
            status=QACheckStatus.PASS if is_dur_ok else QACheckStatus.FAIL,
            severity=QASeverity.CRITICAL,
            measured_value=round(media.duration_seconds, 2),
            expected_value=round(expected_duration, 2),
            message=f"Rendered duration {media.duration_seconds:.2f}s matches expected {expected_duration:.2f}s (delta: {dur_diff:.2f}s)",
        )
    )

    return checks


def check_subtitle_integrity(
    ass_content: str,
    clip_duration: float,
) -> List[QACheck]:
    checks: List[QACheck] = []

    # 1. Structure Verification
    has_script_info = "[Script Info]" in ass_content
    has_styles = "[V4+ Styles]" in ass_content
    has_events = "[Events]" in ass_content
    struct_ok = has_script_info and has_styles and has_events

    checks.append(
        QACheck(
            check_id="subtitle_ass_structure",
            name="ASS Subtitle Header Structure",
            status=QACheckStatus.PASS if struct_ok else QACheckStatus.FAIL,
            severity=QASeverity.CRITICAL,
            message="Valid ASS v4.00+ script structure" if struct_ok else "Malformed ASS script header",
        )
    )

    # 2. Extract Dialogue Events
    dialogue_lines = [
        line for line in ass_content.splitlines()
        if line.strip().startswith("Dialogue:")
    ]

    checks.append(
        QACheck(
            check_id="subtitle_events_present",
            name="Subtitle Dialogue Events Presence",
            status=QACheckStatus.PASS if dialogue_lines else QACheckStatus.WARN,
            severity=QASeverity.WARNING,
            measured_value=len(dialogue_lines),
            expected_value="> 0 events",
            message=f"Found {len(dialogue_lines)} subtitle events",
        )
    )

    # 3. Timestamp Validation
    timestamps_valid = True
    bounds_valid = True
    time_regex = re.compile(r"Dialogue:\s*\d+,\s*(\d+:\d+:\d+\.\d+),\s*(\d+:\d+:\d+\.\d+)")

    def parse_ass_timestamp(ts: str) -> float:
        parts = ts.split(":")
        hrs = float(parts[0])
        mins = float(parts[1])
        secs = float(parts[2])
        return hrs * 3600 + mins * 60 + secs

    for line in dialogue_lines:
        match = time_regex.search(line)
        if match:
            start_sec = parse_ass_timestamp(match.group(1))
            end_sec = parse_ass_timestamp(match.group(2))
            if start_sec < 0.0 or end_sec <= start_sec:
                timestamps_valid = False
            if end_sec > (clip_duration + 1.5):  # 1.5s margin
                bounds_valid = False

    checks.append(
        QACheck(
            check_id="subtitle_timestamps_non_negative",
            name="Subtitle Non-Negative Chronology",
            status=QACheckStatus.PASS if timestamps_valid else QACheckStatus.FAIL,
            severity=QASeverity.CRITICAL,
            message="All subtitle timestamps are chronological and non-negative" if timestamps_valid else "Invalid or negative subtitle timestamps detected",
        )
    )

    checks.append(
        QACheck(
            check_id="subtitle_bounds_inside_clip",
            name="Subtitle Events Within Clip Duration",
            status=QACheckStatus.PASS if bounds_valid else QACheckStatus.FAIL,
            severity=QASeverity.CRITICAL,
            message="All subtitle events finish within clip bounds" if bounds_valid else "Subtitle events extend beyond clip duration",
        )
    )

    return checks


def check_reframe_plan_integrity(
    reframe_plan: ReframePlan,
    source_w: int = 1920,
    source_h: int = 1080,
) -> List[QACheck]:
    checks: List[QACheck] = []

    # 1. Keyframes Present
    has_kfs = len(reframe_plan.keyframes) > 0
    checks.append(
        QACheck(
            check_id="reframe_keyframes_exist",
            name="Reframe Keyframe Existence",
            status=QACheckStatus.PASS if has_kfs else QACheckStatus.FAIL,
            severity=QASeverity.CRITICAL,
            measured_value=len(reframe_plan.keyframes),
            expected_value=">= 1 keyframes",
            message=f"Reframe plan contains {len(reframe_plan.keyframes)} keyframes",
        )
    )

    # 2. Geometric Bounds Check
    bounds_ok = True
    monotonic_ok = True
    last_t = -1.0

    for kf in reframe_plan.keyframes:
        if kf.timestamp < last_t:
            monotonic_ok = False
        last_t = kf.timestamp

        if kf.crop_x < 0 or (kf.crop_x + kf.crop_w) > source_w:
            bounds_ok = False
        if kf.crop_y < 0 or (kf.crop_y + kf.crop_h) > source_h:
            bounds_ok = False

    checks.append(
        QACheck(
            check_id="reframe_crop_bounds_safe",
            name="Crop Geometry In-Bounds Check",
            status=QACheckStatus.PASS if bounds_ok else QACheckStatus.FAIL,
            severity=QASeverity.CRITICAL,
            message="All crop keyframes remain strictly inside source frame dimensions" if bounds_ok else "Crop coordinates exceed source video boundaries",
        )
    )

    checks.append(
        QACheck(
            check_id="reframe_timestamps_monotonic",
            name="Keyframe Chronology Monotonicity",
            status=QACheckStatus.PASS if monotonic_ok else QACheckStatus.FAIL,
            severity=QASeverity.CRITICAL,
            message="Keyframes are ordered monotonically" if monotonic_ok else "Keyframe timestamps out of order",
        )
    )

    return checks


def check_artifact_consistency(
    clip_id: str,
    source_video_id: str,
    selected_clip: Optional[RankedCandidate] = None,
    reframe_plan: Optional[ReframePlan] = None,
    render_output: Optional[RenderOutput] = None,
) -> List[QACheck]:
    checks: List[QACheck] = []

    # 1. ID Consistency
    ids_match = True
    if selected_clip and selected_clip.candidate.candidate_id != clip_id:
        ids_match = False
    if reframe_plan and reframe_plan.clip_id != clip_id:
        ids_match = False
    if render_output and render_output.clip_id != clip_id:
        ids_match = False

    checks.append(
        QACheck(
            check_id="artifact_id_alignment",
            name="Cross-Artifact Clip ID Consistency",
            status=QACheckStatus.PASS if ids_match else QACheckStatus.FAIL,
            severity=QASeverity.CRITICAL,
            measured_value=clip_id,
            expected_value=clip_id,
            message="All artifacts reference consistent clip_id" if ids_match else "Mismatched clip IDs across pipeline artifacts",
        )
    )

    # 2. Duration Metadata Consistency
    if selected_clip and render_output:
        dur_diff = abs(selected_clip.candidate.duration - render_output.duration_seconds)
        is_dur_consistent = dur_diff <= 0.5
        checks.append(
            QACheck(
                check_id="artifact_duration_consistency",
                name="Candidate vs Render Duration Consistency",
                status=QACheckStatus.PASS if is_dur_consistent else QACheckStatus.WARN,
                severity=QASeverity.WARNING,
                measured_value=round(render_output.duration_seconds, 2),
                expected_value=round(selected_clip.candidate.duration, 2),
                message=f"Candidate duration ({selected_clip.candidate.duration:.2f}s) vs Render duration ({render_output.duration_seconds:.2f}s)",
            )
        )

    return checks
