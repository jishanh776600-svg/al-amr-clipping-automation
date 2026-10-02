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