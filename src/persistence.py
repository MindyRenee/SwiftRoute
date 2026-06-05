"""Lightweight SQLite persistence for balances, jobs, and receipts."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import settings


class PersistenceError(Exception):
    """Database operation failure."""


class Persistence:
    """SQLite-backed persistence for creator balances and job history."""

    _instance: "Persistence | None" = None

    def __new__(cls) -> "Persistence":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_db()
        return cls._instance

    def _init_db(self) -> None:
        db_path = Path(settings.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS balances (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    creator_balance REAL NOT NULL DEFAULT 0.0,
                    node_payout_usd REAL NOT NULL DEFAULT 0.0,
                    updated_at TEXT NOT NULL DEFAULT ''
                );
                INSERT OR IGNORE INTO balances (id, creator_balance, node_payout_usd, updated_at)
                VALUES (1, 0.0, 0.0, '');

                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'queued',
                    zone_id TEXT,
                    zone_name TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    script_b64 TEXT,
                    vm_logs_json TEXT,
                    receipt_json TEXT,
                    payment_json TEXT
                );

                CREATE TABLE IF NOT EXISTS receipts (
                    receipt_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            conn.commit()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(settings.db_path, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            yield conn
        finally:
            conn.close()

    # Balances
    def get_balances(self) -> dict[str, float]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT creator_balance, node_payout_usd FROM balances WHERE id = 1"
            ).fetchone()
        if row is None:
            return {"creator_balance": 0.0, "node_payout_usd": 0.0}
        return {
            "creator_balance": float(row["creator_balance"]),
            "node_payout_usd": float(row["node_payout_usd"]),
        }

    def update_balances(self, creator_delta: float = 0.0, node_delta: float = 0.0) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE balances
                SET creator_balance = MAX(0.0, creator_balance + ?),
                    node_payout_usd = MAX(0.0, node_payout_usd + ?),
                    updated_at = ?
                WHERE id = 1
                """,
                (creator_delta, node_delta, now),
            )
            conn.commit()

    def set_balances(self, creator_balance: float, node_payout_usd: float) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE balances
                SET creator_balance = ?, node_payout_usd = ?, updated_at = ?
                WHERE id = 1
                """,
                (creator_balance, node_payout_usd, now),
            )
            conn.commit()

    # Jobs
    def create_job(
        self,
        job_id: str,
        script_b64: str,
        zone_id: str = "",
        zone_name: str = "",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (job_id, status, zone_id, zone_name, created_at, script_b64)
                VALUES (?, 'queued', ?, ?, ?, ?)
                """,
                (job_id, zone_id, zone_name, now, script_b64),
            )
            conn.commit()

    def update_job_status(self, job_id: str, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            params = (status, now, job_id)
            sql = "UPDATE jobs SET status = ?, completed_at = ? WHERE job_id = ?"
            if status != "completed":
                sql = "UPDATE jobs SET status = ? WHERE job_id = ?"
                params = (status, job_id)
            conn.execute(sql, params)
            conn.commit()

    def update_job_result(
        self,
        job_id: str,
        vm_logs: dict[str, Any] | None = None,
        receipt: dict[str, Any] | None = None,
        payment: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET vm_logs_json = COALESCE(?, vm_logs_json),
                    receipt_json = COALESCE(?, receipt_json),
                    payment_json = COALESCE(?, payment_json)
                WHERE job_id = ?
                """,
                (
                    json.dumps(vm_logs) if vm_logs else None,
                    json.dumps(receipt) if receipt else None,
                    json.dumps(payment) if payment else None,
                    job_id,
                ),
            )
            conn.commit()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def purge_old_jobs(self, max_age_seconds: int) -> int:
        """Delete jobs older than max_age_seconds. Returns count deleted."""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM jobs WHERE completed_at < datetime('now', ?)",
                (f"-{max_age_seconds} seconds",),
            )
            conn.commit()
            return cur.rowcount

    # Receipts
    def store_receipt(self, receipt_id: str, job_id: str, receipt_json: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO receipts (receipt_id, job_id, receipt_json, created_at) VALUES (?, ?, ?, ?)",
                (receipt_id, job_id, receipt_json, now),
            )
            conn.commit()

    def get_receipt(self, receipt_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM receipts WHERE receipt_id = ?", (receipt_id,)
            ).fetchone()
        if row is None:
            return None
        return dict(row)
