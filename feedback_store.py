#!/usr/bin/env python3
"""SQLite-backed local learning state, usage decay, and rule audit."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Iterable


SCHEMA_VERSION = 1
RECENT_TERM_HALF_LIFE_DAYS = 30.0
RECENT_TERM_INACTIVE_DAYS = 90.0
RECENT_TERM_LIMIT = 128
RECENT_TERM_MIN_WEIGHT = 0.25


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat()


def _parse_time(value: object, fallback: datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return fallback


class FeedbackStore:
    """Owns Meya's local learned-rule and recent-term state."""

    def __init__(self, user_data_dir: Path):
        self.user_data_dir = user_data_dir
        self.path = user_data_dir / "learning.sqlite3"
        user_data_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self._migrate_legacy_json()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as database:
            database.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS learned_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    canonical TEXT NOT NULL,
                    observed TEXT NOT NULL,
                    canonical_key TEXT NOT NULL,
                    observed_key TEXT NOT NULL,
                    confirmations INTEGER NOT NULL DEFAULT 0,
                    activated INTEGER NOT NULL DEFAULT 0,
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_hit_at TEXT,
                    reverted_at TEXT,
                    owns_glossary_variant INTEGER NOT NULL DEFAULT 0,
                    owns_glossary_canonical INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(canonical_key, observed_key)
                );
                CREATE TABLE IF NOT EXISTS term_usage (
                    canonical_key TEXT PRIMARY KEY,
                    canonical TEXT NOT NULL,
                    accepted_count INTEGER NOT NULL DEFAULT 0,
                    last_accepted_at TEXT NOT NULL,
                    retired_at TEXT
                );
                CREATE INDEX IF NOT EXISTS learned_rules_active_idx
                    ON learned_rules(reverted_at, activated, updated_at);
                CREATE INDEX IF NOT EXISTS term_usage_recent_idx
                    ON term_usage(retired_at, last_accepted_at);
                """
            )
            database.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    @staticmethod
    def _read_json(path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _migrate_legacy_json(self) -> None:
        """Import the old JSON stores once, leaving them intact as a backup."""
        with self._connect() as database:
            migrated = database.execute(
                "SELECT value FROM metadata WHERE key = 'legacy_json_migrated'"
            ).fetchone()
            if migrated:
                return
            now = _iso()
            candidate_payload = self._read_json(self.user_data_dir / "feedback-candidates.json")
            candidates = candidate_payload.get("candidates", {})
            if isinstance(candidates, dict):
                for item in candidates.values():
                    if not isinstance(item, dict):
                        continue
                    canonical = str(item.get("canonical") or "").strip()
                    observed = str(item.get("observed") or "").strip()
                    if not canonical or not observed:
                        continue
                    activated = bool(item.get("activated", False))
                    updated_at = str(item.get("updated_at") or now)
                    database.execute(
                        """
                        INSERT OR IGNORE INTO learned_rules(
                            canonical, observed, canonical_key, observed_key,
                            confirmations, activated, created_at, updated_at
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            canonical,
                            observed,
                            canonical.casefold(),
                            observed.casefold(),
                            max(0, int(item.get("confirmations", 0))),
                            int(activated),
                            updated_at,
                            updated_at,
                        ),
                    )
            usage_payload = self._read_json(self.user_data_dir / "hotword-usage.json")
            usage = usage_payload.get("accepted_terms", usage_payload)
            if isinstance(usage, dict):
                for canonical, count in usage.items():
                    try:
                        accepted_count = max(0, int(count))
                    except (TypeError, ValueError):
                        continue
                    value = str(canonical).strip()
                    if value and accepted_count:
                        database.execute(
                            """
                            INSERT OR IGNORE INTO term_usage(
                                canonical_key, canonical, accepted_count, last_accepted_at
                            ) VALUES(?, ?, ?, ?)
                            """,
                            (value.casefold(), value, accepted_count, now),
                        )
            database.execute(
                "INSERT OR IGNORE INTO metadata(key, value) VALUES('legacy_json_migrated', ?)",
                (now,),
            )

    def observe_rule(
        self,
        canonical: str,
        observed: str,
        *,
        explicit: bool,
        activation_confirmations: int,
    ) -> dict:
        now = _iso()
        canonical_key = canonical.casefold()
        observed_key = observed.casefold()
        with self._connect() as database:
            previous = database.execute(
                """
                SELECT confirmations, reverted_at FROM learned_rules
                WHERE canonical_key = ? AND observed_key = ?
                """,
                (canonical_key, observed_key),
            ).fetchone()
            # A user rollback is a negative signal. If the same mapping is
            # observed again, require fresh evidence instead of immediately
            # reactivating it from the historical confirmation count.
            confirmations = (
                max(0, int(previous["confirmations"])) + 1
                if previous and previous["reverted_at"] is None
                else 1
            )
            activated = explicit or confirmations >= activation_confirmations
            database.execute(
                """
                INSERT INTO learned_rules(
                    canonical, observed, canonical_key, observed_key,
                    confirmations, activated, created_at, updated_at, reverted_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(canonical_key, observed_key) DO UPDATE SET
                    canonical = excluded.canonical,
                    observed = excluded.observed,
                    confirmations = excluded.confirmations,
                    activated = excluded.activated,
                    updated_at = excluded.updated_at,
                    reverted_at = NULL
                """,
                (
                    canonical,
                    observed,
                    canonical_key,
                    observed_key,
                    confirmations,
                    int(activated),
                    now,
                    now,
                ),
            )
            row = database.execute(
                "SELECT * FROM learned_rules WHERE canonical_key = ? AND observed_key = ?",
                (canonical_key, observed_key),
            ).fetchone()
        return dict(row)

    def mark_glossary_ownership(
        self,
        rule_id: int,
        *,
        variant: bool,
        canonical: bool,
    ) -> None:
        with self._connect() as database:
            database.execute(
                """
                UPDATE learned_rules SET
                    owns_glossary_variant = MAX(owns_glossary_variant, ?),
                    owns_glossary_canonical = MAX(owns_glossary_canonical, ?)
                WHERE id = ?
                """,
                (int(variant), int(canonical), rule_id),
            )

    def accept_term(self, canonical: str) -> None:
        now = _iso()
        with self._connect() as database:
            database.execute(
                """
                INSERT INTO term_usage(
                    canonical_key, canonical, accepted_count, last_accepted_at, retired_at
                ) VALUES(?, ?, 1, ?, NULL)
                ON CONFLICT(canonical_key) DO UPDATE SET
                    canonical = excluded.canonical,
                    accepted_count = term_usage.accepted_count + 1,
                    last_accepted_at = excluded.last_accepted_at,
                    retired_at = NULL
                """,
                (canonical.casefold(), canonical, now),
            )

    def recent_terms(
        self,
        *,
        now: datetime | None = None,
        half_life_days: float = RECENT_TERM_HALF_LIFE_DAYS,
        inactive_days: float = RECENT_TERM_INACTIVE_DAYS,
        limit: int = RECENT_TERM_LIMIT,
        min_weight: float = RECENT_TERM_MIN_WEIGHT,
    ) -> dict[str, float]:
        current = now or _utc_now()
        with self._connect() as database:
            rows = database.execute(
                """
                SELECT canonical_key, canonical, accepted_count, last_accepted_at
                FROM term_usage WHERE retired_at IS NULL
                ORDER BY last_accepted_at DESC
                """
            ).fetchall()
            selected: list[tuple[str, float]] = []
            retired: list[str] = []
            for position, row in enumerate(rows):
                last = _parse_time(row["last_accepted_at"], current)
                age_days = max(0.0, (current - last).total_seconds() / 86_400.0)
                weight = float(row["accepted_count"]) * math.pow(0.5, age_days / half_life_days)
                if position >= limit or age_days >= inactive_days or weight < min_weight:
                    retired.append(str(row["canonical_key"]))
                else:
                    selected.append((str(row["canonical"]), weight))
            if retired:
                retired_at = _iso(current)
                database.executemany(
                    "UPDATE term_usage SET retired_at = ? WHERE canonical_key = ?",
                    ((retired_at, key) for key in retired),
                )
        return dict(selected)

    def record_rule_hits(self, changes: Iterable[tuple[str, str]]) -> None:
        now = _iso()
        keys = {(target.casefold(), source.casefold()) for source, target in changes}
        if not keys:
            return
        with self._connect() as database:
            database.executemany(
                """
                UPDATE learned_rules SET
                    hit_count = hit_count + 1,
                    last_hit_at = ?,
                    updated_at = ?
                WHERE canonical_key = ? AND observed_key = ?
                    AND activated = 1 AND reverted_at IS NULL
                """,
                ((now, now, canonical, observed) for canonical, observed in keys),
            )

    def list_rules(self) -> list[dict]:
        with self._connect() as database:
            rows = database.execute(
                """
                SELECT id, canonical, observed, confirmations, activated, hit_count,
                       created_at, updated_at, last_hit_at
                FROM learned_rules
                WHERE reverted_at IS NULL
                ORDER BY activated DESC, COALESCE(last_hit_at, updated_at) DESC, id DESC
                """
            ).fetchall()
        return [
            {
                **dict(row),
                "activated": bool(row["activated"]),
                "evidence": f"用户确认 {row['confirmations']} 次",
            }
            for row in rows
        ]

    def get_rule(self, rule_id: int) -> dict | None:
        with self._connect() as database:
            row = database.execute(
                "SELECT * FROM learned_rules WHERE id = ? AND reverted_at IS NULL",
                (rule_id,),
            ).fetchone()
        return dict(row) if row else None

    def mark_reverted(self, rule_id: int) -> None:
        now = _iso()
        with self._connect() as database:
            database.execute(
                "UPDATE learned_rules SET reverted_at = ?, activated = 0, updated_at = ? WHERE id = ?",
                (now, now, rule_id),
            )
