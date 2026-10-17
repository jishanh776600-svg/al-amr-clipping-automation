"""Master Overeditor Orchestrator.

Coordinates:
- Brief inspection & compliance lock
- 100% AI B-Roll generation (FLUX / 3D Ken-Burns)
- Top multi-color hook headline + Bottom YouTube attribution badge
- Master FFmpeg overedit compilation
- Viral SEO pack generation
- Telegram delivery with 1-click Approval & Change Request feedback loop
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

try:
    from ...db import store
    from ...db.models import Clip, FinalRenderRecord
except Exception:
    try:
        from backend.autoclip.db import store
        from backend.autoclip.db.models import Clip, FinalRenderRecord
    except Exception:
        from autoclip.db import store
        from autoclip.db.models import Clip, FinalRenderRecord

from .broll_factory import AIBrollFactory
from .brief_inspector import parse_brief_compliance
from .seo_generator import SEOGenerator
from .template_engine import TemplateEngine

log = logging.getLogger(__name__)


class MasterOvereditorOrchestrator:
    """End-to-End Autonomous Creative Director & Overeditor."""

    def __init__(self):
        self.template_engine = TemplateEngine()
        self.broll_factory = AIBrollFactory()
        self.seo_generator = SEOGenerator()

    def process_job_clips(
        self,
        job_id: str,
        clips: list[Clip],
        transcript_text: str | None = None,
        brief_text: str | None = None,
    ) -> list[Path]:
        """Transform all 5 raw clips into Content Rewards winning master shorts."""
        job = store.get_job(job_id)
        job_settings = job.settings if job else {}

        # 1. Parse Brief and lock compliance rules
        compliance = parse_brief_compliance(brief_text, job_settings)
        log.info("Job %s: Locked attribution handle: %s", job_id, compliance.handle)

        overedited_paths: list[Path] = []

        for idx, clip in enumerate(clips):
            try:
                # Find the existing exported video
                exports = store.list_exports(clip.id)
                if not exports:
                    log.warning("No export found for clip %s, skipping overedit.", clip.id)
                    continue

                base_export = exports[0]
                base_video_path = Path(base_export.path)
                if not base_video_path.is_file():
                    log.warning("Base video file %s missing, skipping.", base_video_path)
                    continue

                work_dir = base_video_path.parent

                # Preserve clean base video before applying overlays
                clean_base = work_dir / f"clean_base_{clip.id}.mp4"
                if not clean_base.is_file():
                    try:
                        shutil.copy2(base_video_path, clean_base)
                    except Exception as cp_err:
                        log.debug("Could not cache clean_base: %s", cp_err)

                # 2. Generate Viral SEO Pack & Hook Headline
                slice_text = transcript_text or clip.title or "Viral Highlight"
                seo_pack = self.seo_generator.generate_seo_pack(
                    transcript_slice=slice_text,
                    compliance=compliance,
                    clip_index=idx + 1,
                )

                # Update metadata for Telegram review card and publishing
                clip_meta = store.get_clip_metadata(clip.id)
                if clip_meta:
                    clip_meta.telemetry = dict(clip_meta.telemetry or {})
                    clip_meta.telemetry["youtube"] = {
                        "title": seo_pack.yt_title,
                        "description": seo_pack.yt_description,
                        "hashtags": seo_pack.yt_tags,
                        "mentions": [compliance.handle],
                    }
                    clip_meta.telemetry["instagram"] = {
                        "caption": seo_pack.ig_caption,
                        "hashtags": seo_pack.ig_tags,
                        "mentions": [compliance.handle],
                    }
                    store.update_clip_metadata(clip_meta)

                # 3. Create Top Headline Banner PNG & Bottom Attribution Badge PNG
                headline_png = work_dir / f"headline_{clip.id}.png"
                badge_png = work_dir / f"badge_{clip.id}.png"

                self.template_engine.create_top_headline_png(
                    headline_text=seo_pack.hook_headline,
                    target_path=headline_png,
                )
                self.template_engine.create_bottom_attribution_png(
                    handle=compliance.handle,
                    target_path=badge_png,
                )

                # 4. Generate 100% AI B-Roll (1-2 clips per short)
                broll_clips = []
                broll_prompt = f"trading lifestyle, success, {compliance.topic or 'wealth mindset'}"
                broll_asset = self.broll_factory.generate_broll_clip(prompt=broll_prompt, duration_s=2.5)
                if broll_asset and broll_asset.video_path.is_file():
                    # Place B-roll at 00:08s to 00:10.5s into the clip
                    clip_duration = clip.end_s - clip.start_s
                    if clip_duration > 12.0:
                        broll_clips.append((8.0, 10.5, broll_asset.video_path))

                # 5. Composite Final Overedit Short (Remotion 60FPS Spring Engine with FFmpeg Fallback)
                master_out_path = work_dir / f"master_overedit_{clip.id}.mp4"
                rendered = None
                try:
                    from .remotion_bridge import RemotionBridge
                    rem_bridge = RemotionBridge()
                    if rem_bridge.is_available():
                        broll_first = broll_clips[0][2] if broll_clips else None
                        clip_dur = clip.end_s - clip.start_s
                        rendered = rem_bridge.render_content_rewards_short(
                            base_video_path=clean_base if clean_base.is_file() else base_video_path,
                            headline_text=seo_pack.hook_headline,
                            handle=compliance.handle,
                            output_path=master_out_path,
                            duration_s=clip_dur,
                            broll_path=broll_first,
                        )
                except Exception as r_err:
                    log.warning("Remotion render attempt error: %s, falling back to FFmpeg compositor", r_err)

                if not rendered or not master_out_path.is_file() or master_out_path.stat().st_size < 10000:
                    self.template_engine.composite_overedit_video(
                        base_video_path=clean_base if clean_base.is_file() else base_video_path,
                        headline_png=headline_png,
                        attribution_png=badge_png,
                        output_path=master_out_path,
                        broll_clips=broll_clips,
                    )

                # 6. Replace primary export path & final_render so website and bot serve the master short!
                if master_out_path.is_file() and master_out_path.stat().st_size > 10000:
                    base_export.path = str(master_out_path)
                    base_export.file_size_bytes = master_out_path.stat().st_size
                    store.update_export_path(base_export.id, str(master_out_path), master_out_path.stat().st_size)
                    
                    fr = store.get_final_render(clip.id)
                    if fr:
                        fr.output_path = str(master_out_path)
                        store.update_final_render(fr)
                    overedited_paths.append(master_out_path)

                # 7. Deliver to Telegram asynchronously
                try:
                    from ...telegram.review_bot import send_clip_review
                except Exception:
                    try:
                        from backend.autoclip.telegram.review_bot import send_clip_review
                    except Exception:
                        from autoclip.telegram.review_bot import send_clip_review

                try:
                    coro = send_clip_review(
                        job_id=job_id,
                        clip_id=clip.id,
                        force=True,
                        job_settings=job_settings,
                    )
                    from ...jobs.events import broker
                    loop = getattr(broker, "_loop", None)
                    if loop and loop.is_running():
                        asyncio.run_coroutine_threadsafe(coro, loop)
                    else:
                        try:
                            running_loop = asyncio.get_running_loop()
                            running_loop.create_task(coro)
                        except RuntimeError:
                            asyncio.run(coro)
                except Exception as tg_err:
                    log.warning("Could not send Telegram review card for clip %s: %s", clip.id, tg_err)

            except Exception as exc:
                log.error("Failed to overedit clip %s: %s", clip.id, exc, exc_info=True)

        return overedited_paths

    def revise_clip(
        self,
        job_id: str,
        clip_id: str,
        feedback: str,
    ) -> Path | None:
        """Apply human feedback from Telegram and re-composite the master short."""
        log.info("Revising clip %s with operator feedback: %s", clip_id, feedback)
        job = store.get_job(job_id)
        clip = store.get_clip(clip_id)
        if not clip:
            log.warning("Cannot revise clip %s: clip not found.", clip_id)
            return None

        job_settings = job.settings if job else {}
        brief_text = job_settings.get("brief_text") or (job_settings.get("campaign") or {}).get("brief")
        compliance = parse_brief_compliance(str(brief_text) if brief_text else None, job_settings)

        exports = store.list_exports(clip_id)
        if not exports:
            return None
        base_export = exports[0]
        work_dir = Path(base_export.path).parent

        clean_base = work_dir / f"clean_base_{clip_id}.mp4"
        if not clean_base.is_file():
            clean_base = Path(base_export.path)

        # 1. Check for handle override
        handle_match = re.search(r"@[A-Za-z0-9_]+", feedback)
        if handle_match:
            compliance.handle = handle_match.group(0).upper()

        # 2. Check for headline override
        new_headline = None
        quote_match = re.search(r"[\"']([^\"']+)[\"']", feedback)
        if quote_match:
            new_headline = quote_match.group(1).strip()
        elif "headline" in feedback.lower():
            parts = re.split(r"headline\s+(?:to|is|=)?\s*", feedback, flags=re.IGNORECASE)
            if len(parts) > 1 and parts[1].strip():
                new_headline = parts[1].strip().split("\n")[0]

        if not new_headline:
            seo_pack = self.seo_generator.generate_seo_pack(
                transcript_slice=f"{clip.title} {feedback}",
                compliance=compliance,
                clip_index=1,
            )
            new_headline = seo_pack.hook_headline
        else:
            seo_pack = self.seo_generator.generate_seo_pack(
                transcript_slice=new_headline,
                compliance=compliance,
                clip_index=1,
            )
            seo_pack.hook_headline = new_headline

        # 3. Check for B-Roll prompt override
        broll_clips = []
        broll_prompt = (
            f"cinematic {feedback}"
            if any(k in feedback.lower() for k in ("broll", "b-roll", "footage", "video"))
            else f"trading lifestyle, success, {compliance.topic or 'wealth mindset'}"
        )
        broll_asset = self.broll_factory.generate_broll_clip(prompt=broll_prompt, duration_s=2.5)
        if broll_asset and broll_asset.video_path.is_file():
            clip_dur = clip.end_s - clip.start_s
            if clip_dur > 10.0:
                broll_clips.append((8.0, 10.5, broll_asset.video_path))

        # Re-render PNG banners
        headline_png = work_dir / f"headline_{clip_id}.png"
        badge_png = work_dir / f"badge_{clip_id}.png"
        self.template_engine.create_top_headline_png(new_headline, headline_png)
        self.template_engine.create_bottom_attribution_png(compliance.handle, badge_png)

        # Composite revised master short (Remotion Engine with FFmpeg Fallback)
        revised_out_path = work_dir / f"master_revised_{clip_id}.mp4"
        revised_rendered = None
        try:
            from .remotion_bridge import RemotionBridge
            rem_bridge = RemotionBridge()
            if rem_bridge.is_available():
                broll_first = broll_clips[0][2] if broll_clips else None
                clip_dur = clip.end_s - clip.start_s
                revised_rendered = rem_bridge.render_content_rewards_short(
                    base_video_path=clean_base,
                    headline_text=new_headline,
                    handle=compliance.handle,
                    output_path=revised_out_path,
                    duration_s=clip_dur,
                    broll_path=broll_first,
                )
        except Exception as r_err:
            log.warning("Remotion revise render error: %s, falling back to FFmpeg compositor", r_err)

        if not revised_rendered or not revised_out_path.is_file() or revised_out_path.stat().st_size < 10000:
            self.template_engine.composite_overedit_video(
                base_video_path=clean_base,
                headline_png=headline_png,
                attribution_png=badge_png,
                output_path=revised_out_path,
                broll_clips=broll_clips,
            )

        if revised_out_path.is_file() and revised_out_path.stat().st_size > 10000:
            store.update_export_path(base_export.id, str(revised_out_path), revised_out_path.stat().st_size)
            fr = store.get_final_render(clip_id)
            if fr:
                fr.output_path = str(revised_out_path)
                store.update_final_render(fr)

            appr = store.get_clip_approval(clip_id)
            if appr:
                appr.current_status = "PENDING_REVIEW"
                appr.operator_note = f"Revised with feedback: {feedback}"
                store.update_clip_approval(appr)

            log.info("Revision produced successfully: %s", revised_out_path)
            return revised_out_path

        return None
