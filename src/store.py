"""DuckDB persistence: alerts are frozen at creation; analyst decisions are append-only."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

DB = Path(__file__).resolve().parent.parent / "data" / "app" / "sentinel.duckdb"
DECISIONS = ["CONTINUE_MONITORING", "REQUIRES_INVESTIGATION", "FALSE_POSITIVE",
             "ESCALATE_FOR_REVIEW"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    alert_id VARCHAR PRIMARY KEY, run_id VARCHAR, customer_id VARCHAR,
    created_at TIMESTAMP, as_of_date DATE, original_score DOUBLE, original_tier VARCHAR,
    confidence VARCHAR, reasons_json VARCHAR, evidence_json VARCHAR);
CREATE TABLE IF NOT EXISTS analyst_decisions (
    decision_id VARCHAR PRIMARY KEY, alert_id VARCHAR, decision VARCHAR, reason VARCHAR,
    analyst_id VARCHAR, decided_at TIMESTAMP);
"""


def _con():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB))
    con.execute(SCHEMA)
    return con


def publish_alerts(view: pd.DataFrame, run_id: str, as_of) -> int:
    """Freeze an alert for every Watch+ customer not already alerted in this run."""
    with _con() as con:
        existing = {r[0] for r in con.execute(
            "SELECT customer_id FROM alerts WHERE run_id = ?", [run_id]).fetchall()}
        rows = [(f"AL-{uuid.uuid4().hex[:8].upper()}", run_id, cid, datetime.now(),
                 pd.Timestamp(as_of).date(), float(r.risk_score), r.tier, r.confidence,
                 json.dumps(r.reasons), json.dumps(r.evidence, default=str))
                for cid, r in view[view.tier != "Green"].iterrows() if cid not in existing]
        if rows:
            con.executemany("INSERT INTO alerts VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        return len(rows)


def alerts() -> pd.DataFrame:
    with _con() as con:
        return con.execute("""
            SELECT a.*, d.decision AS latest_decision, d.decided_at AS latest_decided_at,
                   (SELECT count(*) FROM analyst_decisions x WHERE x.alert_id = a.alert_id)
                       AS decisions
            FROM alerts a
            LEFT JOIN (SELECT *, row_number() OVER (PARTITION BY alert_id
                       ORDER BY decided_at DESC) rn FROM analyst_decisions) d
              ON d.alert_id = a.alert_id AND d.rn = 1
            ORDER BY a.original_score DESC""").df()


def add_decision(alert_id: str, decision: str, reason: str, analyst: str) -> None:
    assert decision in DECISIONS and reason.strip() and analyst.strip()
    with _con() as con:
        con.execute("INSERT INTO analyst_decisions VALUES (?,?,?,?,?,?)",
                    [f"DEC-{uuid.uuid4().hex[:8].upper()}", alert_id, decision, reason.strip(),
                     analyst.strip(), datetime.now()])


def decisions(alert_id: str) -> pd.DataFrame:
    with _con() as con:
        return con.execute("SELECT decision, reason, analyst_id, decided_at FROM "
                           "analyst_decisions WHERE alert_id = ? ORDER BY decided_at DESC",
                           [alert_id]).df()


def latest_status() -> dict:
    a = alerts()
    a = a.sort_values("created_at").groupby("customer_id").tail(1)
    return dict(zip(a.customer_id, a.latest_decision.fillna("OPEN")))
