import os
import shutil
import tempfile
import json
import io
import wave
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

from autoclip import paths, db
from autoclip.db import store, models
from autoclip.security.vault import CredentialVault, get_vault
from autoclip.bgm.vault import BGMVault
from autoclip.bgm import BGMUploadMetadata
from autoclip.config import ExportSettings
from autoclip.telegram.review_bot import handle_telegram_update, is_user_authorized


def make_test_wav(duration_s: float = 1.0, sample_rate: int = 44100) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        n_frames = int(duration_s * sample_rate)
        wav.writeframes(b"\x00\x00" * n_frames)
    return buf.getvalue()


def test_vault_key_rotation_and_self_healing(autoclip_home: Path, initialised_db: int, monkeypatch: pytest.MonkeyPatch):
    """Verify that credentials survive Render key regeneration via multi-candidate self-healing."""
    key_file = autoclip_home / ".master_key"
    
    # 1. Store secret under key1
    key1 = "alamr-master-key-original-1111"
    monkeypatch.setenv("AL_AMR_MASTER_KEY", key1)
    vault1 = CredentialVault()
    vault1.store_secret("github_token", "ghp_secret1234567890abcdef")
    assert vault1.retrieve_secret("github_token") == "ghp_secret1234567890abcdef"

    # 2. Simulate Render redeploy / key regeneration:
    # Key1 remains in .master_key file or candidate keys, but environment has key2
    key_file.write_text(key1, encoding="utf-8")
    key2 = "alamr-master-key-regenerated-2222"
    monkeypatch.setenv("AL_AMR_MASTER_KEY", key2)

    # Force a vault instance that uses key2 as explicit active key
    vault2 = CredentialVault(master_key=key2)
    
    # Retrieve secret: vault2 active cipher fails with key2, searches candidate keys, finds key1 in key_file,
    # decrypts the secret, re-encrypts with key2, and saves to DB!
    val = vault2.retrieve_secret("github_token")
    assert val == "ghp_secret1234567890abcdef"

    # 3. Verify self-healing: remove key_file, verify vault2 can now decrypt directly with key2
    key_file.unlink()
    val_healed = vault2.retrieve_secret("github_token")
    assert val_healed == "ghp_secret1234567890abcdef"


def test_bgm_sidecar_persistence_and_canonical_tracks(autoclip_home: Path, initialised_db: int):
    """Verify 4 canonical tracks are seeded and user tracks persist sidecars across DB reset."""
    bgm_dir = autoclip_home / "bgm"
    vault = BGMVault(base_dir=bgm_dir)
    assets = vault.list_assets()

    # Verify all 4 canonical tracks exist in vault
    asset_names = [a.name for a in assets]
    assert "Ambient Flow" in asset_names
    assert "Upbeat Energy" in asset_names
    assert "Cinematic Horizon" in asset_names
    assert "Lo-Fi Chill" in asset_names

    # Verify sidecar json exists for all canonical tracks
    for a in assets:
        sidecar = bgm_dir / f"{a.id}.json"
        assert sidecar.exists(), f"Sidecar missing for {a.id}"

    # Register custom track e.g. "motivation"
    wav_bytes = make_test_wav(duration_s=2.0)
    meta = BGMUploadMetadata(
        name="motivation",
        genre="Cinematic",
        mood="Inspirational",
        tags=["epic", "drums", "drive"],
    )
    custom_record = vault.register_asset(
        source=wav_bytes,
        filename="motivation.wav",
        metadata=meta,
    )
    assert custom_record.name == "motivation"

    # Verify sidecar written to disk
    custom_sidecar = bgm_dir / f"{custom_record.id}.json"
    assert custom_sidecar.exists()
    sidecar_data = json.loads(custom_sidecar.read_text(encoding="utf-8"))
    assert sidecar_data["name"] == "motivation"
    assert "epic" in sidecar_data["tags"]

    # Wipe SQLite table to simulate DB recreation
    with db.connection() as conn:
        conn.execute("DELETE FROM bgm_assets")

    # Verify list_assets is empty in memory
    assert len(store.list_bgm_assets()) == 0

    # Create new BGMVault instance pointing to the same folder - reconcile_vault restores assets from sidecars
    restored_vault = BGMVault(base_dir=bgm_dir)
    restored_assets = restored_vault.list_assets()
    restored_custom = next((a for a in restored_assets if a.id == custom_record.id), None)
    assert restored_custom is not None
    assert restored_custom.name == "motivation"
    assert "epic" in restored_custom.tags


def test_export_settings_selection_persistence():
    """Verify ExportSettings supports visual_filter and bgm_asset_id with defaults."""
    settings = ExportSettings(
        caption_style="bold_center",
        visual_filter="clean_vibrant",
        bgm_asset_id="canonical_upbeat"
    )
    assert settings.caption_style == "bold_center"
    assert settings.visual_filter == "clean_vibrant"
    assert settings.bgm_asset_id == "canonical_upbeat"

    dump = settings.model_dump()
    assert dump["caption_style"] == "bold_center"
    assert dump["visual_filter"] == "clean_vibrant"
    assert dump["bgm_asset_id"] == "canonical_upbeat"


def test_telegram_authorization_check():
    """Verify authorization allows numeric ID and username (with/without @, case-insensitive)."""
    allowed_ids = ["12345", "@operator_user", "67890"]

    # Numeric matches
    assert is_user_authorized(12345, allowed_ids, None) is True
    assert is_user_authorized("12345", allowed_ids, None) is True
    assert is_user_authorized(67890, allowed_ids, "other") is True

    # Username matches
    assert is_user_authorized(99999, allowed_ids, "operator_user") is True
    assert is_user_authorized(99999, allowed_ids, "@operator_user") is True
    assert is_user_authorized(99999, allowed_ids, "Operator_User") is True

    # Unauthorized
    assert is_user_authorized(99999, allowed_ids, "stranger") is False
    assert is_user_authorized(99999, allowed_ids, None) is False


@pytest.mark.asyncio
async def test_telegram_webhook_callback_handling(autoclip_home: Path, initialised_db: int, monkeypatch: pytest.MonkeyPatch):
    """Verify Telegram callback updates are acknowledged and authorized."""
    import autoclip.telegram.review_bot as rb

    mock_answer = AsyncMock(return_value=True)
    monkeypatch.setattr(rb, "_answer_callback_query", mock_answer)
    monkeypatch.setattr(rb, "_safe_send_telegram_message", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(rb, "_safe_edit_telegram_message", AsyncMock(return_value=True))

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123456, @test_operator")

    src = store.create_source(models.Source(id="src_1", type="upload", path="test.mp4", duration_s=10.0))
    job = store.create_job(models.Job(id="job_test_123", source_id=src.id))
    clip = store.create_clip(models.Clip(id="clip_test_123", job_id=job.id, rank=1, start_s=0.0, end_s=5.0))

    approval = models.ClipApprovalRecord(
        id=models.new_id(),
        clip_id=clip.id,
        job_id=job.id,
        current_status="PENDING_REVIEW",
        version=1,
        publish_eligible=True,
        blocking_reasons=[],
        created_at=models.utcnow(),
        updated_at=models.utcnow(),
    )
    store.create_clip_approval(approval)

    # 1. Test unauthorized user callback
    unauth_payload = {
        "update_id": 1001,
        "callback_query": {
            "id": "cb_query_unauth",
            "from": {"id": 999999, "username": "unauthorized_hacker"},
            "message": {"message_id": 42, "chat": {"id": 123456}},
            "data": f"tg:appr:{clip.id}"
        }
    }
    res_unauth = await handle_telegram_update(unauth_payload)
    assert res_unauth.get("status") == "unauthorized"
    mock_answer.assert_called()
    assert "Unauthorized" in mock_answer.call_args[1].get("text", "")

    mock_answer.reset_mock()

    # 2. Test authorized user approval callback
    auth_payload = {
        "update_id": 1002,
        "callback_query": {
            "id": "cb_query_auth",
            "from": {"id": 123456, "username": "test_operator"},
            "message": {"message_id": 42, "chat": {"id": 123456}},
            "data": f"tg:appr:{clip.id}"
        }
    }
    res_auth = await handle_telegram_update(auth_payload)
    assert res_auth.get("status") == "approved"
    mock_answer.assert_called()
    assert "Approval received" in mock_answer.call_args[1].get("text", "")
