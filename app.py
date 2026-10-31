"""Sentinel: Early Warning System prototype.

    uv run streamlit run app.py
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from sentinel import ingest, insights, llm, metrics, scoring, store

ROOT = Path(__file__).parent
SOURCES = {"Sample portfolio (clean)": ROOT / "data/sample/csv",
           "Sample portfolio (dirty, for validation)": ROOT / "data/dirty/csv"}
RULES_PATH = ROOT / "data/sample/rules/risk_signal_definitions.json"
TRUTH_PATH = ROOT / "data/_ground_truth/customer_labels.csv"
TIER_COLORS = {"Green": "#2e7d32", "Watch": "#f9a825", "Amber": "#ef6c00", "Red": "#c62828"}
TIERS = list(TIER_COLORS)
PAGES = ["Upload & Validate", "Portfolio", "Watchlist", "Customer", "Review", "Rules"]

st.set_page_config(page_title="Sentinel EWS", page_icon="🛡️", layout="wide")
ss = st.session_state


# ======================================================================================
# Data + pipeline
# ======================================================================================

@st.cache_data(show_spinner="Loading and validating…")
def load_folder(path: str):
    return ingest.validate(ingest.load_folder(Path(path)))


@st.cache_data(show_spinner="Computing customer metrics…")
def get_metrics(data_key: str, as_of: str, params_json: str, _t):
    return metrics.compute(_t, as_of, json.loads(params_json))


@st.cache_data(show_spinner=False)
def get_roll(data_key: str, as_of: str, _t):
    return insights.roll_rates(_t, as_of)


def rules_key(rules) -> str:
    return hashlib.md5(json.dumps(rules, sort_keys=True).encode()).hexdigest()[:8]


def build_view(t, rules, data_key):
    p = rules["parameters"]
    as_of = pd.Timestamp(p["as_of_date"])
    back = as_of - pd.DateOffset(months=int(p.get("trajectory_lookback_months", 3)))
    pj = json.dumps(p, sort_keys=True)
    m_now = get_metrics(data_key, str(as_of.date()), pj, t)
    m_prev = get_metrics(data_key, str(back.date()), pj, t)
    now = scoring.score(m_now, rules, t["customers"])
    prev = scoring.score(m_prev, rules, t["customers"])
    cu = t["customers"].set_index("customer_id")
    fa = t["facilities"]
    active = fa[fa.facility_status.fillna("ACTIVE") != "CLOSED"]
    prods = active.groupby("customer_id").product_name.apply(lambda s: ", ".join(sorted(set(s))))
    primary = active.sort_values("sanctioned_amount").groupby("customer_id").tail(1).set_index(
        "customer_id").product_name
    _, p_worse = get_roll(data_key, str(as_of.date()), t)
    roll = insights.customer_roll_prob(t, as_of, p_worse)
    v = cu[["customer_name", "customer_type", "customer_segment", "portfolio", "emirate",
            "employment_or_business_category"]].join(now)
    v["products"] = prods
    v["primary_product"] = primary
    v["outstanding"] = m_now["_outstanding"].fillna(0)
    v["current_dpd"] = m_now["current_dpd"].fillna(0)
    v["dpd_bucket"] = insights.bucket(v.current_dpd)
    v["trajectory"] = scoring.trajectory(now, prev)
    v["prev_score"] = prev.risk_score
    v["roll_prob"] = roll.reindex(v.index).fillna(0)
    v["exposure_at_risk"] = v.outstanding * v.roll_prob
    v["top_indicators"] = v.reasons.map(lambda r: " · ".join(x.split(":")[0] for x in r[:3]))
    status = store.latest_status()
    v["review_status"] = v.index.map(lambda c: status.get(c, "—"))
    prev_risk = prev.tier.isin(["Amber", "Red"])
    return v, m_now, prev_risk, as_of


def ensure_data():
    if "tables" not in ss:
        name = next(iter(SOURCES))
        ss.tables, ss.issues, ss.summary = load_folder(str(SOURCES[name]))
        ss.source = name
        ss.data_key = name
    if "rules" not in ss:
        ss.rules = scoring.load_rules(RULES_PATH)


ensure_data()
view, M, PREV_RISK, AS_OF = build_view(ss.tables, ss.rules, ss.data_key)

# ======================================================================================
# Sidebar
# ======================================================================================
with st.sidebar:
    st.title("🛡️ Sentinel EWS")
    st.caption("Early Warning System · prototype")
    page = st.radio("Navigate", PAGES, key="page")
    st.divider()
    st.markdown(f"**Data:** {ss.source}")
    st.markdown(f"**As-of date:** {AS_OF.date()}")
    st.markdown(f"**Rules version:** `{rules_key(ss.rules)}`")
    models = llm.available_models()
    st.markdown(f"**Local LLM:** {'🟢 ' + llm.pick_model(models) if models else '⚪ off (template text)'}")
    st.caption("Score 0–100 · higher = riskier")


def tier_badge(t):
    return f"<span style='background:{TIER_COLORS[t]};color:white;padding:2px 10px;" \
           f"border-radius:10px;font-weight:600'>{t}</span>"


# ======================================================================================
# 1. Upload & Validate
# ======================================================================================
if page == "Upload & Validate":
    st.header("Upload & Validate")
    st.write("Load the 8 portfolio tables as CSV, Excel (one sheet per table) or JSON. "
             "Rows with errors are rejected; warnings are normalised and kept.")
    c1, c2 = st.columns([1, 1])
    with c1:
        choice = st.selectbox("Bundled dataset", list(SOURCES))
        if st.button("Load bundled dataset", type="primary"):
            ss.tables, ss.issues, ss.summary = load_folder(str(SOURCES[choice]))
            ss.source = ss.data_key = choice
            st.rerun()
    with c2:
        files = st.file_uploader("…or upload files", type=["csv", "xlsx", "json"],
                                 accept_multiple_files=True)
        if files and st.button("Validate uploaded files"):
            raw = {}
            for f in files:
                raw.update(ingest.read_any(f.name, f.getvalue()))
            ss.tables, ss.issues, ss.summary = ingest.validate(raw)
            ss.source = f"Upload ({len(files)} files)"
            ss.data_key = ss.source + hashlib.md5(b"".join(f.getvalue()[:2000] for f in files)
                                                  ).hexdigest()
            st.rerun()

    s, i = ss.summary, ss.issues
    k = st.columns(4)
    k[0].metric("Records received", f"{s.received.sum():,}")
    k[1].metric("Accepted", f"{s.accepted.sum():,}")
    k[2].metric("Rejected (errors)", f"{s.rejected.sum():,}")
    k[3].metric("Warnings", f"{(i.severity == 'WARNING').sum():,}")
    st.subheader("Per table")
    st.dataframe(s, hide_index=True, use_container_width=True)
    st.subheader("Relationships")
    t = ss.tables
    rel = pd.DataFrame([
        dict(link="customers → facilities", linked=t["facilities"].customer_id.nunique(),
             of=len(t["customers"])),
        dict(link="facilities → repayments", linked=t["repayments"].facility_id.nunique(),
             of=len(t["facilities"])),
        dict(link="customers → financials", linked=t["financials"].customer_id.nunique(),
             of=len(t["customers"])),
        dict(link="customers → exposure", linked=t["exposure"].customer_id.nunique(),
             of=len(t["customers"]))])
    st.dataframe(rel, hide_index=True)
    st.subheader(f"Validation issues ({len(i)})")
    if len(i):
        c = st.columns(3)
        sev = c[0].multiselect("Severity", ["ERROR", "WARNING"], ["ERROR", "WARNING"])
        tbl = c[1].multiselect("Table", sorted(i.table.unique()))
        typ = c[2].multiselect("Issue type", sorted(i.issue_type.unique()))
        f = i[i.severity.isin(sev)]
        f = f[f.table.isin(tbl)] if tbl else f
        f = f[f.issue_type.isin(typ)] if typ else f
        st.bar_chart(f.issue_type.value_counts())
        st.dataframe(f, hide_index=True, use_container_width=True, height=320)
    else:
        st.success("No issues found. All records accepted and linked.")

# ======================================================================================
# 2. Portfolio
# ======================================================================================
elif page == "Portfolio":
    st.header("Portfolio overview")
    at_risk = view.tier.isin(["Amber", "Red"])
    k = st.columns(5)
    k[0].metric("Customers", len(view))
    k[1].metric("Red", int((view.tier == "Red").sum()))
    k[2].metric("Amber", int((view.tier == "Amber").sum()))
    k[3].metric("Watch", int((view.tier == "Watch").sum()))
    k[4].metric("Outstanding at Amber/Red", f"AED {view[at_risk].outstanding.sum() / 1e6:,.1f}M")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Tier mix by portfolio")
        d = view.groupby(["portfolio", "tier"]).size().reset_index(name="customers")
        st.altair_chart(alt.Chart(d).mark_bar().encode(
            x="customers:Q", y="portfolio:N",
            color=alt.Color("tier:N", scale=alt.Scale(domain=TIERS,
                                                        range=list(TIER_COLORS.values()))),
            order=alt.Order("tier:N")), use_container_width=True)
    with c2:
        st.subheader("Trajectory (vs 3 months ago)")
        d = view.trajectory.value_counts().reset_index()
        d.columns = ["trajectory", "customers"]
        st.altair_chart(alt.Chart(d).mark_bar().encode(x="customers:Q", y=alt.Y(
            "trajectory:N", sort="-x")), use_container_width=True)

    st.subheader("Emerging risk clusters")
    st.caption("Segments whose Amber/Red rate is significantly above the portfolio rate "
               "(Wilson 95% lower bound > portfolio rate, ≥ 3 at-risk customers). "
               "Momentum = change in at-risk share vs 3 months ago.")
    cl = insights.clusters(view, PREV_RISK)
    if len(cl):
        st.dataframe(cl.head(15), hide_index=True, use_container_width=True,
                     column_config={"emerging": st.column_config.CheckboxColumn("Emerging")})

    st.subheader("Roll-rate matrix (month-to-month DPD bucket transitions)")
    matrix, _ = get_roll(ss.data_key, str(AS_OF.date()), ss.tables)
    mm = (matrix * 100).round(1).reset_index().melt(id_vars="b", var_name="to", value_name="pct")
    st.altair_chart(alt.Chart(mm).mark_rect().encode(
        x=alt.X("to:N", sort=insights.BUCKETS, title="Next month"),
        y=alt.Y("b:N", sort=insights.BUCKETS, title="This month"),
        color=alt.Color("pct:Q", scale=alt.Scale(scheme="oranges"))) +
        alt.Chart(mm).mark_text().encode(x=alt.X("to:N", sort=insights.BUCKETS),
                                        y=alt.Y("b:N", sort=insights.BUCKETS),
                                        text=alt.Text("pct:Q", format=".1f")),
        use_container_width=True)

    if TRUTH_PATH.exists() and ss.source.startswith("Sample portfolio (clean)"):
        st.subheader("Accuracy vs planted ground truth")
        ev = insights.evaluate(view, pd.read_csv(TRUTH_PATH))
        k = st.columns(4)
        k[0].metric("High/Critical caught (Amber+)", f"{ev['catch_rate']}%")
        k[1].metric("False alarms on low-risk", f"{ev['false_alarm_rate']}%")
        k[2].metric("Within one tier", f"{ev['within_one']}%")
        k[3].metric("Trap customers flagged", f"{ev['trap_flagged']} / {ev['trap_total']}")
        c1, c2 = st.columns(2)
        c1.caption("Expected (rows) vs predicted (columns)")
        c1.dataframe(ev["confusion"], use_container_width=True)
        c2.caption("Predicted tier by planted archetype")
        c2.dataframe(ev["by_archetype"], use_container_width=True)

# ======================================================================================
# 3. Watchlist
# ======================================================================================
elif page == "Watchlist":
    st.header("Customer risk watchlist")
    f = st.columns(4)
    tiers = f[0].multiselect("Risk tier", TIERS, ["Watch", "Amber", "Red"])
    prod = f[1].multiselect("Product", sorted(ss.tables["facilities"].product_name.unique()))
    seg = f[2].multiselect("Customer segment", sorted(view.customer_segment.dropna().unique()))
    port = f[3].multiselect("Portfolio", sorted(view.portfolio.dropna().unique()))
    g = st.columns(4)
    dpdb = g[0].multiselect("Delinquency status", insights.BUCKETS)
    emir = g[1].multiselect("Emirate", sorted(view.emirate.dropna().unique()))
    traj = g[2].multiselect("Trajectory", sorted(view.trajectory.unique()))
    conf = g[3].multiselect("Confidence", ["High", "Medium", "Low"])
    w = view
    for col, sel in [("tier", tiers), ("customer_segment", seg), ("portfolio", port),
                     ("dpd_bucket", dpdb), ("emirate", emir), ("trajectory", traj),
                     ("confidence", conf)]:
        if sel:
            w = w[w[col].isin(sel)]
    if prod:
        w = w[w.products.fillna("").apply(lambda s: any(p in s for p in prod))]
    w = w.sort_values(["tier_rank", "risk_score", "exposure_at_risk"], ascending=False)
    st.caption(f"{len(w)} customers · sorted by tier, score, exposure at risk")
    table = w.reset_index()[["customer_id", "customer_name", "products", "outstanding",
                             "risk_score", "tier", "confidence", "trajectory", "top_indicators",
                             "dpd_bucket", "monitoring_status", "review_status",
                             "customer_segment", "portfolio"]]
    ev = st.dataframe(
        table, hide_index=True, use_container_width=True, height=520, on_select="rerun",
        selection_mode="single-row",
        column_config={
            "outstanding": st.column_config.NumberColumn("Outstanding (AED)", format="%,.0f"),
            "risk_score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100,
                                                          format="%d"),
            "top_indicators": st.column_config.TextColumn("Contributing indicators",
                                                          width="large")})
    rows = ev.selection.rows if ev and ev.selection else []
    if rows:
        cid = table.iloc[rows[0]].customer_id
        def _open(c=cid):
            ss.customer = c
            ss.page = "Customer"
        st.button(f"Open {cid} →", type="primary", on_click=_open)
    st.download_button("Download watchlist (CSV)", table.to_csv(index=False),
                       "watchlist.csv", "text/csv")

# ======================================================================================
# 4. Customer
# ======================================================================================
elif page == "Customer":
    ids = view.sort_values("risk_score", ascending=False).index.tolist()
    default = ids.index(ss.get("customer", ids[0])) if ss.get("customer") in ids else 0
    cid = st.selectbox("Customer", ids, index=default,
                       format_func=lambda c: f"{c} · {view.at[c, 'customer_name']} · "
                                             f"{view.at[c, 'tier']} {view.at[c, 'risk_score']:.0f}")
    ss.customer = cid
    r = view.loc[cid]
    evd = r.evidence
    st.markdown(f"### {r.customer_name} &nbsp; {tier_badge(r.tier)}", unsafe_allow_html=True)
    st.caption(f"{cid} · {r.customer_type} · {r.employment_or_business_category} · "
               f"{r.customer_segment} · {r.emirate} · {r.products}")
    k = st.columns(6)
    k[0].metric("Risk score", f"{r.risk_score:.0f}", f"{r.risk_score - r.prev_score:+.0f} vs 3m",
                delta_color="inverse")
    k[1].metric("Confidence", r.confidence, f"DCS {r.dcs:.0f}", delta_color="off")
    k[2].metric("Trajectory", r.trajectory)
    k[3].metric("Current DPD", f"{r.current_dpd:.0f}")
    k[4].metric("Roll-forward prob.", f"{100 * r.roll_prob:.0f}%")
    k[5].metric("Outstanding", f"AED {r.outstanding:,.0f}")
    if r.insufficient_evidence:
        st.info("Insufficient evidence: limited history or data. Missing information is not "
                "treated as risk; tier capped at Watch unless hard evidence appears.")
    if r.trigger:
        st.error(f"Hard trigger: {r.trigger}")
    st.markdown(f"**Recommended action:** {r.recommended_action}")

    c1, c2 = st.columns([3, 2])
    with c1:
        st.subheader("How the score was calculated")
        sig = pd.DataFrame(evd["signals"])
        fired = sig[sig.points != 0].sort_values("points", ascending=False)
        if len(fired):
            st.altair_chart(alt.Chart(fired).mark_bar().encode(
                x=alt.X("points:Q", title="Points"), y=alt.Y("name:N", sort="-x", title=None),
                color=alt.condition("datum.points < 0", alt.value("#2e7d32"),
                                    alt.value("#ef6c00")),
                tooltip=["name", "value", "operator", "threshold", "strength", "points"]),
                use_container_width=True)
        st.caption(f"Raw {evd['raw_score']} + mitigation {evd['mitigation']} → "
                   f"score {evd['risk_score']:.0f} → tier {evd['tier']}"
                   + (f" (trigger: {evd['trigger']})" if evd["trigger"] else "")
                   + (" · mitigation blocked by underlying stress"
                      if evd["mitigation_blocked"] else ""))
        for reason in r.reasons:
            st.markdown(f"- {reason}")
        with st.expander("All signals (incl. not evaluated)"):
            sig["status"] = sig.strength.map(lambda s: "not evaluated (no data)" if pd.isna(s)
                                             else ("fired" if s > 0 else "ok"))
            st.dataframe(sig[["id", "name", "category", "value", "operator", "threshold",
                              "strength", "points", "status"]], hide_index=True,
                         use_container_width=True)
    with c2:
        st.subheader("Module indices")
        idx = pd.DataFrame([dict(module=k, index=v) for k, v in evd["indices"].items()])
        st.dataframe(idx, hide_index=True, use_container_width=True)
        st.subheader("AI risk summary")
        model = llm.pick_model(models)
        # Cached per customer + rules version + model: repeat views cost no tokens.
        key = f"sum_{cid}_{rules_key(ss.rules)}_{model}"
        if st.button("Generate summary" + (f" ({model})" if model else " (template)")):
            if model:
                with st.spinner(f"Running {model} locally…"):
                    try:
                        ss[key] = llm.llm_summary(cid, evd, r.reasons, model,
                                                  r.recommended_action)
                    except Exception as e:  # noqa: BLE001
                        ss[key] = dict(text=llm.template_summary(
                            cid, evd, r.reasons, r.customer_name), grounded=True, model=None,
                            error=str(e))
            else:
                ss[key] = dict(text=llm.template_summary(cid, evd, r.reasons, r.customer_name),
                               grounded=True, model=None)
        out = ss.get(key)
        if out:
            if out.get("model") and not out["grounded"]:
                st.warning(f"LLM output contained numbers not in the evidence "
                           f"{out['ungrounded_numbers']}; showing template summary instead.")
                st.write(llm.template_summary(cid, evd, r.reasons, r.customer_name))
            else:
                st.write(out["text"])
            st.caption(f"Source: {out.get('model') or 'deterministic template'}"
                       + (f" · numbers verified against evidence · {out['prompt_tokens']} in / "
                          f"{out['output_tokens']} out tokens · {out['seconds']} s"
                          if out.get("model") else ""))

    t = ss.tables
    st.subheader("History")
    h1, h2 = st.columns(2)
    dl = t["delinquency"][t["delinquency"].customer_id == cid].groupby(
        "snapshot_date").days_past_due.max().reset_index()
    h1.caption("Max days past due (month-end)")
    h1.altair_chart(alt.Chart(dl).mark_area(opacity=0.6, color="#c62828").encode(
        x="snapshot_date:T", y="days_past_due:Q"), use_container_width=True)
    fi = t["financials"][t["financials"].customer_id == cid].copy()
    fi["income"] = fi.salary_credit.fillna(fi.business_receipts)
    long = fi.melt(id_vars="month_end_date", value_vars=["income", "total_inflows",
                                                         "average_balance"])
    h2.caption("Income / inflows / average balance (AED)")
    h2.altair_chart(alt.Chart(long.dropna()).mark_line(point=True).encode(
        x="month_end_date:T", y="value:Q", color="variable:N"), use_container_width=True)
    h3, h4 = st.columns(2)
    ex = t["exposure"][t["exposure"].customer_id == cid]
    h3.caption("Revolving utilisation % and bureau score")
    h3.altair_chart(alt.Chart(ex.melt(id_vars="snapshot_date", value_vars=[
        "revolving_utilisation_pct"]).dropna()).mark_line(point=True).encode(
        x="snapshot_date:T", y="value:Q"), use_container_width=True)
    h4.caption("Obligations: on-us vs other lenders (AED / month)")
    h4.altair_chart(alt.Chart(ex.melt(id_vars="snapshot_date", value_vars=[
        "onus_monthly_obligation", "bureau_external_monthly_obligation"]).dropna()).mark_area(
        opacity=0.6).encode(x="snapshot_date:T", y=alt.Y("value:Q", stack=True),
                            color="variable:N"), use_container_width=True)
    tabs = st.tabs(["Repayments", "Facilities", "Exposure", "Evidence on file"])
    rp = t["repayments"][t["repayments"].customer_id == cid].sort_values("due_date",
                                                                         ascending=False)
    tabs[0].dataframe(rp, hide_index=True, use_container_width=True)
    tabs[1].dataframe(t["facilities"][t["facilities"].customer_id == cid], hide_index=True,
                      use_container_width=True)
    tabs[2].dataframe(ex.sort_values("snapshot_date", ascending=False), hide_index=True,
                      use_container_width=True)
    tabs[3].dataframe(t["exception_evidence"][t["exception_evidence"].customer_id == cid],
                      hide_index=True, use_container_width=True)

# ======================================================================================
# 5. Review
# ======================================================================================
elif page == "Review":
    st.header("Alert review")
    run_id = f"{AS_OF.date()}-{rules_key(ss.rules)}"
    c1, c2 = st.columns([2, 1])
    c1.write("Publishing freezes the current score, tier and evidence for every Watch/Amber/Red "
             "customer. Analyst decisions are appended and never change the frozen alert.")
    if c2.button(f"Publish alerts for run {run_id}", type="primary"):
        n = store.publish_alerts(view, run_id, AS_OF)
        st.success(f"{n} new alerts frozen")
    al = store.alerts()
    if al.empty:
        st.info("No alerts yet. Publish a run first.")
        st.stop()
    al["status"] = al.latest_decision.fillna("OPEN")
    flt = st.multiselect("Status", sorted(al.status.unique()), ["OPEN"]
                         if "OPEN" in al.status.values else [])
    shown = al[al.status.isin(flt)] if flt else al
    st.dataframe(shown[["alert_id", "customer_id", "original_tier", "original_score",
                        "confidence", "status", "decisions", "run_id", "created_at"]],
                 hide_index=True, use_container_width=True, height=260)
    pick = st.selectbox("Alert", shown.alert_id.tolist(),
                        format_func=lambda a: f"{a} · {al.set_index('alert_id').at[a, 'customer_id']}"
                                              f" · {al.set_index('alert_id').at[a, 'original_tier']}")
    if pick:
        a = al.set_index("alert_id").loc[pick]
        live = view.loc[a.customer_id] if a.customer_id in view.index else None
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**Original (frozen)** &nbsp; {tier_badge(a.original_tier)} "
                        f"score **{a.original_score:.0f}** · {a.confidence} confidence",
                        unsafe_allow_html=True)
            for reason in json.loads(a.reasons_json):
                st.markdown(f"- {reason}")
            with st.expander("Frozen evidence JSON"):
                st.json(json.loads(a.evidence_json))
        with c2:
            if live is not None:
                st.markdown(f"**Live (current rules)** &nbsp; {tier_badge(live.tier)} "
                            f"score **{live.risk_score:.0f}**", unsafe_allow_html=True)
                if live.risk_score != a.original_score:
                    st.caption("Live score differs from the frozen alert (rules or data "
                               "changed); the original is preserved.")
            with st.form("decision", clear_on_submit=True):
                dec = st.radio("Decision", store.DECISIONS, format_func=lambda d: d.replace(
                    "_", " ").title())
                reason = st.text_area("Reason (required)")
                analyst = st.text_input("Analyst", value=ss.get("analyst", ""))
                if st.form_submit_button("Record decision", type="primary"):
                    if not reason.strip() or not analyst.strip():
                        st.error("Reason and analyst are required.")
                    else:
                        store.add_decision(pick, dec, reason, analyst)
                        ss.analyst = analyst
                        st.rerun()
        st.subheader("Decision history (append-only)")
        st.dataframe(store.decisions(pick), hide_index=True, use_container_width=True)

# ======================================================================================
# 6. Rules
# ======================================================================================
elif page == "Rules":
    st.header("Risk signal rules")
    st.write("Thresholds, severities and weights are configuration, not code. A signal earns "
             "half its weight at the threshold and full weight at the severe value. Apply to "
             "re-score the whole portfolio instantly.")
    sig = pd.DataFrame(ss.rules["signals"])
    edited = st.data_editor(
        sig[["signal_id", "enabled", "name", "category", "metric", "operator", "threshold",
             "severe", "weight", "lookback_months", "description"]],
        hide_index=True, use_container_width=True, height=560,
        disabled=["signal_id", "name", "category", "metric", "operator", "lookback_months",
                  "description"])
    bands = st.data_editor(pd.DataFrame(ss.rules["score_bands"]), hide_index=True,
                           disabled=["band"], use_container_width=True)
    c1, c2, c3 = st.columns(3)
    if c1.button("Apply and re-score", type="primary"):
        before = view.tier.value_counts().reindex(TIERS, fill_value=0)
        new = copy.deepcopy(ss.rules)
        by_id = edited.set_index("signal_id")
        for s in new["signals"]:
            for col in ("enabled", "threshold", "severe", "weight"):
                s[col] = by_id.at[s["signal_id"], col].item() if hasattr(
                    by_id.at[s["signal_id"], col], "item") else by_id.at[s["signal_id"], col]
        new["score_bands"] = bands.to_dict("records")
        ss.rules = new
        ss.rules_before = before
        st.rerun()
    if c2.button("Reset to file defaults"):
        ss.rules = scoring.load_rules(RULES_PATH)
        st.rerun()
    c3.download_button("Download rules JSON", json.dumps(ss.rules, indent=2, default=str),
                       "risk_signal_definitions.json", "application/json")
    if "rules_before" in ss:
        after = view.tier.value_counts().reindex(TIERS, fill_value=0)
        st.subheader("Effect of last change")
        st.dataframe(pd.DataFrame({"before": ss.rules_before, "after": after,
                                   "change": after - ss.rules_before}).T,
                     use_container_width=True)
