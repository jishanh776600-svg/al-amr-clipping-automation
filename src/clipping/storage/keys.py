"""Deterministic logical storage key builder for vault organization."""

import re


class StorageKeyBuilder:
    """Generates portable, deterministic logical storage keys."""

    @staticmethod
    def _sanitize(identifier: str) -> str:
        """Sanitizes identifier to prevent directory traversal and invalid characters."""
        return re.sub(r"[^a-zA-Z0-9_\-]", "_", identifier)

    @classmethod
    def campaign_raw_pdf(cls, campaign_id: str) -> str:
        return f"campaigns/{cls._sanitize(campaign_id)}/raw_spec.pdf"

    @classmethod
    def campaign_spec_json(cls, campaign_id: str) -> str:
        return f"campaigns/{cls._sanitize(campaign_id)}/campaign_spec.json"

    @classmethod
    def source_master_video(cls, source_video_id: str, ext: str = "mp4") -> str:
        clean_ext = ext.lstrip(".")
        return f"sources/{cls._sanitize(source_video_id)}/master.{clean_ext}"

    @classmethod
    def source_audio_wav(cls, source_video_id: str) -> str:
        return f"sources/{cls._sanitize(source_video_id)}/audio.wav"

    @classmethod
    def source_transcript_json(cls, source_video_id: str) -> str:
        return f"sources/{cls._sanitize(source_video_id)}/transcript.json"

    @classmethod
    def source_diarization_json(cls, source_video_id: str) -> str:
        return f"sources/{cls._sanitize(source_video_id)}/diarization.json"

    @classmethod
    def source_scenes_json(cls, source_video_id: str) -> str:
        return f"sources/{cls._sanitize(source_video_id)}/scenes.json"

    @classmethod
    def clip_candidate_json(cls, clip_id: str) -> str:
        return f"candidates/{cls._sanitize(clip_id)}/candidate.json"

    @classmethod
    def clip_reframe_plan_json(cls, clip_id: str) -> str:
        return f"clips/{cls._sanitize(clip_id)}/reframe_plan.json"

    @classmethod
    def clip_subtitles_ass(cls, clip_id: str) -> str:
        return f"clips/{cls._sanitize(clip_id)}/subtitles.ass"

    @classmethod
    def clip_rendered_mp4(cls, clip_id: str) -> str:
        return f"clips/{cls._sanitize(clip_id)}/final_1080x1920.mp4"

    @classmethod
    def clip_thumbnail_jpg(cls, clip_id: str) -> str:
        return f"clips/{cls._sanitize(clip_id)}/thumbnail.jpg"

    @classmethod
    def clip_qa_report_json(cls, clip_id: str) -> str:
        return f"clips/{cls._sanitize(clip_id)}/qa_report.json"
