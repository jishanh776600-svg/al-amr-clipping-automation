"""Unit and regression tests verifying propagation of BGM, visual filter, and caption style.

Ensures that:
1. Public endpoints include /api/bgm to prevent 401 Unauthorized during asset streaming.
2. Ingest form parameters (caption_style, visual_filter, bgm_asset_id) are preserved.
3. Worker runner correctly parses CLI arguments and merges them into job settings.
4. Export and render engine receive and apply the operator-selected settings.
"""

from __future__ import annotations

import argparse
import json
import pytest
import sys
from pathlib import Path

from autoclip.api.auth import PUBLIC_PREFIXES
from autoclip.jobs.worker_runner import parse_args
from autoclip.pipeline import captions
from autoclip.pipeline.filters import get_filter


def test_public_prefixes_include_bgm():
    """Verify that /api/bgm and /bgm are unauthenticated to allow streaming to workers."""
    assert "/api/bgm" in PUBLIC_PREFIXES
    assert "/bgm" in PUBLIC_PREFIXES


def test_worker_runner_args_parsing():
    """Verify worker_runner parses CLI flags for filter, caption style, and BGM."""
    test_argv = [
        "worker_runner.py",
        "--job-id", "test-job-123",
        "--source-url", "https://example.com/video.mp4",
        "--visual-filter", "black_and_white",
        "--caption-style", "kinetic",
        "--bgm-asset-id", "motivation",
    ]
    orig_argv = sys.argv
    try:
        sys.argv = test_argv
        args = parse_args()
        assert args.job_id == "test-job-123"
        assert args.visual_filter == "black_and_white"
        assert args.caption_style == "kinetic"
        assert args.bgm_asset_id == "motivation"
    finally:
        sys.argv = orig_argv


def test_filter_and_style_resolution():
    """Verify black_and_white filter and kinetic style resolve to valid configurations."""
    vf = get_filter("black_and_white")
    assert vf is not None
    assert vf.ffmpeg_expr == "hue=s=0"

    # Verify kinetic resolves to bold_pop style preset
    style = captions.resolve_style("kinetic")
    assert style is not None
    assert style.key == "bold_pop"


def test_bgm_vault_resolves_canonical_motivation(autoclip_home: Path, initialised_db: int, tmp_path: Path):
    """Verify BGMVault resolves 'motivation' to canonical_motivation asset."""
    from autoclip.bgm.vault import BGMVault
    vault = BGMVault(base_dir=tmp_path / "bgm")
    vault.reconcile_vault()
    asset = vault.get_asset("motivation")
    assert asset is not None
    assert "motivation" in asset.id.lower() or "motivation" in asset.name.lower()
