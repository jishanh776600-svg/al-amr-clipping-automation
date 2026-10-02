"""Whop Campaign Persistent Ledger (Step 3).

Provides authoritative, idempotent SQLite persistence for discovered Whop campaigns
and an append-only immutable audit trail event history.
Reuses existing backend.autoclip database infrastructure when available or a dedicated
SQLite ledger file configured via WHOP_LEDGER_PATH.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .catalog import DiscoveredCampaign
from .config import sanitize_text
from .models import (
    CampaignEvent,
    CampaignRecord,
    CampaignState,
    InvalidStateTransitionError,
    WhopCampaignBrief,
    WhopAutoClipJobRecord,
    WhopJobQAReport,
    ClipQARecord,
    ClipTechnicalQAResult,
    RuleComplianceResult,
    RuleComplianceStatus,
    validate_transition,
)

log = logging.getLogger(__name__)

# Default ledger path if not configured
DEFAULT_LEDGER_DIR = Path("data")
DEFAULT_LEDGER_PATH = DEFAULT_LEDGER_DIR / "whop_ledger.db"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS whop_campaigns (
    campaign_id          TEXT PRIMARY KEY,
    title                TEXT NOT NULL,
    campaign_url         TEXT NOT NULL,
    payout_raw           TEXT NOT NULL DEFAULT '',
    cpm                  REAL,
    platforms_json       TEXT NOT NULL DEFAULT '[]',
    source_urls_json     TEXT NOT NULL DEFAULT '[]',
    guideline_urls_json  TEXT NOT NULL DEFAULT '[]',
    recommended_account  TEXT,
    current_state        TEXT NOT NULL DEFAULT 'DISCOVERED',
    discovered_at        TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    last_error           TEXT,
    retry_count          INTEGER NOT NULL DEFAULT 0,
    last_attempt_at      TEXT,
    job_id               TEXT,
    submission_url       TEXT,
    submitted_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_whop_campaigns_state ON whop_campaigns(current_state);
CREATE INDEX IF NOT EXISTS idx_whop_campaigns_cpm ON whop_campaigns(cpm);

CREATE TABLE IF NOT EXISTS whop_campaign_events (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id          TEXT NOT NULL REFERENCES whop_campaigns(campaign_id) ON DELETE CASCADE,
    previous_state       TEXT,
    new_state            TEXT NOT NULL,
    timestamp            TEXT NOT NULL,
    reason               TEXT NOT NULL DEFAULT '',
    source               TEXT NOT NULL DEFAULT 'whop_discovery',
    metadata_json        TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_whop_events_campaign ON whop_campaign_events(campaign_id);
CREATE INDEX IF NOT EXISTS idx_whop_events_timestamp ON whop_campaign_events(timestamp);

CREATE TABLE IF NOT EXISTS whop_campaign_briefs (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id                 TEXT NOT NULL REFERENCES whop_campaigns(campaign_id) ON DELETE CASCADE,
    guideline_hash              TEXT NOT NULL,
    guideline_source_type       TEXT NOT NULL,
    guideline_source_reference  TEXT NOT NULL,
    parsing_status              TEXT NOT NULL,
    brief_json                  TEXT NOT NULL,
    rules_count                 INTEGER NOT NULL DEFAULT 0,
    mandatory_rules_count       INTEGER NOT NULL DEFAULT 0,
    prohibited_rules_count      INTEGER NOT NULL DEFAULT 0,
    unresolved_rules_count      INTEGER NOT NULL DEFAULT 0,
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL,
    UNIQUE(campaign_id, guideline_hash)
);

CREATE INDEX IF NOT EXISTS idx_whop_briefs_campaign ON whop_campaign_briefs(campaign_id);
CREATE INDEX IF NOT EXISTS idx_whop_briefs_hash ON whop_campaign_briefs(guideline_hash);

CREATE TABLE IF NOT EXISTS whop_autoclip_jobs (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id          TEXT NOT NULL REFERENCES whop_campaigns(campaign_id) ON DELETE CASCADE,
    guideline_hash       TEXT NOT NULL,
    autoclip_job_id      TEXT NOT NULL,
    idempotency_key      TEXT NOT NULL UNIQUE,
    status               TEXT NOT NULL,
    request_hash         TEXT NOT NULL,
    source_hash          TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    last_error           TEXT,
    metadata_json        TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_whop_autoclip_campaign ON whop_autoclip_jobs(campaign_id);
CREATE INDEX IF NOT EXISTS idx_whop_autoclip_job_id ON whop_autoclip_jobs(autoclip_job_id);
CREATE INDEX IF NOT EXISTS idx_whop_autoclip_idempotency ON whop_autoclip_jobs(idempotency_key);
CREATE UNIQUE INDEX IF NOT EXISTS uq_whop_autoclip_logical ON whop_autoclip_jobs(campaign_id, guideline_hash, source_hash);

CREATE TABLE IF NOT EXISTS whop_qa_records (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id          TEXT NOT NULL REFERENCES whop_campaigns(campaign_id) ON DELETE CASCADE,
    guideline_hash       TEXT NOT NULL,
    autoclip_job_id      TEXT NOT NULL,
    artifact_hash        TEXT NOT NULL,
    qa_status            TEXT NOT NULL,
    overall_quality_score REAL NOT NULL,
    valid_clips_count    INTEGER NOT NULL,
    total_clips_evaluated INTEGER NOT NULL,
    clips_json           TEXT NOT NULL,
    compliance_summary_json TEXT NOT NULL,
    unsupported_rules_json TEXT NOT NULL,
    warnings_json        TEXT NOT NULL,
    failures_json        TEXT NOT NULL,
    created_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_whop_qa_campaign ON whop_qa_records(campaign_id);
CREATE INDEX IF NOT EXISTS idx_whop_qa_job ON whop_qa_records(autoclip_job_id);
CREATE INDEX IF NOT EXISTS idx_whop_qa_lookup ON whop_qa_records(campaign_id, guideline_hash, autoclip_job_id, artifact_hash);
"""


def _get_default_db_path() -> Path:
    """Resolves database path: environment override or default local data directory."""
    env_path = os.getenv("WHOP_LEDGER_PATH", "").strip()
    if env_path:
        p = Path(env_path)
    else:
        # Check if autoclip persistent root exists
        autoclip_home = os.getenv("AUTOCLIP_HOME", "").strip()
        if autoclip_home and Path(autoclip_home).exists():
            p = Path(autoclip_home) / "whop_ledger.db"
        else:
            p = DEFAULT_LEDGER_PATH

    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class CampaignLedger:
    """Thread-safe, transaction-guarded persistent ledger for Whop campaigns."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else _get_default_db_path()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.executescript(_SCHEMA_SQL)
            conn.commit()

    def get_campaign(self, campaign_id: str) -> Optional[CampaignRecord]:
        """Retrieves a single canonical campaign record by primary key."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM whop_campaigns WHERE campaign_id = ?",
                (campaign_id,)
            ).fetchone()
            if not row:
                return None
            return self._row_to_record(row)

    def save_campaign(self, record: CampaignRecord) -> None:
        """Saves or updates a campaign in whop_campaigns."""
        with self._get_connection() as conn:
            self._insert_campaign_tx(conn, record)
            conn.commit()


    def list_campaigns(
        self,
        state: Optional[CampaignState] = None,
        limit: int = 100,
    ) -> List[CampaignRecord]:
        """Lists campaigns, optionally filtered by state."""
        with self._get_connection() as conn:
            if state:
                rows = conn.execute(
                    "SELECT * FROM whop_campaigns WHERE current_state = ? ORDER BY updated_at DESC LIMIT ?",
                    (state.value, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM whop_campaigns ORDER BY updated_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            return [self._row_to_record(r) for r in rows]

    def list_events(self, campaign_id: str) -> List[CampaignEvent]:
        """Retrieves immutable chronological audit trail events for a campaign."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM whop_campaign_events WHERE campaign_id = ? ORDER BY id ASC",
                (campaign_id,)
            ).fetchall()
            events = []
            for r in rows:
                events.append(
                    CampaignEvent(
                        campaign_id=r["campaign_id"],
                        previous_state=r["previous_state"],
                        new_state=r["new_state"],
                        timestamp=r["timestamp"],
                        reason=r["reason"],
                        source=r["source"],
                        metadata_json=r["metadata_json"],
                    )
                )
            return events

    def ingest_discovered_campaign(
        self,
        discovered: Union[DiscoveredCampaign, Dict[str, Any]],
        source: str = "whop_discovery",
    ) -> Tuple[CampaignRecord, bool]:
        """Ingests a discovered campaign idempotently.
        
        Accepts either a DiscoveredCampaign dataclass or a raw dictionary.
        Returns (record, is_new_campaign).
        If campaign exists, safely updates metadata and logs METADATA_UPDATED event if changed.
        Transitions state:
          - New campaign: DISCOVERED -> VALIDATING -> ELIGIBLE or REJECTED
          - Existing campaign: VALIDATING -> ELIGIBLE or REJECTED
        """
        if isinstance(discovered, dict):
            discovered = DiscoveredCampaign(
                campaign_id=discovered["campaign_id"],
                title=discovered.get("title", ""),
                campaign_url=discovered.get("campaign_url", ""),
                payout_raw=discovered.get("payout_raw", ""),
                cpm=discovered.get("cpm"),
                platforms=discovered.get("platforms", []),
                source_urls=discovered.get("source_urls", []),
                guideline_urls=discovered.get("guideline_urls", []),
                eligible=discovered.get("eligible", False),
                eligibility_reasons=discovered.get("eligibility_reasons", []),
                recommended_account=discovered.get("recommended_account"),
                discovered_at=discovered.get("discovered_at"),
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        campaign_id = discovered.campaign_id

        # Target outcome state based on eligibility decision
        target_state = CampaignState.ELIGIBLE if discovered.eligible else CampaignState.REJECTED
        eligibility_reason = "; ".join(discovered.eligibility_reasons)

        with self._get_connection() as conn:
            existing_row = conn.execute(
                "SELECT * FROM whop_campaigns WHERE campaign_id = ?",
                (campaign_id,)
            ).fetchone()

            if existing_row is None:
                # 1. Insert New Campaign in DISCOVERED state
                record = CampaignRecord(
                    campaign_id=campaign_id,
                    title=discovered.title,
                    campaign_url=discovered.campaign_url,
                    payout_raw=discovered.payout_raw,
                    cpm=discovered.cpm,
                    platforms=discovered.platforms,
                    source_urls=discovered.source_urls,
                    guideline_urls=discovered.guideline_urls,
                    recommended_account=discovered.recommended_account,
                    current_state=CampaignState.DISCOVERED,
                    discovered_at=discovered.discovered_at or now_iso,
                    updated_at=now_iso,
                )
                self._insert_campaign_tx(conn, record)

                # Record creation event
                self._record_event_tx(
                    conn,
                    CampaignEvent(
                        campaign_id=campaign_id,
                        previous_state=None,
                        new_state=CampaignState.DISCOVERED.value,
                        timestamp=now_iso,
                        reason="Discovered from Whop Content Rewards marketplace",
                        source=source,
                        metadata_json=json.dumps({"initial_cpm": discovered.cpm, "platforms": discovered.platforms}),
                    )
                )

                # Transition DISCOVERED -> VALIDATING
                validate_transition(CampaignState.DISCOVERED, CampaignState.VALIDATING)
                self._update_state_tx(conn, campaign_id, CampaignState.VALIDATING, now_iso)
                self._record_event_tx(
                    conn,
                    CampaignEvent(
                        campaign_id=campaign_id,
                        previous_state=CampaignState.DISCOVERED.value,
                        new_state=CampaignState.VALIDATING.value,
                        timestamp=now_iso,
                        reason="Evaluating read-only eligibility criteria",
                        source=source,
                    )
                )

                # Transition VALIDATING -> ELIGIBLE or REJECTED
                validate_transition(CampaignState.VALIDATING, target_state)
                self._update_state_tx(conn, campaign_id, target_state, now_iso)
                self._record_event_tx(
                    conn,
                    CampaignEvent(
                        campaign_id=campaign_id,
                        previous_state=CampaignState.VALIDATING.value,
                        new_state=target_state.value,
                        timestamp=now_iso,
                        reason=eligibility_reason,
                        source=source,
                        metadata_json=json.dumps({
                            "eligible": discovered.eligible,
                            "reasons": discovered.eligibility_reasons,
                            "recommended_account": discovered.recommended_account,
                        }),
                    )
                )

                conn.commit()
                return self.get_campaign(campaign_id), True

            else:
                # 2. Update existing campaign
                existing = self._row_to_record(existing_row)

                # Detect metadata changes
                changes: Dict[str, Any] = {}
                if existing.cpm != discovered.cpm:
                    changes["cpm"] = {"old": existing.cpm, "new": discovered.cpm}
                if existing.title != discovered.title:
                    changes["title"] = {"old": existing.title, "new": discovered.title}
                if existing.payout_raw != discovered.payout_raw:
                    changes["payout_raw"] = {"old": existing.payout_raw, "new": discovered.payout_raw}
                if sorted(existing.platforms) != sorted(discovered.platforms):
                    changes["platforms"] = {"old": existing.platforms, "new": discovered.platforms}
                if sorted(existing.source_urls) != sorted(discovered.source_urls):
                    changes["source_urls"] = {"old": existing.source_urls, "new": discovered.source_urls}
                if existing.recommended_account != discovered.recommended_account:
                    changes["recommended_account"] = {"old": existing.recommended_account, "new": discovered.recommended_account}

                # Update record fields
                conn.execute(
                    """
                    UPDATE whop_campaigns SET
                        title = ?,
                        campaign_url = ?,
                        payout_raw = ?,
                        cpm = ?,
                        platforms_json = ?,
                        source_urls_json = ?,
                        guideline_urls_json = ?,
                        recommended_account = ?,
                        updated_at = ?
                    WHERE campaign_id = ?
                    """,
                    (
                        discovered.title,
                        discovered.campaign_url,
                        discovered.payout_raw,
                        discovered.cpm,
                        json.dumps(discovered.platforms),
                        json.dumps(discovered.source_urls),
                        json.dumps(discovered.guideline_urls),
                        discovered.recommended_account,
                        now_iso,
                        campaign_id,
                    )
                )

                if changes:
                    self._record_event_tx(
                        conn,
                        CampaignEvent(
                            campaign_id=campaign_id,
                            previous_state=existing.current_state.value,
                            new_state=existing.current_state.value,
                            timestamp=now_iso,
                            reason="METADATA_UPDATED",
                            source=source,
                            metadata_json=json.dumps(changes),
                        )
                    )

                # Evaluate state transition if eligibility changed
                # Only transition if existing state is DISCOVERED, VALIDATING, ELIGIBLE, or REJECTED
                if existing.current_state in (CampaignState.DISCOVERED, CampaignState.VALIDATING, CampaignState.ELIGIBLE, CampaignState.REJECTED):
                    if existing.current_state != target_state:
                        # Re-validation transition
                        validate_transition(existing.current_state, CampaignState.VALIDATING)
                        self._update_state_tx(conn, campaign_id, CampaignState.VALIDATING, now_iso)
                        validate_transition(CampaignState.VALIDATING, target_state)
                        self._update_state_tx(conn, campaign_id, target_state, now_iso)
                        self._record_event_tx(
                            conn,
                            CampaignEvent(
                                campaign_id=campaign_id,
                                previous_state=existing.current_state.value,
                                new_state=target_state.value,
                                timestamp=now_iso,
                                reason=f"Eligibility re-evaluated on rediscovery: {eligibility_reason}",
                                source=source,
                                metadata_json=json.dumps({"eligible": discovered.eligible, "reasons": discovered.eligibility_reasons}),
                            )
                        )

                conn.commit()
                return self.get_campaign(campaign_id), False

    def transition_state(
        self,
        campaign_id: str,
        target_state: CampaignState,
        reason: str = "",
        source: str = "whop_ledger",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CampaignRecord:
        """Explicitly transitions campaign state with strict transition validation."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM whop_campaigns WHERE campaign_id = ?",
                (campaign_id,)
            ).fetchone()
            if not row:
                raise KeyError(f"Campaign '{campaign_id}' not found in ledger.")

            current = CampaignState(row["current_state"])

            # If already in target state, return idempotently without duplicate event
            if current == target_state:
                return self._row_to_record(row)

            # Validate permitted transition
            validate_transition(current, target_state)

            self._update_state_tx(conn, campaign_id, target_state, now_iso)
            self._record_event_tx(
                conn,
                CampaignEvent(
                    campaign_id=campaign_id,
                    previous_state=current.value,
                    new_state=target_state.value,
                    timestamp=now_iso,
                    reason=reason,
                    source=source,
                    metadata_json=json.dumps(metadata or {}),
                )
            )
            conn.commit()

        return self.get_campaign(campaign_id)

    def record_retry_attempt(
        self,
        campaign_id: str,
        error_message: str,
    ) -> CampaignRecord:
        """Records error and increments retry_count safely."""
        now_iso = datetime.now(timezone.utc).isoformat()
        clean_error = sanitize_text(error_message)
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE whop_campaigns SET
                    retry_count = retry_count + 1,
                    last_error = ?,
                    last_attempt_at = ?,
                    updated_at = ?
                WHERE campaign_id = ?
                """,
                (clean_error, now_iso, now_iso, campaign_id)
            )
            conn.commit()
        return self.get_campaign(campaign_id)

    def inspect_stale_campaigns(
        self,
        max_age_seconds: int = 3600,
    ) -> List[Dict[str, Any]]:
        """Read-only stale-state inspection mechanism.
        
        Identifies campaigns that have remained in non-terminal, transient states
        (e.g. VALIDATING, CLAIMING, INGESTED, RENDERING, SUBMITTING) longer than max_age_seconds.
        Strictly READ-ONLY: does not modify any database records.
        """
        transient_states = (
            CampaignState.VALIDATING.value,
            CampaignState.CLAIMING.value,
            CampaignState.INGESTED.value,
            CampaignState.RENDERING.value,
            CampaignState.SUBMITTING.value,
        )
        stale_records: List[Dict[str, Any]] = []
        now = datetime.now(timezone.utc)

        with self._get_connection() as conn:
            placeholders = ",".join(["?"] * len(transient_states))
            rows = conn.execute(
                f"SELECT * FROM whop_campaigns WHERE current_state IN ({placeholders})",
                transient_states
            ).fetchall()

            for r in rows:
                updated_at_str = r["updated_at"]
                try:
                    updated_dt = datetime.fromisoformat(updated_at_str)
                    age_seconds = (now - updated_dt).total_seconds()
                except Exception:
                    age_seconds = 999999.0

                if age_seconds > max_age_seconds:
                    stale_records.append({
                        "campaign_id": r["campaign_id"],
                        "title": r["title"],
                        "current_state": r["current_state"],
                        "updated_at": updated_at_str,
                        "age_seconds": round(age_seconds, 1),
                        "possible_stale": True,
                    })

        return stale_records

    # --------------------------------------------------------------------------
    # Step 4: CampaignBrief Persistence & Idempotency Methods
    # --------------------------------------------------------------------------

    def save_campaign_brief(
        self,
        brief: WhopCampaignBrief,
        source: str = "whop_brief_parser",
    ) -> Tuple[WhopCampaignBrief, bool]:
        """Persists a CampaignBrief idempotently.
        
        Returns (brief, is_new).
        If (campaign_id, guideline_hash) already exists:
          Updates record and logs BRIEF_REUSED event.
        If new:
          Inserts record and logs BRIEF_PARSED event.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        campaign_id = brief.campaign_id
        g_hash = brief.guideline_hash
        brief_json_str = json.dumps(brief.to_dict())

        rules_cnt = len(brief.rules)
        mandatory_cnt = sum(1 for r in brief.rules if r.mandatory)
        prohibited_cnt = sum(1 for r in brief.rules if r.prohibited)
        unresolved_cnt = sum(1 for r in brief.rules if r.status == "INTERPRETATION_REQUIRED")

        with self._get_connection() as conn:
            # Check existing campaign state
            c_row = conn.execute(
                "SELECT current_state FROM whop_campaigns WHERE campaign_id = ?",
                (campaign_id,)
            ).fetchone()
            curr_state = c_row["current_state"] if c_row else CampaignState.ELIGIBLE.value

            existing = conn.execute(
                "SELECT id FROM whop_campaign_briefs WHERE campaign_id = ? AND guideline_hash = ?",
                (campaign_id, g_hash)
            ).fetchone()

            if existing:
                # Update existing brief idempotently
                conn.execute(
                    """
                    UPDATE whop_campaign_briefs SET
                        guideline_source_type = ?,
                        guideline_source_reference = ?,
                        parsing_status = ?,
                        brief_json = ?,
                        rules_count = ?,
                        mandatory_rules_count = ?,
                        prohibited_rules_count = ?,
                        unresolved_rules_count = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        brief.guideline_source_type,
                        brief.guideline_source_reference,
                        brief.parsing_status.value,
                        brief_json_str,
                        rules_cnt,
                        mandatory_cnt,
                        prohibited_cnt,
                        unresolved_cnt,
                        now_iso,
                        existing["id"],
                    )
                )

                self._record_event_tx(
                    conn,
                    CampaignEvent(
                        campaign_id=campaign_id,
                        previous_state=curr_state,
                        new_state=curr_state,
                        timestamp=now_iso,
                        reason="BRIEF_REUSED",
                        source=source,
                        metadata_json=json.dumps({
                            "guideline_hash": g_hash,
                            "rules_count": rules_cnt,
                            "parsing_status": brief.parsing_status.value,
                        }),
                    )
                )
                conn.commit()
                return brief, False

            else:
                # Insert new brief
                conn.execute(
                    """
                    INSERT INTO whop_campaign_briefs (
                        campaign_id, guideline_hash, guideline_source_type, guideline_source_reference,
                        parsing_status, brief_json, rules_count, mandatory_rules_count,
                        prohibited_rules_count, unresolved_rules_count, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        campaign_id,
                        g_hash,
                        brief.guideline_source_type,
                        brief.guideline_source_reference,
                        brief.parsing_status.value,
                        brief_json_str,
                        rules_cnt,
                        mandatory_cnt,
                        prohibited_cnt,
                        unresolved_cnt,
                        now_iso,
                        now_iso,
                    )
                )

                self._record_event_tx(
                    conn,
                    CampaignEvent(
                        campaign_id=campaign_id,
                        previous_state=curr_state,
                        new_state=curr_state,
                        timestamp=now_iso,
                        reason="BRIEF_PARSED",
                        source=source,
                        metadata_json=json.dumps({
                            "guideline_hash": g_hash,
                            "rules_count": rules_cnt,
                            "mandatory_count": mandatory_cnt,
                            "prohibited_count": prohibited_cnt,
                            "unresolved_count": unresolved_cnt,
                            "parsing_status": brief.parsing_status.value,
                        }),
                    )
                )
                conn.commit()
                return brief, True

    def get_latest_campaign_brief(self, campaign_id: str) -> Optional[WhopCampaignBrief]:
        """Retrieves the most recently created or updated CampaignBrief for a campaign."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT brief_json FROM whop_campaign_briefs WHERE campaign_id = ? ORDER BY id DESC LIMIT 1",
                (campaign_id,)
            ).fetchone()
            if not row:
                return None
            return WhopCampaignBrief.from_dict(json.loads(row["brief_json"]))

    def list_campaign_briefs(self, campaign_id: str) -> List[WhopCampaignBrief]:
        """Retrieves all versioned CampaignBrief records for a campaign chronologically."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT brief_json FROM whop_campaign_briefs WHERE campaign_id = ? ORDER BY id ASC",
                (campaign_id,)
            ).fetchall()
            return [WhopCampaignBrief.from_dict(json.loads(r["brief_json"])) for r in rows]


    def _insert_campaign_tx(self, conn: sqlite3.Connection, r: CampaignRecord) -> None:
        conn.execute(
            """
            INSERT INTO whop_campaigns (
                campaign_id, title, campaign_url, payout_raw, cpm,
                platforms_json, source_urls_json, guideline_urls_json,
                recommended_account, current_state, discovered_at, updated_at,
                last_error, retry_count, last_attempt_at, job_id, submission_url, submitted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                r.campaign_id,
                r.title,
                r.campaign_url,
                r.payout_raw,
                r.cpm,
                json.dumps(r.platforms),
                json.dumps(r.source_urls),
                json.dumps(r.guideline_urls),
                r.recommended_account,
                r.current_state.value,
                r.discovered_at,
                r.updated_at,
                r.last_error,
                r.retry_count,
                r.last_attempt_at,
                r.job_id,
                r.submission_url,
                r.submitted_at,
            )
        )

    def _update_state_tx(
        self,
        conn: sqlite3.Connection,
        campaign_id: str,
        state: CampaignState,
        updated_at: str,
    ) -> None:
        conn.execute(
            "UPDATE whop_campaigns SET current_state = ?, updated_at = ? WHERE campaign_id = ?",
            (state.value, updated_at, campaign_id)
        )

    def _record_event_tx(self, conn: sqlite3.Connection, event: CampaignEvent) -> None:
        # Sanitize event metadata
        safe_meta = sanitize_text(event.metadata_json)
        safe_reason = sanitize_text(event.reason)
        conn.execute(
            """
            INSERT INTO whop_campaign_events (
                campaign_id, previous_state, new_state, timestamp, reason, source, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.campaign_id,
                event.previous_state,
                event.new_state,
                event.timestamp,
                safe_reason,
                event.source,
                safe_meta,
            )
        )

    def _row_to_record(self, row: sqlite3.Row) -> CampaignRecord:
        return CampaignRecord(
            campaign_id=row["campaign_id"],
            title=row["title"],
            campaign_url=row["campaign_url"],
            payout_raw=row["payout_raw"],
            cpm=row["cpm"],
            platforms=json.loads(row["platforms_json"] or "[]"),
            source_urls=json.loads(row["source_urls_json"] or "[]"),
            guideline_urls=json.loads(row["guideline_urls_json"] or "[]"),
            recommended_account=row["recommended_account"],
            current_state=CampaignState(row["current_state"]),
            discovered_at=row["discovered_at"],
            updated_at=row["updated_at"],
            last_error=row["last_error"],
            retry_count=row["retry_count"],
            last_attempt_at=row["last_attempt_at"],
            job_id=row["job_id"],
            submission_url=row["submission_url"],
            submitted_at=row["submitted_at"],
        )

    def _row_to_autoclip_job(self, row: sqlite3.Row) -> WhopAutoClipJobRecord:
        return WhopAutoClipJobRecord(
            id=row["id"],
            campaign_id=row["campaign_id"],
            guideline_hash=row["guideline_hash"],
            autoclip_job_id=row["autoclip_job_id"],
            idempotency_key=row["idempotency_key"],
            status=row["status"],
            request_hash=row["request_hash"],
            source_hash=row["source_hash"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_error=row["last_error"],
            metadata_json=row["metadata_json"] or "{}",
        )

    def save_autoclip_job(self, record: WhopAutoClipJobRecord) -> WhopAutoClipJobRecord:
        """Persists or updates an AutoClip job record in the ledger."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO whop_autoclip_jobs (
                    campaign_id, guideline_hash, autoclip_job_id, idempotency_key,
                    status, request_hash, source_hash, created_at, updated_at,
                    last_error, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(idempotency_key) DO UPDATE SET
                    status=excluded.status,
                    autoclip_job_id=excluded.autoclip_job_id,
                    updated_at=excluded.updated_at,
                    last_error=excluded.last_error,
                    metadata_json=excluded.metadata_json
                RETURNING id;
                """,
                (
                    record.campaign_id,
                    record.guideline_hash,
                    record.autoclip_job_id,
                    record.idempotency_key,
                    record.status,
                    record.request_hash,
                    record.source_hash,
                    record.created_at,
                    record.updated_at,
                    record.last_error,
                    record.metadata_json,
                ),
            )
            row = cursor.fetchone()
            record_id = row[0] if row else record.id
            conn.commit()

        record.id = record_id
        return record

    def get_autoclip_job(
        self,
        campaign_id: str,
        guideline_hash: str,
        source_hash: str,
    ) -> Optional[WhopAutoClipJobRecord]:
        """Finds an existing AutoClip job by campaign, guideline hash, and source hash."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM whop_autoclip_jobs
                WHERE campaign_id = ? AND guideline_hash = ? AND source_hash = ?
                ORDER BY id DESC LIMIT 1;
                """,
                (campaign_id, guideline_hash, source_hash),
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_autoclip_job(row)
        return None

    def get_autoclip_job_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> Optional[WhopAutoClipJobRecord]:
        """Finds an existing AutoClip job by its unique deterministic idempotency key."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM whop_autoclip_jobs WHERE idempotency_key = ? LIMIT 1;",
                (idempotency_key,),
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_autoclip_job(row)
        return None

    def get_autoclip_job_by_job_id(
        self,
        autoclip_job_id: str,
    ) -> Optional[WhopAutoClipJobRecord]:
        """Finds an existing AutoClip job by the AutoClip server job ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM whop_autoclip_jobs WHERE autoclip_job_id = ? LIMIT 1;",
                (autoclip_job_id,),
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_autoclip_job(row)
        return None

    def update_autoclip_job_status(
        self,
        autoclip_job_id: str,
        status: str,
        last_error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[WhopAutoClipJobRecord]:
        """Updates the status and metadata of an AutoClip job."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM whop_autoclip_jobs WHERE autoclip_job_id = ? LIMIT 1;",
                (autoclip_job_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            current_meta = {}
            if row["metadata_json"]:
                try:
                    current_meta = json.loads(row["metadata_json"])
                except Exception:
                    pass
            if metadata:
                current_meta.update(metadata)

            meta_json = json.dumps(current_meta)

            cursor.execute(
                """
                UPDATE whop_autoclip_jobs
                SET status = ?, updated_at = ?, last_error = ?, metadata_json = ?
                WHERE autoclip_job_id = ?;
                """,
                (status, now_iso, last_error, meta_json, autoclip_job_id),
            )
            conn.commit()

            cursor.execute(
                "SELECT * FROM whop_autoclip_jobs WHERE autoclip_job_id = ? LIMIT 1;",
                (autoclip_job_id,),
            )
            updated_row = cursor.fetchone()
            if updated_row:
                return self._row_to_autoclip_job(updated_row)
        return None

    def list_autoclip_jobs(
        self,
        campaign_id: Optional[str] = None,
    ) -> List[WhopAutoClipJobRecord]:
        """Lists AutoClip jobs, optionally filtered by campaign."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if campaign_id:
                cursor.execute(
                    "SELECT * FROM whop_autoclip_jobs WHERE campaign_id = ? ORDER BY id DESC;",
                    (campaign_id,),
                )
            else:
                cursor.execute("SELECT * FROM whop_autoclip_jobs ORDER BY id DESC;")
            rows = cursor.fetchall()
            return [self._row_to_autoclip_job(r) for r in rows]

    # ==========================================================================
    # Step 6: QA Report Persistence
    # ==========================================================================

    def _row_to_qa_report(self, row: sqlite3.Row) -> WhopJobQAReport:
        """Hydrates a WhopJobQAReport from an SQLite row."""
        clips_raw = json.loads(row["clips_json"]) if row["clips_json"] else []
        clips: List[ClipQARecord] = []
        for c in clips_raw:
            t_raw = c.get("technical_qa", {})
            tech_qa = ClipTechnicalQAResult(
                clip_id=t_raw.get("clip_id", c.get("clip_id", "")),
                duration_s=float(t_raw.get("duration_s", 0.0)),
                width=int(t_raw.get("width", 0)),
                height=int(t_raw.get("height", 0)),
                fps=float(t_raw.get("fps", 0.0)),
                video_codec=str(t_raw.get("video_codec", "")),
                audio_codec=str(t_raw.get("audio_codec", "")),
                channels=int(t_raw.get("channels", 2)),
                sample_rate=int(t_raw.get("sample_rate", 48000)),
                mean_volume_db=float(t_raw.get("mean_volume_db", -14.0)),
                true_peak_db=float(t_raw.get("true_peak_db", -1.5)),
                av_sync_diff_s=float(t_raw.get("av_sync_diff_s", 0.0)),
                decode_ok=bool(t_raw.get("decode_ok", True)),
                broll_coverage_pct=float(t_raw.get("broll_coverage_pct", 0.0)),
                longest_a_roll_gap_s=float(t_raw.get("longest_a_roll_gap_s", 0.0)),
                file_size_bytes=int(t_raw.get("file_size_bytes", 0)),
                warnings=list(t_raw.get("warnings", [])),
                rejection_reasons=list(t_raw.get("rejection_reasons", [])),
                is_valid=bool(t_raw.get("is_valid", True)),
            )

            comp_results: List[RuleComplianceResult] = []
            for cr in c.get("compliance_results", []):
                stat_val = cr.get("status", "UNKNOWN")
                try:
                    stat_enum = RuleComplianceStatus(stat_val)
                except Exception:
                    stat_enum = RuleComplianceStatus.UNKNOWN
                comp_results.append(
                    RuleComplianceResult(
                        rule_id=cr.get("rule_id", ""),
                        rule_text=cr.get("rule_text", ""),
                        category=cr.get("category", ""),
                        mandatory=bool(cr.get("mandatory", False)),
                        status=stat_enum,
                        reason=cr.get("reason", ""),
                        evidence=cr.get("evidence", {}),
                    )
                )

            clips.append(
                ClipQARecord(
                    clip_id=c.get("clip_id", ""),
                    candidate_index=int(c.get("candidate_index", 0)),
                    technical_qa=tech_qa,
                    compliance_results=comp_results,
                    artifact_path=c.get("artifact_path", ""),
                    drive_file_id=c.get("drive_file_id"),
                    artifact_url=c.get("artifact_url"),
                    is_durable=bool(c.get("is_durable", False)),
                    is_distinct=bool(c.get("is_distinct", True)),
                    quality_score=float(c.get("quality_score", 100.0)),
                    is_valid=bool(c.get("is_valid", True)),
                    rejection_summary=list(c.get("rejection_summary", [])),
                    metadata=c.get("metadata", {}),
                )
            )

        unsupp_raw = json.loads(row["unsupported_rules_json"]) if row["unsupported_rules_json"] else []
        unsupported_rules: List[RuleComplianceResult] = []
        for ur in unsupp_raw:
            stat_val = ur.get("status", "UNSUPPORTED_REQUIRES_REVIEW")
            try:
                stat_enum = RuleComplianceStatus(stat_val)
            except Exception:
                stat_enum = RuleComplianceStatus.UNSUPPORTED_REQUIRES_REVIEW
            unsupported_rules.append(
                RuleComplianceResult(
                    rule_id=ur.get("rule_id", ""),
                    rule_text=ur.get("rule_text", ""),
                    category=ur.get("category", ""),
                    mandatory=bool(ur.get("mandatory", False)),
                    status=stat_enum,
                    reason=ur.get("reason", ""),
                    evidence=ur.get("evidence", {}),
                )
            )

        return WhopJobQAReport(
            id=row["id"],
            campaign_id=row["campaign_id"],
            guideline_hash=row["guideline_hash"],
            autoclip_job_id=row["autoclip_job_id"],
            artifact_hash=row["artifact_hash"],
            qa_status=row["qa_status"],
            overall_quality_score=float(row["overall_quality_score"]),
            valid_clips_count=int(row["valid_clips_count"]),
            total_clips_evaluated=int(row["total_clips_evaluated"]),
            clips=clips,
            compliance_summary=json.loads(row["compliance_summary_json"]) if row["compliance_summary_json"] else {},
            unsupported_rules=unsupported_rules,
            warnings=json.loads(row["warnings_json"]) if row["warnings_json"] else [],
            failures=json.loads(row["failures_json"]) if row["failures_json"] else [],
            created_at=row["created_at"],
        )

    def save_qa_record(self, report: WhopJobQAReport) -> WhopJobQAReport:
        """Persists an authoritative QA report into SQLite."""
        clips_json = json.dumps([c.to_dict() for c in report.clips])
        comp_summary_json = json.dumps(report.compliance_summary)
        unsupp_json = json.dumps([r.to_dict() for r in report.unsupported_rules])
        warn_json = json.dumps(report.warnings)
        fail_json = json.dumps(report.failures)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO whop_qa_records (
                    campaign_id, guideline_hash, autoclip_job_id, artifact_hash,
                    qa_status, overall_quality_score, valid_clips_count,
                    total_clips_evaluated, clips_json, compliance_summary_json,
                    unsupported_rules_json, warnings_json, failures_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    report.campaign_id,
                    report.guideline_hash,
                    report.autoclip_job_id,
                    report.artifact_hash,
                    report.qa_status,
                    report.overall_quality_score,
                    report.valid_clips_count,
                    report.total_clips_evaluated,
                    clips_json,
                    comp_summary_json,
                    unsupp_json,
                    warn_json,
                    fail_json,
                    report.created_at,
                ),
            )
            report.id = cursor.lastrowid
            conn.commit()

        log.info(
            "Persisted QA report #%s for campaign %s job %s: status=%s, valid_clips=%d/%d",
            report.id,
            report.campaign_id,
            report.autoclip_job_id,
            report.qa_status,
            report.valid_clips_count,
            report.total_clips_evaluated,
        )
        return report

    def get_qa_record(
        self,
        campaign_id: str,
        guideline_hash: str,
        autoclip_job_id: str,
        artifact_hash: str,
    ) -> Optional[WhopJobQAReport]:
        """Looks up an exact existing QA record by campaign, guideline, job, and artifact hash."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM whop_qa_records
                WHERE campaign_id = ? AND guideline_hash = ? AND autoclip_job_id = ? AND artifact_hash = ?
                ORDER BY id DESC LIMIT 1;
                """,
                (campaign_id, guideline_hash, autoclip_job_id, artifact_hash),
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_qa_report(row)
        return None

    def get_latest_qa_record(self, campaign_id: str) -> Optional[WhopJobQAReport]:
        """Retrieves the most recent QA report for a campaign."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM whop_qa_records WHERE campaign_id = ? ORDER BY id DESC LIMIT 1;",
                (campaign_id,),
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_qa_report(row)
        return None

    def list_qa_records(
        self,
        campaign_id: Optional[str] = None,
    ) -> List[WhopJobQAReport]:
        """Lists QA reports, optionally filtered by campaign."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if campaign_id:
                cursor.execute(
                    "SELECT * FROM whop_qa_records WHERE campaign_id = ? ORDER BY id DESC;",
                    (campaign_id,),
                )
            else:
                cursor.execute("SELECT * FROM whop_qa_records ORDER BY id DESC;")
            rows = cursor.fetchall()
            return [self._row_to_qa_report(r) for r in rows]