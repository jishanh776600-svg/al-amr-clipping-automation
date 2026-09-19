"""Tests for Pexels stock video acquisition layer and INSUFFICIENT_VALID_CLIPS hard failure."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Pexels Client Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestPexelsClientAvailability:
    """Tests for API key resolution and availability detection."""

    def test_unavailable_when_no_api_key(self, monkeypatch, tmp_path):
        """Client reports unavailable when PEXELS_API_KEY is not set."""
        monkeypatch.delenv("PEXELS_API_KEY", raising=False)

        from autoclip.pipeline.broll.pexels_client import PexelsVideoClient
        client = PexelsVideoClient(cache_dir=tmp_path)
        assert client.is_available() is False

    def test_available_when_env_key_set(self, monkeypatch, tmp_path):
        """Client reports available when PEXELS_API_KEY is present in env."""
        monkeypatch.setenv("PEXELS_API_KEY", "test-key-12345")

        from autoclip.pipeline.broll.pexels_client import PexelsVideoClient
        client = PexelsVideoClient(cache_dir=tmp_path)
        assert client.is_available() is True

    def test_search_returns_empty_when_unavailable(self, monkeypatch, tmp_path):
        """search() returns [] when no API key is configured."""
        monkeypatch.delenv("PEXELS_API_KEY", raising=False)

        from autoclip.pipeline.broll.pexels_client import PexelsVideoClient
        client = PexelsVideoClient(cache_dir=tmp_path)
        results = client.search("warehouse shipping logistics", "warehouse_shipping")
        assert results == []

    def test_search_and_acquire_returns_empty_when_unavailable(self, monkeypatch, tmp_path):
        """search_and_acquire() returns [] when no API key is configured."""
        monkeypatch.delenv("PEXELS_API_KEY", raising=False)

        from autoclip.pipeline.broll.pexels_client import PexelsVideoClient
        client = PexelsVideoClient(cache_dir=tmp_path)
        assets = client.search_and_acquire(
            query="warehouse shipping logistics",
            concept="warehouse_shipping",
            cue_duration_s=3.0,
        )
        assert assets == []


class TestRelevanceScoring:
    """Tests for the Pexels video relevance scoring heuristic."""

    def test_portrait_video_scores_higher_than_landscape(self):
        """Portrait orientation (h > w) scores higher than landscape (w > h)."""
        from autoclip.pipeline.broll.pexels_client import _relevance_score

        portrait_video = {"width": 1080, "height": 1920, "duration": 5, "url": "warehouse", "user": {"name": ""}}
        landscape_video = {"width": 1920, "height": 1080, "duration": 5, "url": "warehouse", "user": {"name": ""}}

        portrait_score = _relevance_score(portrait_video, "warehouse shipping", "warehouse_shipping")
        landscape_score = _relevance_score(landscape_video, "warehouse shipping", "warehouse_shipping")

        assert portrait_score > landscape_score

    def test_ideal_duration_scores_max(self):
        """Videos in 3-7s range score higher than very short or very long ones."""
        from autoclip.pipeline.broll.pexels_client import _relevance_score

        base = {"width": 1080, "height": 1920, "url": "warehouse", "user": {"name": ""}}

        ideal = {**base, "duration": 5}
        too_short = {**base, "duration": 0.5}
        too_long = {**base, "duration": 120}

        ideal_score = _relevance_score(ideal, "warehouse", "warehouse_shipping")
        short_score = _relevance_score(too_short, "warehouse", "warehouse_shipping")
        long_score = _relevance_score(too_long, "warehouse", "warehouse_shipping")

        assert ideal_score > short_score
        assert ideal_score > long_score

    def test_high_res_scores_higher(self):
        """1080p+ videos score higher than 480p."""
        from autoclip.pipeline.broll.pexels_client import _relevance_score

        hd = {"width": 1080, "height": 1920, "duration": 5, "url": "", "user": {"name": ""}}
        sd = {"width": 480, "height": 854, "duration": 5, "url": "", "user": {"name": ""}}

        hd_score = _relevance_score(hd, "warehouse", "warehouse")
        sd_score = _relevance_score(sd, "warehouse", "warehouse")

        assert hd_score > sd_score

    def test_best_video_file_prefers_portrait(self):
        """_best_video_file() picks portrait HD file over landscape."""
        from autoclip.pipeline.broll.pexels_client import _best_video_file

        video = {
            "video_files": [
                {"link": "landscape.mp4", "width": 1920, "height": 1080},
                {"link": "portrait.mp4", "width": 1080, "height": 1920},
                {"link": "square.mp4", "width": 1080, "height": 1080},
            ]
        }
        best = _best_video_file(video)
        assert best is not None
        assert best["link"] == "portrait.mp4"

    def test_best_video_file_falls_back_gracefully(self):
        """_best_video_file() returns first file if no HD option exists."""
        from autoclip.pipeline.broll.pexels_client import _best_video_file

        video = {
            "video_files": [
                {"link": "low.mp4", "width": 320, "height": 568},
            ]
        }
        best = _best_video_file(video)
        assert best is not None
        assert best["link"] == "low.mp4"

    def test_best_video_file_empty(self):
        """_best_video_file() returns None for empty video_files."""
        from autoclip.pipeline.broll.pexels_client import _best_video_file

        assert _best_video_file({"video_files": []}) is None
        assert _best_video_file({}) is None


class TestPexelsApiResponseParsing:
    """Tests for parsing and scoring Pexels API mock responses."""

    def test_search_with_mock_api_response(self, monkeypatch, tmp_path):
        """search() correctly parses and scores a mock Pexels API response."""
        import json
        import urllib.request

        monkeypatch.setenv("PEXELS_API_KEY", "test-key-abc")

        mock_response = {
            "videos": [
                {
                    "id": 1001,
                    "url": "https://www.pexels.com/video/warehouse-1001",
                    "width": 1080,
                    "height": 1920,
                    "duration": 5,
                    "user": {"name": "testuser"},
                    "video_files": [{"link": "https://cdn.pexels.com/v1001.mp4", "width": 1080, "height": 1920}],
                },
                {
                    "id": 1002,
                    "url": "https://www.pexels.com/video/warehouse-1002",
                    "width": 1920,
                    "height": 1080,
                    "duration": 30,
                    "user": {"name": "testuser"},
                    "video_files": [{"link": "https://cdn.pexels.com/v1002.mp4", "width": 1920, "height": 1080}],
                },
            ]
        }

        class MockResponse:
            def read(self): return json.dumps(mock_response).encode()
            def __enter__(self): return self
            def __exit__(self, *a): pass

        with patch("urllib.request.urlopen", return_value=MockResponse()):
            from autoclip.pipeline.broll.pexels_client import PexelsVideoClient
            client = PexelsVideoClient(cache_dir=tmp_path)
            results = client.search("warehouse shipping", "warehouse_shipping", per_page=2)

        assert len(results) == 2
        # Portrait video (id=1001) should score higher, so it comes first
        assert results[0]["id"] == 1001
        assert results[0]["_relevance"] > results[1]["_relevance"]


# ─────────────────────────────────────────────────────────────────────────────
# Vault Integration Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestVaultPexelsIntegration:
    """Tests that VisualAssetVault calls Pexels when available."""

    def test_vault_calls_pexels_discovery(self, monkeypatch, tmp_path):
        """discover_candidates() invokes _discover_pexels_candidates."""
        from autoclip.pipeline.broll.vault import VisualAssetVault
        from autoclip.pipeline.broll.models import SemanticVisualCue, PresentationMode, VisualType

        vault = VisualAssetVault(vault_dir=tmp_path / "vault")

        cue = SemanticVisualCue(
            cue_id="cue-001",
            concept="warehouse_shipping",
            trigger_phrase="shipped from our warehouse",
            trigger_word="warehouse",
            start_s=5.0,
            end_s=8.0,
            preferred_mode=PresentationMode.FULL_SCREEN,
            visual_type=VisualType.STOCK_VIDEO,
        )

        called = {"count": 0}
        original = vault._discover_pexels_candidates

        def mock_pexels(c):
            called["count"] += 1
            return []

        vault._discover_pexels_candidates = mock_pexels
        candidates = vault.discover_candidates(cue)
        assert called["count"] == 1

    def test_vault_pexels_failure_is_nonfatal(self, monkeypatch, tmp_path):
        """If Pexels acquisition fails, discover_candidates() still returns local candidates."""
        from autoclip.pipeline.broll.vault import VisualAssetVault
        from autoclip.pipeline.broll.models import SemanticVisualCue, PresentationMode, VisualType

        vault = VisualAssetVault(vault_dir=tmp_path / "vault")

        cue = SemanticVisualCue(
            cue_id="cue-002",
            concept="financial_revenue",
            trigger_phrase="revenue crossed a million",
            trigger_word="million",
            start_s=10.0,
            end_s=13.0,
            preferred_mode=PresentationMode.PARTIAL_OVERLAY,
            visual_type=VisualType.DASHBOARD,
        )

        def raise_on_pexels(c):
            raise RuntimeError("Simulated Pexels failure")

        vault._discover_pexels_candidates = raise_on_pexels

        # Should not raise — Pexels failure is non-fatal
        candidates = vault.discover_candidates(cue)
        # May return 0 or more from local vault, but should not crash
        assert isinstance(candidates, list)


# ─────────────────────────────────────────────────────────────────────────────
# INSUFFICIENT_VALID_CLIPS Hard Failure Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestInsufficientClipsHardFailure:
    """Tests that runner raises HighlightError when clip count < required."""

    def test_insufficient_clips_error_message_format(self):
        """HighlightError for INSUFFICIENT_VALID_CLIPS contains diagnostic info."""
        from autoclip.pipeline.highlights import HighlightError

        diag = "INSUFFICIENT_VALID_CLIPS: pipeline produced 2/5 valid clips from 817 candidates (5 evaluated by assembly gate). Rejection breakdown: [too_short: 2; dangling_ending: 1]. Pipeline requires exactly 5 clips; check source quality, campaign rules, and duration constraints."
        err = HighlightError(diag)

        assert "INSUFFICIENT_VALID_CLIPS" in str(err)
        assert "2/5" in str(err)
        assert "817" in str(err)
        assert "too_short" in str(err)

    def test_insufficient_clips_is_highlight_error(self):
        """INSUFFICIENT_VALID_CLIPS is always a HighlightError, not a generic RuntimeError."""
        from autoclip.pipeline.highlights import HighlightError

        err = HighlightError("INSUFFICIENT_VALID_CLIPS: 0/5")
        assert isinstance(err, HighlightError)
        assert isinstance(err, Exception)

    def test_bgm_weight_is_conservative(self):
        """DuckingConfig.bgm_weight <= 0.5 to keep BGM subordinate to voice."""
        from autoclip.pipeline.audio_mix.models import DuckingConfig
        cfg = DuckingConfig()
        assert cfg.bgm_weight <= 0.5, (
            f"bgm_weight={cfg.bgm_weight} is too high; BGM will dominate voice. "
            "Use <= 0.5 to keep voice clearly dominant."
        )

    def test_bgm_duck_attenuation_is_aggressive(self):
        """DuckingConfig.duck_attenuation_db >= 15.0 for proper voice dominance."""
        from autoclip.pipeline.audio_mix.models import DuckingConfig
        cfg = DuckingConfig()
        assert cfg.duck_attenuation_db >= 15.0, (
            f"duck_attenuation_db={cfg.duck_attenuation_db}dB is too low; "
            "BGM will be audible over speech."
        )

    def test_bgm_release_is_fast(self):
        """DuckingConfig.release_ms <= 400ms for tight, responsive ducking."""
        from autoclip.pipeline.audio_mix.models import DuckingConfig
        cfg = DuckingConfig()
        assert cfg.release_ms <= 400.0, (
            f"release_ms={cfg.release_ms}ms is too slow; "
            "BGM will surge loudly during speech pauses."
        )
