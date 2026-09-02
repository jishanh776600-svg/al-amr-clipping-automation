"""Unit tests for StorageKeyBuilder."""

from clipping.storage.keys import StorageKeyBuilder


def test_storage_key_generation():
    assert StorageKeyBuilder.campaign_raw_pdf("CAMP_01") == "campaigns/CAMP_01/raw_spec.pdf"
    assert StorageKeyBuilder.campaign_spec_json("CAMP_01") == "campaigns/CAMP_01/campaign_spec.json"
    assert StorageKeyBuilder.source_master_video("VID_01", "mp4") == "sources/VID_01/master.mp4"
    assert StorageKeyBuilder.source_audio_wav("VID_01") == "sources/VID_01/audio.wav"
    assert StorageKeyBuilder.source_transcript_json("VID_01") == "sources/VID_01/transcript.json"
    assert StorageKeyBuilder.clip_rendered_mp4("CLIP_01") == "clips/CLIP_01/final_1080x1920.mp4"
    assert StorageKeyBuilder.clip_subtitles_ass("CLIP_01") == "clips/CLIP_01/subtitles.ass"
    assert StorageKeyBuilder.clip_thumbnail_jpg("CLIP_01") == "clips/CLIP_01/thumbnail.jpg"


def test_storage_key_sanitization():
    unsafe_id = "../evil_id/../test!@#"
    safe_key = StorageKeyBuilder.campaign_raw_pdf(unsafe_id)
    assert ".." not in safe_key
    assert "evil_id" in safe_key
    assert safe_key.startswith("campaigns/")
