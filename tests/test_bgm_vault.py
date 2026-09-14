"""Focused tests for Step 20: BGM Vault + Operator-Selected Campaign Music."""

from __future__ import annotations

import io
import wave
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autoclip import app as app_module
from autoclip.app import create_app
from autoclip.bgm import (
    BGMCorruptAudioError,
    BGMFileSizeError,
    BGMUnavailableError,
    BGMUnsupportedFormatError,
    BGMUploadMetadata,
    BGMValidationError,
    BGMVault,
    clean_tags,
    clean_text,
    infer_title,
)
from autoclip.db import connection, migrate, store
from autoclip.db.models import BGMAssetRecord, Job, Source, new_id
from autoclip.db.schema import SCHEMA_VERSION


def make_valid_wav(duration_s: float = 1.0, sample_rate: int = 44100) -> bytes:
    """Generate a clean uncompressed PCM WAV in memory."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        n_frames = int(duration_s * sample_rate)
        wav.writeframes(b"\x00\x00" * n_frames)
    return buf.getvalue()


@pytest.fixture
def client(autoclip_home, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv(app_module.ENV_NO_WORKER, "1")
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def source() -> Source:
    return store.create_source(
        Source(
            id=new_id(),
            type="upload",
            path="C:/media/video.mp4",
            title="Test Source",
            duration_s=120.0,
            width=1920,
            height=1080,
            fps=30.0,
        )
    )


class TestBGMMigrationAndSchema:
    def test_migration_v15_creates_table_and_indexes(self, autoclip_home):
        with connection() as conn:
            v = migrate(conn)
            assert v == 15
            assert SCHEMA_VERSION == 15

            # Table existence and column verification
            cols = conn.execute("PRAGMA table_info(bgm_assets)").fetchall()
            col_names = [c[1] for c in cols]
            expected = [
                "id",
                "name",
                "file_path",
                "genre",
                "mood",
                "tags",
                "mime_type",
                "duration_s",
                "file_size_bytes",
                "enabled",
                "created_at",
                "updated_at",
            ]
            for exp in expected:
                assert exp in col_names, f"Missing column {exp}"

            indexes = conn.execute("PRAGMA index_list(bgm_assets)").fetchall()
            index_names = [idx[1] for idx in indexes]
            assert "idx_bgm_assets_enabled" in index_names


class TestBGMVaultValidationAndStorage:
    def test_register_valid_audio_creates_asset(self, initialised_db):
        vault = BGMVault()
        wav_bytes = make_valid_wav(duration_s=2.5)

        meta = BGMUploadMetadata(
            name="Epic Orchestral Hook",
            genre="Cinematic",
            mood="Heroic",
            tags=["epic", "trailer", "strings"],
        )
        record = vault.register_asset(
            source=wav_bytes,
            filename="epic_orchestral_track.wav",
            metadata=meta,
        )

        assert record.id
        assert record.name == "Epic Orchestral Hook"
        assert record.genre == "Cinematic"
        assert record.mood == "Heroic"
        assert record.tags == ["epic", "trailer", "strings"]
        assert record.mime_type == "audio/wav"
        assert record.duration_s >= 2.4
        assert record.file_size_bytes == len(wav_bytes)
        assert record.enabled is True
        assert Path(record.file_path).exists()

        # Check DB fetch
        fetched = vault.get_asset(record.id)
        assert fetched is not None
        assert fetched.id == record.id
        assert fetched.name == record.name

    def test_infer_title_from_filename_when_omitted(self, initialised_db):
        vault = BGMVault()
        wav_bytes = make_valid_wav(duration_s=1.0)
        record = vault.register_asset(
            source=wav_bytes,
            filename="motivational_speech_background.wav",
            metadata=None,
        )
        assert record.name == "Motivational Speech Background"

    def test_reject_unsupported_format(self, initialised_db):
        vault = BGMVault()
        with pytest.raises(BGMUnsupportedFormatError) as exc_info:
            vault.register_asset(
                source=b"fake-content-bytes",
                filename="music.txt",
            )
        assert "Unsupported audio extension" in str(exc_info.value)

    def test_reject_empty_or_too_small_file(self, initialised_db):
        vault = BGMVault()
        with pytest.raises(BGMFileSizeError) as exc_info:
            vault.register_asset(
                source=b"",
                filename="empty.wav",
            )
        assert "empty or too small" in str(exc_info.value)

    def test_reject_corrupt_audio_bytes(self, initialised_db):
        vault = BGMVault()
        corrupt_bytes = b"RIFF" + b"\x00" * 500  # Invalid header/frames
        with pytest.raises(BGMCorruptAudioError) as exc_info:
            vault.register_asset(
                source=corrupt_bytes,
                filename="corrupt.wav",
            )
        assert "Could not read audio stream" in str(exc_info.value)

    def test_multiple_bgms_coexist_without_collision(self, initialised_db):
        vault = BGMVault()
        wav_bytes1 = make_valid_wav(duration_s=1.0)
        wav_bytes2 = make_valid_wav(duration_s=1.5)

        asset1 = vault.register_asset(wav_bytes1, "track1.wav", BGMUploadMetadata(name="Track 1"))
        asset2 = vault.register_asset(wav_bytes2, "track2.wav", BGMUploadMetadata(name="Track 2"))

        assert asset1.id != asset2.id
        assert asset1.file_path != asset2.file_path
        assert Path(asset1.file_path).exists()
        assert Path(asset2.file_path).exists()

        all_assets = vault.list_assets()
        assert len(all_assets) >= 2
        ids = [a.id for a in all_assets]
        assert asset1.id in ids
        assert asset2.id in ids


class TestBGMVaultLifecycleAndManagement:
    def test_enable_disable_and_filter(self, initialised_db):
        vault = BGMVault()
        wav = make_valid_wav(duration_s=1.0)
        asset1 = vault.register_asset(wav, "a1.wav", BGMUploadMetadata(genre="LoFi"))
        asset2 = vault.register_asset(wav, "a2.wav", BGMUploadMetadata(genre="Rock"))

        # Disable asset2
        updated = vault.update_asset(asset2.id, enabled=False)
        assert updated is not None
        assert updated.enabled is False

        # list enabled only
        enabled_list = vault.list_assets(enabled_only=True)
        enabled_ids = [a.id for a in enabled_list]
        assert asset1.id in enabled_ids
        assert asset2.id not in enabled_ids

        # list by genre
        lofi_list = vault.list_assets(genre="lofi")
        assert len(lofi_list) == 1
        assert lofi_list[0].id == asset1.id

    def test_delete_asset_removes_db_and_file(self, initialised_db):
        vault = BGMVault()
        wav = make_valid_wav(duration_s=1.0)
        asset = vault.register_asset(wav, "delete_me.wav", BGMUploadMetadata(name="Temporary"))
        path = Path(asset.file_path)
        assert path.exists()

        deleted = vault.delete_asset(asset.id)
        assert deleted is True
        assert not path.exists()
        assert vault.get_asset(asset.id) is None


class TestCampaignBGMSelectionAndPropagation:
    def test_resolve_campaign_bgm_none(self, initialised_db):
        vault = BGMVault()
        for opt in (None, "", "none", "NONE", "null", "false", "no"):
            enabled, asset, path = vault.resolve_campaign_bgm(opt)
            assert enabled is False
            assert asset is None
            assert path is None

    def test_resolve_campaign_bgm_valid(self, initialised_db):
        vault = BGMVault()
        wav = make_valid_wav(duration_s=1.2)
        asset = vault.register_asset(wav, "ambient.wav", BGMUploadMetadata(name="Ambient Flow"))

        enabled, resolved_asset, path = vault.resolve_campaign_bgm(asset.id)
        assert enabled is True
        assert resolved_asset is not None
        assert resolved_asset.id == asset.id
        assert resolved_asset.name == "Ambient Flow"
        assert path is not None
        assert path.exists()

    def test_resolve_campaign_bgm_disabled_raises(self, initialised_db):
        vault = BGMVault()
        wav = make_valid_wav(duration_s=1.0)
        asset = vault.register_asset(wav, "disabled.wav", BGMUploadMetadata(name="Disabled Track"))
        vault.update_asset(asset.id, enabled=False)

        with pytest.raises(BGMUnavailableError) as exc_info:
            vault.resolve_campaign_bgm(asset.id)
        assert "currently disabled" in str(exc_info.value)

    def test_resolve_campaign_bgm_missing_file_raises(self, initialised_db):
        vault = BGMVault()
        wav = make_valid_wav(duration_s=1.0)
        asset = vault.register_asset(wav, "missing_file.wav", BGMUploadMetadata(name="Missing Track"))
        Path(asset.file_path).unlink()

        with pytest.raises(BGMUnavailableError) as exc_info:
            vault.resolve_campaign_bgm(asset.id)
        assert "was not found on disk" in str(exc_info.value)


class TestBGMAPIEndpoints:
    def test_api_upload_list_get_update_delete(self, client: TestClient):
        wav_bytes = make_valid_wav(duration_s=1.5)

        # 1. POST /api/bgm
        resp = client.post(
            "/api/bgm",
            files={"file": ("podcast_intro.wav", wav_bytes, "audio/wav")},
            data={
                "name": "Podcast Intro Theme",
                "genre": "Podcast",
                "mood": "Calm",
                "tags": "interview, minimal, intro",
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        asset_id = data["id"]
        assert data["name"] == "Podcast Intro Theme"
        assert data["genre"] == "Podcast"
        assert data["mood"] == "Calm"
        assert data["tags"] == ["interview", "minimal", "intro"]
        assert data["duration_s"] >= 1.4

        # 2. GET /api/bgm
        list_resp = client.get("/api/bgm")
        assert list_resp.status_code == 200
        items = list_resp.json()
        assert any(i["id"] == asset_id for i in items)

        # 3. GET /api/bgm/{id}
        get_resp = client.get(f"/api/bgm/{asset_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == asset_id

        # 4. PATCH /api/bgm/{id}
        patch_resp = client.patch(
            f"/api/bgm/{asset_id}",
            json={"name": "Renamed Podcast Intro", "enabled": False},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["name"] == "Renamed Podcast Intro"
        assert patch_resp.json()["enabled"] is False

        # 5. GET /api/bgm?enabled_only=true should now exclude it
        filtered_resp = client.get("/api/bgm?enabled_only=true")
        assert filtered_resp.status_code == 200
        assert not any(i["id"] == asset_id for i in filtered_resp.json())

        # 6. DELETE /api/bgm/{id}
        del_resp = client.delete(f"/api/bgm/{asset_id}")
        assert del_resp.status_code == 200
        assert del_resp.json()["status"] == "deleted"

        # 7. GET /api/bgm/{id} is 404
        get_after_del = client.get(f"/api/bgm/{asset_id}")
        assert get_after_del.status_code == 404

    def test_job_creation_with_bgm_selection(self, client: TestClient, source: Source):
        vault = BGMVault()
        wav_bytes = make_valid_wav(duration_s=2.0)
        asset = vault.register_asset(
            wav_bytes, "cinematic.wav", BGMUploadMetadata(name="Cinematic Strings")
        )

        # Create job with bgm_asset_id
        resp = client.post(
            "/api/jobs",
            json={
                "source_id": source.id,
                "settings": {
                    "bgm_asset_id": asset.id,
                    "caption_style": "classic_professional",
                },
            },
        )
        assert resp.status_code == 201, resp.text
        job_data = resp.json()
        job_id = job_data["id"]

        job = store.get_job(job_id)
        assert job is not None
        assert job.settings["bgm_enabled"] is True
        assert job.settings["bgm_asset_id"] == asset.id
        assert job.settings["bgm_asset_name"] == "Cinematic Strings"
        assert job.settings["bgm_asset_path"] == str(asset.file_path)

    def test_job_creation_with_no_bgm_backward_compatible(self, client: TestClient, source: Source):
        # Create job with no BGM specified
        resp = client.post(
            "/api/jobs",
            json={
                "source_id": source.id,
                "settings": {},
            },
        )
        assert resp.status_code == 201, resp.text
        job_data = resp.json()
        job_id = job_data["id"]

        job = store.get_job(job_id)
        assert job is not None
        assert job.settings["bgm_enabled"] is False
        assert job.settings.get("bgm_asset_id") is None
