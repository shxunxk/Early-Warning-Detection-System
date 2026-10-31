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
INTERVENTION_STATUSES = ["OPEN", "IN_PROGRESS", "COMPLETED", "DEFERRED", "ESCALATED"]
NOTE_TYPES = ["NOTE", "RECLASSIFICATION", "INFO_REQUEST", "OVERRIDE"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    alert_id VARCHAR PRIMARY KEY, run_id VARCHAR, customer_id VARCHAR,
    created_at TIMESTAMP, as_of_date DATE, original_score DOUBLE, original_tier VARCHAR,
    confidence VARCHAR, reasons_json VARCHAR, evidence_json VARCHAR);
CREATE TABLE IF NOT EXISTS analyst_decisions (
    decision_id VARCHAR PRIMARY KEY, alert_id VARCHAR, decision VARCHAR, reason VARCHAR,
    analyst_id VARCHAR, decided_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS interventions (
    intervention_id VARCHAR PRIMARY KEY, customer_id VARCHAR NOT NULL,
    facility_id VARCHAR, alert_id VARCHAR, run_id VARCHAR,
    warning_category VARCHAR, contributing_indicators VARCHAR,
    recommended_intervention VARCHAR, assigned_owner VARCHAR,
    priority VARCHAR, target_date DATE, status VARCHAR DEFAULT 'OPEN',
    notes VARCHAR, outcome VARCHAR,
    created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL);
CREATE TABLE IF NOT EXISTS intervention_history (
    history_id VARCHAR PRIMARY KEY, intervention_id VARCHAR NOT NULL,
    field_changed VARCHAR NOT NULL, old_value VARCHAR, new_value VARCHAR,
    changed_by VARCHAR NOT NULL, changed_at TIMESTAMP NOT NULL);
CREATE TABLE IF NOT EXISTS investigation_notes (
    note_id VARCHAR PRIMARY KEY, customer_id VARCHAR NOT NULL,
    alert_id VARCHAR, intervention_id VARCHAR,
    note_type VARCHAR NOT NULL, content VARCHAR NOT NULL,
    analyst_id VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL);
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


# ---------------------------------------------------------------------------
# Interventions
# ---------------------------------------------------------------------------

def create_intervention(customer_id: str, facility_id: str | None,
                        alert_id: str | None, run_id: str | None,
                        warning_category: str, indicators: list[str],
                        recommendation: str, owner: str, priority: str,
                        target_date, notes: str = "") -> str:
    iid = f"INT-{uuid.uuid4().hex[:8].upper()}"
    now = datetime.now()
    with _con() as con:
        con.execute(
            "INSERT INTO interventions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [iid, customer_id, facility_id, alert_id, run_id,
             warning_category, json.dumps(indicators), recommendation,
             owner, priority, pd.Timestamp(target_date).date() if target_date else None,
             "OPEN", notes, None, now, now])
    return iid


def update_intervention(intervention_id: str, changed_by: str, **fields) -> None:
    with _con() as con:
        current = con.execute(
            "SELECT * FROM interventions WHERE intervention_id = ?",
            [intervention_id]).df()
        if current.empty:
            raise ValueError(f"Intervention {intervention_id} not found")
        now = datetime.now()
        for field, new_val in fields.items():
            old_val = str(current.iloc[0].get(field, ""))
            con.execute(
                "INSERT INTO intervention_history VALUES (?,?,?,?,?,?,?)",
                [f"IH-{uuid.uuid4().hex[:8].upper()}", intervention_id,
                 field, old_val, str(new_val), changed_by, now])
            con.execute(
                f"UPDATE interventions SET {field} = ?, updated_at = ? "
                "WHERE intervention_id = ?", [new_val, now, intervention_id])


def get_interventions(customer_id: str | None = None,
                      status: str | None = None,
                      owner: str | None = None) -> pd.DataFrame:
    clauses, params = [], []
    if customer_id:
        clauses.append("customer_id = ?"); params.append(customer_id)
    if status:
        clauses.append("status = ?"); params.append(status)
    if owner:
        clauses.append("assigned_owner = ?"); params.append(owner)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with _con() as con:
        return con.execute(
            f"SELECT * FROM interventions{where} ORDER BY updated_at DESC",
            params).df()


def intervention_summary() -> dict:
    with _con() as con:
        df = con.execute("SELECT status, count(*) c FROM interventions GROUP BY status").df()
        by_status = dict(zip(df.status, df.c)) if not df.empty else {}
        overdue = con.execute(
            "SELECT count(*) FROM interventions "
            "WHERE status IN ('OPEN','IN_PROGRESS') AND target_date < CURRENT_DATE"
        ).fetchone()[0]
        by_owner = con.execute(
            "SELECT assigned_owner, status, count(*) c FROM interventions "
            "GROUP BY assigned_owner, status").df()
    return dict(by_status=by_status, overdue=overdue, by_owner=by_owner)


def intervention_history(intervention_id: str) -> pd.DataFrame:
    with _con() as con:
        return con.execute(
            "SELECT * FROM intervention_history WHERE intervention_id = ? "
            "ORDER BY changed_at DESC", [intervention_id]).df()


# ---------------------------------------------------------------------------
# Investigation notes
# ---------------------------------------------------------------------------

def add_investigation_note(customer_id: str, alert_id: str | None,
                           intervention_id: str | None, note_type: str,
                           content: str, analyst_id: str) -> str:
    assert note_type in NOTE_TYPES and content.strip() and analyst_id.strip()
    nid = f"NTE-{uuid.uuid4().hex[:8].upper()}"
    with _con() as con:
        con.execute("INSERT INTO investigation_notes VALUES (?,?,?,?,?,?,?,?)",
                    [nid, customer_id, alert_id, intervention_id,
                     note_type, content.strip(), analyst_id.strip(), datetime.now()])
    return nid


def investigation_notes(customer_id: str) -> pd.DataFrame:
    with _con() as con:
        return con.execute(
            "SELECT * FROM investigation_notes WHERE customer_id = ? "
            "ORDER BY created_at DESC", [customer_id]).df()
