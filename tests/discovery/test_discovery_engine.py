"""Integration and Scale tests for ClipDiscoveryEngine."""

import time
import pytest
from clipping.contracts.perception import (
    SpeakerAttributedTranscript,
    WordTimestamp,
    SpeakerSegment,
    SceneCut,
    ActiveSpeakerSegment,
)
from clipping.contracts.clip import ClipSelectionResult
from clipping.discovery.engine import ClipDiscoveryEngine
from clipping.storage.local import LocalStorageDriver


@pytest.mark.asyncio
async def test_clip_discovery_engine_lifecycle(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)

    # 1. Seed transcript with several realistic conversational insights
    sentences = [
        "The biggest breakthrough in AI agents is deterministic tool execution.",
        "When agents have strict Pydantic schemas they stop hallucinating parameters.",
        "And that allows teams to deploy fully autonomous systems to production safely.",
        "However most developers still rely on unstructured string parsing.",
        "And the problem is that string parsing fails fifteen percent of the time.",
        "So if you want reliability you must enforce typed contracts from day one.",
    ]

    words = []
    t = 0.0
    for s_idx, s in enumerate(sentences):
        spk = "SPEAKER_00" if s_idx < 3 else "SPEAKER_01"
        for tok in s.split():
            words.append(WordTimestamp(word=tok, start=round(t, 2), end=round(t + 0.4, 2), probability=0.99, speaker_id=spk))
            t += 0.4
        t += 0.5

    transcript = SpeakerAttributedTranscript(
        source_video_id="VID_DISC_01",
        text=" ".join(sentences),
        words=words,
        speaker_segments=[
            SpeakerSegment(speaker_id="SPEAKER_00", start=0.0, end=15.0),
            SpeakerSegment(speaker_id="SPEAKER_01", start=15.0, end=35.0),
        ],
    )

    # Seed transcript JSON into storage
    t_key = "sources/VID_DISC_01/speaker_transcript.json"
    await storage.upload_bytes(transcript.model_dump_json(indent=2).encode("utf-8"), t_key, content_type="application/json")

    # 2. Run Discovery Engine
    engine = ClipDiscoveryEngine()
    result = await engine.process(source_video_id="VID_DISC_01", storage_driver=storage)

    assert isinstance(result, ClipSelectionResult)
    assert result.source_video_id == "VID_DISC_01"
    assert result.total_candidates_generated > 0

    # 3. Verify Artifacts in StorageDriver
    assert await storage.exists("sources/VID_DISC_01/candidates.json") is True
    assert await storage.exists("sources/VID_DISC_01/ranked_candidates.json") is True
    assert await storage.exists("sources/VID_DISC_01/selected_clips.json") is True

    # 4. Idempotency Check
    cached_result = await engine.process(source_video_id="VID_DISC_01", storage_driver=storage, force_recompute=False)
    assert cached_result.source_video_id == "VID_DISC_01"
    assert len(cached_result.selected_clips) == len(result.selected_clips)


@pytest.mark.asyncio
async def test_large_scale_podcast_synthetic_benchmark(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)

    # Generate 2.5 hours synthetic transcript: 150 minutes = 9,000 seconds
    # ~25,000 words across 600 sentences with 15 distinct topics/chapters
    words = []
    t = 0.0
    spk_ids = ["SPEAKER_00", "SPEAKER_01"]

    topics = [
        ("startup", "The biggest lesson from scaling our startup to ten million users was optimizing database indexes. We saw a ninety percent reduction in latency after removing redundant queries. And that saved our infrastructure budget."),
        ("cloud", "What nobody tells you about distributed cloud systems is that network partitions happen every day. Here is why you should always design with idempotent background workers. That ensures zero data loss during outages."),
        ("product", "The truth is most companies fail because they spend twelve months building features nobody asked for. I realized we needed to talk to fifty customers every single week. That changed our entire growth trajectory."),
        ("pricing", "The secret to SaaS pricing is never charging based on server compute costs. You must align pricing directly with customer business outcomes. That gives you tenfold higher retention over time."),
        ("hiring", "The biggest mistake in hiring engineers is focusing entirely on whiteboard algorithms. What actually matters is their ability to read and refactor existing production codebases safely."),
        ("security", "Here is why secret management is the most critical vulnerability in modern cloud deployments. If you embed API keys in environment files without encryption, attackers will compromise your cluster within hours."),
        ("marketing", "Most people think organic distribution is purely about posting five times a day on social media. The reality is that one exceptional case study outperforms five hundred generic text posts."),
        ("ai", "The paradox of artificial intelligence in 2026 is that reasoning models are only as good as their data contracts. When schemas are strictly enforced, agent hallucinations drop to near zero percent."),
        ("culture", "I was wrong about remote work during our first three years of growth. Building asynchronous communication protocols transformed our development speed across global timezones."),
        ("growth", "Here is how to analyze customer churn before it impacts your bottom line. Look at weekly active feature usage rather than monthly login metrics."),
        ("design", "The mistake most UI designers make is cluttering the mobile view with unnecessary navigation controls. Keep the primary user action directly in thumb reach."),
        ("testing", "Why do ninety percent of software teams struggle with flaky end to end tests? Because they mock external network requests improperly without deterministic replay."),
        ("leadership", "The lesson from managing fifty engineers is that clarity of goals always trumps micromanagement. Give senior developers clear boundaries and let them execute."),
        ("sales", "What happened was we doubled our conversion rate simply by shortening the qualification form from ten questions to three. Friction is the enemy of conversions."),
        ("investing", "If you want to understand venture capital economics, look at power law distributions where one outlier company returns the entire fund."),
    ]

    for cycle in range(7):  # 7 * 15 topics = 105 discussion segments spanning > 2.5 hours
        for topic_name, topic_text in topics:
            spk = spk_ids[cycle % 2]
            for tok in topic_text.split():
                words.append(
                    WordTimestamp(
                        word=tok,
                        start=round(t, 2),
                        end=round(t + 0.35, 2),
                        probability=0.98,
                        speaker_id=spk,
                    )
                )
                t += 0.35
            t += 1.0  # pause between topic turns

    transcript = SpeakerAttributedTranscript(
        source_video_id="VID_2_5_HOURS",
        text=" ".join(w.word for w in words),
        words=words,
    )
    t_key = "sources/VID_2_5_HOURS/speaker_transcript.json"
    await storage.upload_bytes(transcript.model_dump_json().encode("utf-8"), t_key, content_type="application/json")

    engine = ClipDiscoveryEngine()

    start_wall = time.time()
    result = await engine.process(source_video_id="VID_2_5_HOURS", storage_driver=storage)
    elapsed = time.time() - start_wall

    # Verification:
    # 1. Performance: 2.5 hour source processed in < 10.0 seconds on pure CPU
    assert elapsed < 10.0, f"Discovery on 2.5h podcast took {elapsed:.2f}s (expected < 10.0s)"
    # 2. Selected clips capped between 5 and 10
    assert 5 <= len(result.selected_clips) <= 10
    # 3. All selected clips meet quality threshold
    for sel in result.selected_clips:
        assert sel.score.overall_virality_score >= result.quality_threshold
