"""SQLite schema and migrations.

Migrations are driven by SQLite's ``user_version`` pragma: each entry in
:data:`MIGRATIONS` moves the database forward one version. Applying them is
idempotent, so :func:`migrate` runs safely on every startup.

We use raw ``sqlite3`` rather than an ORM. The schema is six small tables read
by one local process, so an ORM would add a dependency and a layer of
indirection without buying anything.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

_V1 = """
CREATE TABLE sources (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL CHECK (type IN ('youtube', 'upload')),
    url         TEXT,
    filename    TEXT,
    path        TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    channel     TEXT,
    duration_s  REAL NOT NULL DEFAULT 0,
    width       INTEGER,
    height      INTEGER,
    fps         REAL,
    has_audio   INTEGER NOT NULL DEFAULT 1,
    has_video   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE jobs (
    id            TEXT PRIMARY KEY,
    source_id     TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    status        TEXT NOT NULL CHECK (status IN ('queued','running','failed','done','cancelled')),
    current_stage TEXT NOT NULL DEFAULT '',
    progress      REAL NOT NULL DEFAULT 0,
    error         TEXT,
    provider      TEXT NOT NULL DEFAULT '',
    settings_json TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT
);
CREATE INDEX idx_jobs_source ON jobs(source_id);
CREATE INDEX idx_jobs_status ON jobs(status);

CREATE TABLE transcripts (
    job_id          TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    json_path       TEXT NOT NULL,
    language        TEXT NOT NULL DEFAULT '',
    model           TEXT NOT NULL DEFAULT '',
    has_diarization INTEGER NOT NULL DEFAULT 0,
    word_count      INTEGER NOT NULL DEFAULT 0,
    source          TEXT NOT NULL DEFAULT 'whisper',
    created_at      TEXT NOT NULL
);

CREATE TABLE clips (
    id           TEXT PRIMARY KEY,
    job_id       TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    rank         INTEGER NOT NULL DEFAULT 0,
    start_s      REAL NOT NULL,
    end_s        REAL NOT NULL,
    start_word   INTEGER NOT NULL DEFAULT 0,
    end_word     INTEGER NOT NULL DEFAULT 0,
    title        TEXT NOT NULL DEFAULT '',
    hook         TEXT NOT NULL DEFAULT '',
    score        INTEGER NOT NULL DEFAULT 0,
    reason       TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'candidate'
                 CHECK (status IN ('candidate','kept','discarded','exported')),
    user_trimmed INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
CREATE INDEX idx_clips_job ON clips(job_id, rank);

CREATE TABLE clip_edits (
    clip_id           TEXT PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
    edited_words_json TEXT,
    caption_style     TEXT NOT NULL DEFAULT 'bold_pop',
    ratio             TEXT NOT NULL DEFAULT '9:16',
    updated_at        TEXT NOT NULL
);

CREATE TABLE exports (
    id         TEXT PRIMARY KEY,
    clip_id    TEXT NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    path       TEXT NOT NULL,
    ratio      TEXT NOT NULL DEFAULT '9:16',
    style      TEXT NOT NULL DEFAULT 'bold_pop',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_exports_clip ON exports(clip_id);
"""


def _migration_v1(conn: sqlite3.Connection) -> None:
    conn.executescript(_V1)


_V2 = """
CREATE TABLE campaign_evaluations (
    clip_id         TEXT PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
    campaign_id     TEXT NOT NULL DEFAULT '',
    approved        INTEGER NOT NULL DEFAULT 1,
    final_score     REAL NOT NULL DEFAULT 0.0,
    hook_score      REAL NOT NULL DEFAULT 0.0,
    cta_score       REAL NOT NULL DEFAULT 0.0,
    viral_score     REAL NOT NULL DEFAULT 0.0,
    density_score   REAL NOT NULL DEFAULT 0.0,
    hard_failures   TEXT NOT NULL DEFAULT '[]',
    soft_warnings   TEXT NOT NULL DEFAULT '[]',
    rule_results    TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL
);
CREATE INDEX idx_evaluations_campaign ON campaign_evaluations(campaign_id);
"""


def _migration_v2(conn: sqlite3.Connection) -> None:
    conn.executescript(_V2)


_V3 = """
CREATE TABLE campaigns (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    brief_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_campaigns_name ON campaigns(name);
"""


def _migration_v3(conn: sqlite3.Connection) -> None:
    conn.executescript(_V3)


_V4 = """
ALTER TABLE exports ADD COLUMN drive_file_id TEXT;
ALTER TABLE exports ADD COLUMN drive_web_view_link TEXT;
ALTER TABLE exports ADD COLUMN drive_storage_key TEXT;
ALTER TABLE jobs ADD COLUMN dispatch_mode TEXT NOT NULL DEFAULT 'local';
ALTER TABLE jobs ADD COLUMN github_run_id TEXT;
"""


def _migration_v4(conn: sqlite3.Connection) -> None:
    conn.executescript(_V4)


_V5 = """
CREATE TABLE publishing_records (
    id            TEXT PRIMARY KEY,
    export_id     TEXT NOT NULL REFERENCES exports(id) ON DELETE CASCADE,
    job_id        TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    platform      TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('pending', 'publishing', 'published', 'failed')),
    external_id   TEXT,
    destination   TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    error         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE(export_id, platform, destination)
);
CREATE INDEX idx_publishing_export ON publishing_records(export_id);
CREATE INDEX idx_publishing_job ON publishing_records(job_id);
CREATE INDEX idx_publishing_status ON publishing_records(status);
"""


def _migration_v5(conn: sqlite3.Connection) -> None:
    conn.executescript(_V5)


_V6 = """
CREATE TABLE jobs_new (
    id                  TEXT PRIMARY KEY,
    source_id           TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    status              TEXT NOT NULL CHECK (status IN (
                            'queued', 'dispatching', 'running', 'processing',
                            'uploading', 'publishing', 'done', 'failed',
                            'cancel_requested', 'cancelled'
                        )),
    current_stage       TEXT NOT NULL DEFAULT '',
    progress            REAL NOT NULL DEFAULT 0,
    error               TEXT,
    provider            TEXT NOT NULL DEFAULT '',
    settings_json       TEXT NOT NULL DEFAULT '{}',
    dispatch_mode       TEXT NOT NULL DEFAULT 'local',
    github_run_id       TEXT,
    attempt             INTEGER NOT NULL DEFAULT 1,
    max_attempts        INTEGER NOT NULL DEFAULT 3,
    last_heartbeat_at   TEXT,
    stale_at            TEXT,
    github_workflow     TEXT,
    github_job_id       TEXT,
    github_run_url      TEXT,
    github_run_status   TEXT,
    github_conclusion   TEXT,
    dispatched_at       TEXT,
    started_at          TEXT,
    completed_at        TEXT,
    failed_at           TEXT,
    cancelled_at        TEXT,
    cancel_requested_at TEXT,
    finished_at         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

INSERT INTO jobs_new (
    id, source_id, status, current_stage, progress, error,
    provider, settings_json, dispatch_mode, github_run_id,
    created_at, updated_at, started_at, finished_at
)
SELECT
    id, source_id, status, current_stage, progress, error,
    provider, settings_json, dispatch_mode, github_run_id,
    created_at, updated_at, started_at, finished_at
FROM jobs;

DROP TABLE jobs;
ALTER TABLE jobs_new RENAME TO jobs;

CREATE INDEX idx_jobs_source ON jobs(source_id);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_jobs_github_run ON jobs(github_run_id);
"""


def _migration_v6(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(_V6)
    conn.execute("PRAGMA foreign_keys = ON")


_V7 = """
CREATE TABLE campaign_guidelines (
    id             TEXT PRIMARY KEY,
    job_id         TEXT REFERENCES jobs(id) ON DELETE CASCADE,
    filename       TEXT NOT NULL,
    mime_type      TEXT NOT NULL,
    size_bytes     INTEGER NOT NULL DEFAULT 0,
    storage_path   TEXT NOT NULL,
    extracted_text TEXT NOT NULL DEFAULT '',
    parsed_brief   TEXT NOT NULL DEFAULT '{}',
    status         TEXT NOT NULL DEFAULT 'extracted',
    error          TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX idx_guidelines_job ON campaign_guidelines(job_id);
"""


def _migration_v7(conn: sqlite3.Connection) -> None:
    conn.executescript(_V7)


_V8 = """
ALTER TABLE campaign_guidelines ADD COLUMN source_type TEXT NOT NULL DEFAULT 'upload_pdf';
ALTER TABLE campaign_guidelines ADD COLUMN drive_file_id TEXT;
ALTER TABLE campaign_guidelines ADD COLUMN sha256 TEXT;
ALTER TABLE campaign_guidelines ADD COLUMN word_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE campaign_guidelines ADD COLUMN char_count INTEGER NOT NULL DEFAULT 0;
"""


def _migration_v8(conn: sqlite3.Connection) -> None:
    conn.executescript(_V8)


_V9 = """
CREATE TABLE campaign_specifications (
    id              TEXT PRIMARY KEY,
    job_id          TEXT REFERENCES jobs(id) ON DELETE CASCADE,
    title           TEXT NOT NULL DEFAULT '',
    spec_json       TEXT NOT NULL DEFAULT '{}',
    has_conflicts   INTEGER NOT NULL DEFAULT 0,
    conflict_count  INTEGER NOT NULL DEFAULT 0,
    document_count  INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX idx_campaign_spec_job ON campaign_specifications(job_id);
ALTER TABLE jobs ADD COLUMN campaign_spec_id TEXT;
"""


def _migration_v9(conn: sqlite3.Connection) -> None:
    conn.executescript(_V9)


_V10 = """
CREATE TABLE clip_candidates (
    id                  TEXT PRIMARY KEY,
    job_id              TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    rank                INTEGER NOT NULL DEFAULT 0,
    selected            INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'discovered' CHECK (status IN ('discovered', 'scored', 'selected', 'rejected')),
    start_s             REAL NOT NULL,
    end_s               REAL NOT NULL,
    duration_s          REAL NOT NULL,
    start_word          INTEGER NOT NULL DEFAULT 0,
    end_word            INTEGER NOT NULL DEFAULT 0,
    title               TEXT NOT NULL DEFAULT '',
    hook_text           TEXT NOT NULL DEFAULT '',
    reason              TEXT NOT NULL DEFAULT '',
    transcript_slice    TEXT NOT NULL DEFAULT '',
    score               REAL NOT NULL DEFAULT 0.0,
    score_breakdown     TEXT NOT NULL DEFAULT '{}',
    hook_signals        TEXT NOT NULL DEFAULT '{}',
    climax_signals      TEXT NOT NULL DEFAULT '{}',
    cta_signals         TEXT NOT NULL DEFAULT '{}',
    requirement_matches TEXT NOT NULL DEFAULT '[]',
    rejection_reasons   TEXT NOT NULL DEFAULT '[]',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX idx_clip_candidates_job ON clip_candidates(job_id, selected, rank);
"""


def _migration_v10(conn: sqlite3.Connection) -> None:
    conn.executescript(_V10)


_V11 = """
CREATE TABLE clip_specifications (
    id                   TEXT PRIMARY KEY,
    job_id               TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    candidate_id         TEXT NOT NULL REFERENCES clip_candidates(id) ON DELETE CASCADE,
    source_id            TEXT NOT NULL,
    start_time           REAL NOT NULL,
    end_time             REAL NOT NULL,
    duration             REAL NOT NULL,
    start_word           INTEGER NOT NULL DEFAULT 0,
    end_word             INTEGER NOT NULL DEFAULT 0,
    hook_start           REAL,
    hook_end             REAL,
    hook_type            TEXT NOT NULL DEFAULT '',
    climax_start         REAL,
    climax_end           REAL,
    cta_start            REAL,
    cta_end              REAL,
    boundary_adjustments TEXT NOT NULL DEFAULT '{}',
    requirement_matches  TEXT NOT NULL DEFAULT '[]',
    quality_score        REAL NOT NULL DEFAULT 0.0,
    quality_status       TEXT NOT NULL DEFAULT 'QUALITY_PASS' CHECK (quality_status IN ('QUALITY_PASS', 'QUALITY_WARN', 'QUALITY_REJECT')),
    rejection_reasons    TEXT NOT NULL DEFAULT '[]',
    warnings             TEXT NOT NULL DEFAULT '[]',
    final_rank           INTEGER NOT NULL DEFAULT 0,
    version              INTEGER NOT NULL DEFAULT 1,
    telemetry            TEXT NOT NULL DEFAULT '{}',
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
CREATE INDEX idx_clip_specifications_job ON clip_specifications(job_id, quality_status, final_rank);
"""


def _migration_v11(conn: sqlite3.Connection) -> None:
    conn.executescript(_V11)


_V12 = """
CREATE TABLE visual_compositions (
    id                     TEXT PRIMARY KEY,
    clip_id                TEXT NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    job_id                 TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    source_width           INTEGER NOT NULL,
    source_height          INTEGER NOT NULL,
    output_width           INTEGER NOT NULL,
    output_height          INTEGER NOT NULL,
    crop_strategy          TEXT NOT NULL DEFAULT 'track',
    tracking_strategy      TEXT NOT NULL DEFAULT 'mediapipe',
    tracking_confidence    REAL NOT NULL DEFAULT 0.0,
    camera_movement_score  REAL NOT NULL DEFAULT 0.0,
    smoothing_parameters   TEXT NOT NULL DEFAULT '{}',
    fallback_used          INTEGER NOT NULL DEFAULT 0,
    fallback_reason        TEXT NOT NULL DEFAULT '',
    quality_score          REAL NOT NULL DEFAULT 0.0,
    quality_status         TEXT NOT NULL DEFAULT 'VISUAL_PASS' CHECK (quality_status IN ('VISUAL_PASS', 'VISUAL_WARN', 'VISUAL_REJECT')),
    warnings               TEXT NOT NULL DEFAULT '[]',
    rejection_reasons      TEXT NOT NULL DEFAULT '[]',
    version                INTEGER NOT NULL DEFAULT 1,
    telemetry              TEXT NOT NULL DEFAULT '{}',
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);
CREATE INDEX idx_visual_compositions_job ON visual_compositions(job_id, quality_status);
"""


def _migration_v12(conn: sqlite3.Connection) -> None:
    conn.executescript(_V12)


_V13 = """
CREATE TABLE retention_optimizations (
    id                     TEXT PRIMARY KEY,
    clip_id                TEXT NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    job_id                 TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    retention_score        REAL NOT NULL DEFAULT 0.0,
    final_score            REAL NOT NULL DEFAULT 0.0,
    quality_status         TEXT NOT NULL DEFAULT 'FINAL_PASS' CHECK (quality_status IN ('FINAL_PASS', 'FINAL_WARN', 'FINAL_REJECT')),
    hook_strength          REAL NOT NULL DEFAULT 0.0,
    speech_density_wps     REAL NOT NULL DEFAULT 0.0,
    dead_air_percentage    REAL NOT NULL DEFAULT 0.0,
    pacing_score           REAL NOT NULL DEFAULT 0.0,
    narrative_score        REAL NOT NULL DEFAULT 0.0,
    editing_decisions      TEXT NOT NULL DEFAULT '{}',
    visual_emphasis        TEXT NOT NULL DEFAULT '[]',
    scoring_breakdown      TEXT NOT NULL DEFAULT '{}',
    rejection_reasons      TEXT NOT NULL DEFAULT '[]',
    warnings               TEXT NOT NULL DEFAULT '[]',
    processing_time_s      REAL NOT NULL DEFAULT 0.0,
    version                INTEGER NOT NULL DEFAULT 1,
    telemetry              TEXT NOT NULL DEFAULT '{}',
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);
CREATE INDEX idx_retention_optimizations_job ON retention_optimizations(job_id, quality_status);
"""


def _migration_v13(conn: sqlite3.Connection) -> None:
    conn.executescript(_V13)


_V14 = """
CREATE TABLE caption_optimizations (
    id                  TEXT PRIMARY KEY,
    clip_id             TEXT NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    job_id              TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    style_key           TEXT NOT NULL DEFAULT 'classic_professional',
    style_label         TEXT NOT NULL DEFAULT 'Classic Professional',
    caption_segments    TEXT NOT NULL DEFAULT '[]',
    emphasis_metadata   TEXT NOT NULL DEFAULT '{}',
    hook_treatment      TEXT NOT NULL DEFAULT '{}',
    climax_treatment    TEXT NOT NULL DEFAULT '{}',
    cta_treatment       TEXT NOT NULL DEFAULT '{}',
    quality_score       REAL NOT NULL DEFAULT 0.0,
    quality_status      TEXT NOT NULL DEFAULT 'CAPTION_PASS' CHECK (quality_status IN ('CAPTION_PASS', 'CAPTION_WARN', 'CAPTION_REJECT')),
    rejection_reasons   TEXT NOT NULL DEFAULT '[]',
    warnings            TEXT NOT NULL DEFAULT '[]',
    fallback_used       INTEGER NOT NULL DEFAULT 0,
    fallback_reason     TEXT NOT NULL DEFAULT '',
    render_time_s       REAL NOT NULL DEFAULT 0.0,
    version             INTEGER NOT NULL DEFAULT 1,
    telemetry           TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX idx_caption_optimizations_job ON caption_optimizations(job_id, quality_status);
"""


def _migration_v14(conn: sqlite3.Connection) -> None:
    conn.executescript(_V14)


_V15 = """
CREATE TABLE bgm_assets (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    file_path         TEXT NOT NULL,
    genre             TEXT NOT NULL DEFAULT '',
    mood              TEXT NOT NULL DEFAULT '',
    tags              TEXT NOT NULL DEFAULT '[]',
    mime_type         TEXT NOT NULL DEFAULT 'audio/mpeg',
    duration_s        REAL NOT NULL DEFAULT 0.0,
    file_size_bytes   INTEGER NOT NULL DEFAULT 0,
    enabled           INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX idx_bgm_assets_enabled ON bgm_assets(enabled);
"""


def _migration_v15(conn: sqlite3.Connection) -> None:
    conn.executescript(_V15)


_V16 = """
CREATE TABLE bgm_mixes (
    id                     TEXT PRIMARY KEY,
    clip_id                TEXT NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    job_id                 TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    bgm_asset_id           TEXT,
    bgm_asset_name         TEXT NOT NULL DEFAULT '',
    bgm_applied            INTEGER NOT NULL DEFAULT 0,
    clip_duration_s        REAL NOT NULL DEFAULT 0.0,
    bgm_duration_s         REAL NOT NULL DEFAULT 0.0,
    loop_trim_decision     TEXT NOT NULL DEFAULT 'none',
    ducking_applied        INTEGER NOT NULL DEFAULT 0,
    ducking_parameters     TEXT NOT NULL DEFAULT '{}',
    normalization_applied  INTEGER NOT NULL DEFAULT 0,
    integrated_lufs        REAL NOT NULL DEFAULT 0.0,
    true_peak_db           REAL NOT NULL DEFAULT 0.0,
    quality_score          REAL NOT NULL DEFAULT 0.0,
    quality_status         TEXT NOT NULL DEFAULT 'MIX_PASS' CHECK (quality_status IN ('MIX_PASS', 'MIX_WARN', 'MIX_REJECT')),
    warnings               TEXT NOT NULL DEFAULT '[]',
    rejection_reasons      TEXT NOT NULL DEFAULT '[]',
    processing_time_s      REAL NOT NULL DEFAULT 0.0,
    mixed_audio_path       TEXT NOT NULL DEFAULT '',
    telemetry              TEXT NOT NULL DEFAULT '{}',
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);
CREATE INDEX idx_bgm_mixes_job ON bgm_mixes(job_id, quality_status);
"""


def _migration_v16(conn: sqlite3.Connection) -> None:
    conn.executescript(_V16)


_V17 = """
CREATE TABLE final_renders (
    id             TEXT PRIMARY KEY,
    job_id         TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    clip_id        TEXT NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    output_path    TEXT NOT NULL,
    package_dir    TEXT NOT NULL DEFAULT '',
    duration       REAL NOT NULL DEFAULT 0.0,
    width          INTEGER NOT NULL DEFAULT 0,
    height         INTEGER NOT NULL DEFAULT 0,
    fps            REAL NOT NULL DEFAULT 0.0,
    video_codec    TEXT NOT NULL DEFAULT '',
    audio_codec    TEXT NOT NULL DEFAULT '',
    caption_style  TEXT NOT NULL DEFAULT '',
    bgm_asset_id   TEXT,
    quality_score  REAL NOT NULL DEFAULT 0.0,
    quality_status TEXT NOT NULL DEFAULT 'RENDER_PASS' CHECK (quality_status IN ('RENDER_PASS', 'RENDER_WARN', 'RENDER_REJECT')),
    render_status  TEXT NOT NULL DEFAULT 'completed' CHECK (render_status IN ('completed', 'failed')),
    render_attempt INTEGER NOT NULL DEFAULT 1,
    error_details  TEXT NOT NULL DEFAULT '[]',
    telemetry      TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX idx_final_renders_job ON final_renders(job_id, quality_status);
CREATE INDEX idx_final_renders_clip ON final_renders(clip_id);
"""


def _migration_v17(conn: sqlite3.Connection) -> None:
    conn.executescript(_V17)


_V18 = """
CREATE TABLE clip_metadata (
    id                             TEXT PRIMARY KEY,
    job_id                         TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    clip_id                        TEXT NOT NULL REFERENCES clips(id) ON DELETE CASCADE UNIQUE,
    generated_title                TEXT NOT NULL,
    final_title                    TEXT NOT NULL,
    generated_description          TEXT NOT NULL,
    final_description              TEXT NOT NULL,
    generated_hashtags             TEXT NOT NULL DEFAULT '[]',
    final_hashtags                 TEXT NOT NULL DEFAULT '[]',
    generated_mentions             TEXT NOT NULL DEFAULT '[]',
    final_mentions                 TEXT NOT NULL DEFAULT '[]',
    generated_cta                  TEXT NOT NULL DEFAULT '',
    final_cta                      TEXT NOT NULL DEFAULT '',
    campaign_requirements_matched  TEXT NOT NULL DEFAULT '{}',
    compliance_status              TEXT NOT NULL DEFAULT 'SEO_PASS' CHECK (compliance_status IN ('SEO_PASS', 'SEO_WARN', 'SEO_REJECT')),
    compliance_score               REAL NOT NULL DEFAULT 100.0,
    validation_errors              TEXT NOT NULL DEFAULT '[]',
    validation_warnings            TEXT NOT NULL DEFAULT '[]',
    version                        INTEGER NOT NULL DEFAULT 1,
    telemetry                      TEXT NOT NULL DEFAULT '{}',
    created_at                     TEXT NOT NULL,
    updated_at                     TEXT NOT NULL
);
CREATE INDEX idx_clip_metadata_job ON clip_metadata(job_id, compliance_status);
CREATE INDEX idx_clip_metadata_clip ON clip_metadata(clip_id);
"""


def _migration_v18(conn: sqlite3.Connection) -> None:
    conn.executescript(_V18)


# ---------------------------------------------------------------------------
# Step 24: Clip Approval (V19)
# ---------------------------------------------------------------------------

_V19 = """
CREATE TABLE clip_approvals (
    id               TEXT PRIMARY KEY,
    job_id           TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    clip_id          TEXT NOT NULL REFERENCES clips(id) ON DELETE CASCADE UNIQUE,
    current_status   TEXT NOT NULL DEFAULT 'PENDING_REVIEW'
                     CHECK (current_status IN (
                         'PENDING_REVIEW',
                         'APPROVED',
                         'REJECTED',
                         'CHANGES_REQUESTED',
                         'PUBLISHING_LOCKED'
                     )),
    operator_action  TEXT,
    operator_note    TEXT NOT NULL DEFAULT '',
    version          INTEGER NOT NULL DEFAULT 1,
    previous_status  TEXT,
    publish_eligible INTEGER NOT NULL DEFAULT 0,
    blocking_reasons TEXT NOT NULL DEFAULT '[]',
    history          TEXT NOT NULL DEFAULT '[]',
    telemetry        TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX idx_clip_approvals_job    ON clip_approvals(job_id, current_status);
CREATE INDEX idx_clip_approvals_clip   ON clip_approvals(clip_id);
"""


def _migration_v19(conn: sqlite3.Connection) -> None:
    conn.executescript(_V19)


# ---------------------------------------------------------------------------
# Step 25: Remote Publications (V20)
# ---------------------------------------------------------------------------

_V20 = """
CREATE TABLE publications (
    id                  TEXT PRIMARY KEY,
    job_id              TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    clip_id             TEXT NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    final_render_id     TEXT REFERENCES final_renders(id) ON DELETE SET NULL,
    platform            TEXT NOT NULL,
    account_id          TEXT NOT NULL DEFAULT '',
    destination_id      TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN (
                            'PENDING',
                            'UPLOADING',
                            'PUBLISHED',
                            'FAILED_RETRYABLE',
                            'FAILED_PERMANENT',
                            'SKIPPED',
                            'CANCELLED'
                        )),
    attempt_number      INTEGER NOT NULL DEFAULT 1,
    idempotency_key     TEXT NOT NULL,
    remote_media_id     TEXT,
    remote_post_id      TEXT,
    permalink           TEXT,
    upload_started_at   TEXT,
    upload_completed_at TEXT,
    published_at        TEXT,
    error_code          TEXT,
    error_message       TEXT,
    response_metadata   TEXT NOT NULL DEFAULT '{}',
    retry_count         INTEGER NOT NULL DEFAULT 0,
    version             INTEGER NOT NULL DEFAULT 1,
    telemetry           TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX idx_publications_job ON publications(job_id, status);
CREATE INDEX idx_publications_clip ON publications(clip_id);
CREATE UNIQUE INDEX idx_publications_idempotency ON publications(idempotency_key);
"""


def _migration_v20(conn: sqlite3.Connection) -> None:
    conn.executescript(_V20)


#: Ordered migrations. Index + 1 is the resulting ``user_version``.
#: Append only — never edit a migration that has shipped.
MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [
    _migration_v1,
    _migration_v2,
    _migration_v3,
    _migration_v4,
    _migration_v5,
    _migration_v6,
    _migration_v7,
    _migration_v8,
    _migration_v9,
    _migration_v10,
    _migration_v11,
    _migration_v12,
    _migration_v13,
    _migration_v14,
    _migration_v15,
    _migration_v16,
    _migration_v17,
    _migration_v18,
    _migration_v19,
    _migration_v20,
]

SCHEMA_VERSION = len(MIGRATIONS)


def migrate(conn: sqlite3.Connection) -> int:
    """Apply any outstanding migrations. Returns the resulting schema version."""
    current: int = conn.execute("PRAGMA user_version").fetchone()[0]

    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema version {current} is newer than this AutoClip "
            f"build supports ({SCHEMA_VERSION}). Upgrade AutoClip."
        )

    for version in range(current, SCHEMA_VERSION):
        migration = MIGRATIONS[version]
        with conn:
            migration(conn)
            # PRAGMA doesn't accept bound parameters; version is loop-controlled.
            conn.execute(f"PRAGMA user_version = {version + 1}")

    return SCHEMA_VERSION
