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

from sentinel import (ingest, insights, llm, metrics, ml, scoring, store,
                      correlation, interventions as interv, priority, rbi)

ROOT = Path(__file__).parent
SOURCES = {"Sample portfolio (clean)": ROOT / "data/sample/csv",
           "Sample portfolio (dirty, for validation)": ROOT / "data/dirty/csv"}
RULES_PATH = ROOT / "data/sample/rules/risk_signal_definitions.json"
TRUTH_PATH = ROOT / "data/_ground_truth/customer_labels.csv"
TIER_COLORS = {"Green": "#2e7d32", "Watch": "#f9a825", "Amber": "#ef6c00", "Red": "#c62828"}
TIERS = list(TIER_COLORS)
PAGES = ["Upload & Validate", "Portfolio Dashboard", "Watchlist", "Customer",
         "Investigation", "Review", "Rules", "RBI Reporting", "AI Assistant"]

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
    v["recovery_profile"] = insights.recovery_profiles(t, as_of).reindex(v.index).fillna("Clean")
    v["exposure_at_risk"] = v.outstanding * v.roll_prob
    v["top_indicators"] = v.reasons.map(lambda r: " · ".join(x.split(":")[0] for x in r[:3]))
    status = store.latest_status()
    v["review_status"] = v.index.map(lambda c: status.get(c, "—"))
    prev_risk = prev.tier.isin(["Amber", "Red"])
    # ML second opinion: train on prior-period metrics → current Amber/Red.
    ml_model, ml_features = ml.train_model(m_prev, now)
    if ml_model is not None:
        v["ml_prob"] = ml.predict_proba(ml_model, m_now, ml_features).reindex(v.index)
    else:
        v["ml_prob"] = pd.Series(dtype=float, index=v.index)
    # Phase 2: correlation, priority, recommendations, SMA
    corr = correlation.detect_correlations(v)
    v["correlation_patterns"] = corr["correlation_names"]
    v["n_modules_stressed"] = corr["n_modules_stressed"]
    pri = priority.composite_priority(v, corr["combined_risk_factor"])
    v["priority_score"] = pri["priority_score"]
    v["priority_rank"] = pri["priority_rank"]
    v["recommendations"] = interv.recommend_bulk(v)
    v["primary_recommendation"] = v.recommendations.map(
        lambda r: interv.primary_recommendation(r).get("name", "") if r else "")
    v["sma_category"] = rbi.sma_classify(v.current_dpd)
    return v, m_now, prev_risk, as_of, ml_model, ml_features


def ensure_data():
    if "tables" not in ss:
        name = next(iter(SOURCES))
        ss.tables, ss.issues, ss.summary = load_folder(str(SOURCES[name]))
        ss.source = name
        ss.data_key = name
    if "rules" not in ss:
        ss.rules = scoring.load_rules(RULES_PATH)


ensure_data()
view, M, PREV_RISK, AS_OF, ML_MODEL, ML_FEATURES = build_view(ss.tables, ss.rules, ss.data_key)

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
elif page == "Portfolio Dashboard":
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

    # --- Phase 2: pre-delinquent vs delinquent split ---
    st.subheader("Pre-delinquent vs delinquent")
    pre_del = view[(view.current_dpd == 0) & view.tier.isin(["Watch", "Amber", "Red"])]
    already_del = view[view.current_dpd > 0]
    pc1, pc2 = st.columns(2)
    pc1.metric("Pre-delinquent (DPD=0, Watch+)", f"{len(pre_del)}",
               f"AED {pre_del.outstanding.sum() / 1e6:,.1f}M exposure")
    pc2.metric("Already delinquent (DPD>0)", f"{len(already_del)}",
               f"AED {already_del.outstanding.sum() / 1e6:,.1f}M exposure")

    # --- Score movers ---
    st.subheader("Top score movers vs 3 months ago")
    view["score_delta"] = view.risk_score - view.prev_score
    movers = view[view.score_delta.abs() > 0].sort_values("score_delta", ascending=False)
    top_worse = movers.head(10).reset_index()[["customer_id", "customer_name", "risk_score",
                                                "score_delta", "tier", "trajectory"]]
    top_better = movers.tail(10).sort_values("score_delta").reset_index()[
        ["customer_id", "customer_name", "risk_score", "score_delta", "tier", "trajectory"]]
    mc1, mc2 = st.columns(2)
    with mc1:
        st.caption("Top 10 deteriorating")
        if not top_worse.empty:
            st.altair_chart(alt.Chart(top_worse).mark_bar(color="#c62828").encode(
                x=alt.X("score_delta:Q", title="Score change"),
                y=alt.Y("customer_name:N", sort="-x")), use_container_width=True)
    with mc2:
        st.caption("Top 10 improving")
        if not top_better.empty:
            st.altair_chart(alt.Chart(top_better).mark_bar(color="#2e7d32").encode(
                x=alt.X("score_delta:Q", title="Score change"),
                y=alt.Y("customer_name:N", sort="x")), use_container_width=True)

    # --- Intervention dashboard ---
    st.subheader("Intervention status")
    inv_sum = store.intervention_summary()
    ic = st.columns(4)
    ic[0].metric("Open", inv_sum["by_status"].get("OPEN", 0))
    ic[1].metric("In Progress", inv_sum["by_status"].get("IN_PROGRESS", 0))
    ic[2].metric("Overdue", inv_sum["overdue"])
    ic[3].metric("Completed", inv_sum["by_status"].get("COMPLETED", 0))

    # --- Priority matrix ---
    st.subheader("Priority matrix")
    st.caption("X = P(deterioration) · Y = Outstanding · Color = Tier · Size = Priority score")
    pm = view[view.tier != "Green"].copy()
    if not pm.empty:
        st.altair_chart(alt.Chart(pm.reset_index()).mark_circle().encode(
            x=alt.X("roll_prob:Q", title="P(deterioration)", scale=alt.Scale(domain=[0, 1])),
            y=alt.Y("outstanding:Q", title="Outstanding (AED)"),
            color=alt.Color("tier:N", scale=alt.Scale(domain=TIERS,
                                                       range=list(TIER_COLORS.values()))),
            size=alt.Size("priority_score:Q", scale=alt.Scale(range=[30, 500])),
            tooltip=["customer_id", "customer_name", "tier", "risk_score", "priority_score",
                     "roll_prob", "outstanding"]
        ).interactive(), use_container_width=True)

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
    g = st.columns(5)
    dpdb = g[0].multiselect("Delinquency status", insights.BUCKETS)
    emir = g[1].multiselect("Emirate", sorted(view.emirate.dropna().unique()))
    traj = g[2].multiselect("Trajectory", sorted(view.trajectory.unique()))
    conf = g[3].multiselect("Confidence", ["High", "Medium", "Low"])
    sma_f = g[4].multiselect("SMA Category", rbi.SMA_CATEGORIES)
    w = view
    for col, sel in [("tier", tiers), ("customer_segment", seg), ("portfolio", port),
                     ("dpd_bucket", dpdb), ("emirate", emir), ("trajectory", traj),
                     ("confidence", conf), ("sma_category", sma_f)]:
        if sel:
            w = w[w[col].isin(sel)]
    if prod:
        w = w[w.products.fillna("").apply(lambda s: any(p in s for p in prod))]
    w = w.sort_values(["priority_score", "tier_rank", "risk_score"], ascending=False)
    st.caption(f"{len(w)} customers · sorted by priority score, tier, risk score")
    cols = ["customer_id", "customer_name", "products", "outstanding",
            "risk_score", "tier", "priority_score", "sma_category", "confidence",
            "trajectory", "top_indicators", "primary_recommendation",
            "roll_prob", "recovery_profile", "dpd_bucket",
            "monitoring_status", "review_status", "customer_segment", "portfolio"]
    if "ml_prob" in w.columns and w.ml_prob.notna().any():
        cols.insert(cols.index("roll_prob") + 1, "ml_prob")
    table = w.reset_index()[cols]
    ev = st.dataframe(
        table, hide_index=True, use_container_width=True, height=520, on_select="rerun",
        selection_mode="single-row",
        column_config={
            "outstanding": st.column_config.NumberColumn("Outstanding (AED)", format="%,.0f"),
            "risk_score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100,
                                                          format="%d"),
            "top_indicators": st.column_config.TextColumn("Contributing indicators",
                                                          width="large"),
            "roll_prob": st.column_config.NumberColumn("Roll Prob (30d)", format="%.0%%"),
            "ml_prob": st.column_config.NumberColumn("ML P(det.)", format="%.0%%"),
            "priority_score": st.column_config.ProgressColumn("Priority", min_value=0,
                                                               max_value=100, format="%d")})
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
    k = st.columns(7)
    k[0].metric("Risk score", f"{r.risk_score:.0f}", f"{r.risk_score - r.prev_score:+.0f} vs 3m",
                delta_color="inverse")
    k[1].metric("Confidence", r.confidence, f"DCS {r.dcs:.0f}", delta_color="off")
    k[2].metric("Trajectory", r.trajectory)
    k[3].metric("Current DPD", f"{r.current_dpd:.0f}")
    k[4].metric("Roll-forward prob.", f"{100 * r.roll_prob:.0f}%")
    k[5].metric("Outstanding", f"AED {r.outstanding:,.0f}")
    k[6].metric("Recovery profile", r.recovery_profile)
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

    # ML second opinion
    if ML_MODEL is not None and pd.notna(r.get("ml_prob")):
        st.subheader("ML second opinion")
        mc1, mc2 = st.columns([1, 2])
        mc1.metric("P(deterioration 90d)", f"{100 * r.ml_prob:.0f}%")
        ml_scores = ml.get_scores(ML_MODEL)
        if ml_scores:
            mc1.caption(f"Model: CV AUC {ml_scores['cv_auc']:.3f} · "
                        f"CV F1 {ml_scores['cv_f1']:.3f} · "
                        f"threshold {ml_scores['threshold']:.2f}")
        sv = ml.shap_values(ML_MODEL, M, ML_FEATURES, cid)
        if sv:
            mc2.caption("SHAP top drivers (positive = pushes toward at-risk)")
            shap_df = pd.DataFrame({"feature": sv["features"], "SHAP value": sv["values"],
                                    "metric value": sv["raw_values"]})
            mc2.altair_chart(alt.Chart(shap_df).mark_bar().encode(
                x=alt.X("SHAP value:Q"), y=alt.Y("feature:N", sort="-x"),
                color=alt.condition("datum['SHAP value'] > 0",
                                    alt.value("#c62828"), alt.value("#2e7d32"))),
                use_container_width=True)
            with mc2.expander("SHAP detail"):
                st.dataframe(shap_df, hide_index=True, use_container_width=True)

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

# ======================================================================================
# 7. Investigation Workspace
# ======================================================================================
elif page == "Investigation":
    st.header("Investigation Workspace")
    ids = view.sort_values("priority_score", ascending=False).index.tolist()
    default = ids.index(ss.get("customer", ids[0])) if ss.get("customer") in ids else 0
    cid = st.selectbox("Customer", ids, index=default, key="inv_customer",
                       format_func=lambda c: f"{c} · {view.at[c, 'customer_name']} · "
                                             f"{view.at[c, 'tier']} · Priority #{view.at[c, 'priority_rank']:.0f}")
    ss.customer = cid
    r = view.loc[cid]
    evd = r.evidence

    st.markdown(f"### {r.customer_name} &nbsp; {tier_badge(r.tier)} &nbsp; "
                f"SMA: **{r.sma_category}** &nbsp; Priority: **{r.priority_score:.0f}**",
                unsafe_allow_html=True)
    st.caption(f"{cid} · {r.customer_type} · {r.customer_segment} · "
               f"{r.products} · Outstanding AED {r.outstanding:,.0f}")

    k = st.columns(6)
    k[0].metric("Risk Score", f"{r.risk_score:.0f}", f"{r.risk_score - r.prev_score:+.0f}")
    k[1].metric("Trajectory", r.trajectory)
    k[2].metric("Current DPD", f"{r.current_dpd:.0f}")
    k[3].metric("Roll Prob", f"{100 * r.roll_prob:.0f}%")
    k[4].metric("Recovery Profile", r.recovery_profile)
    k[5].metric("Modules Stressed", int(r.n_modules_stressed))

    # --- Correlation patterns ---
    if r.correlation_patterns:
        st.subheader("Correlation patterns detected")
        for pat_name in r.correlation_patterns:
            st.warning(f"**{pat_name}**")
        overlap = correlation.overlap_warning(evd)
        if overlap:
            st.caption(f"⚠️ Overlap advisory: {overlap}")

    # --- Recommendations ---
    recs = r.recommendations
    if recs:
        st.subheader("Intervention recommendations")
        for rec in recs:
            urgency_color = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}.get(
                rec["urgency"], "⚪")
            with st.expander(f"{urgency_color} {rec['name']} — {rec['urgency']}", expanded=(rec["urgency"] == "CRITICAL")):
                st.write("**Actions:**")
                for action in rec["actions"]:
                    st.markdown(f"- {action}")
                st.caption(f"Supporting indicators: {', '.join(rec['supporting_indicators'])}")

    # --- Module indices over time ---
    st.subheader("Module index breakdown")
    idx = pd.DataFrame([dict(module=k, index=v) for k, v in evd["indices"].items()])
    c1, c2 = st.columns([1, 2])
    c1.dataframe(idx, hide_index=True, use_container_width=True)
    with c2:
        if not idx.empty:
            st.altair_chart(alt.Chart(idx).mark_bar().encode(
                x=alt.X("index:Q", scale=alt.Scale(domain=[0, 100]), title="Stress %"),
                y=alt.Y("module:N", sort="-x"),
                color=alt.condition("datum.index > 50", alt.value("#c62828"), alt.value("#f9a825"))),
                use_container_width=True)

    # --- Indicator sparklines ---
    from sentinel import trends as trend_mod
    m_row = M.loc[cid] if cid in M.index else None
    if m_row is not None:
        timeline = trend_mod.indicator_timeline(m_row)
        if not timeline.empty:
            st.subheader("12-month indicator trends")
            timeline["month"] = range(len(timeline))
            long = timeline.melt(id_vars="month", var_name="indicator", value_name="value")
            st.altair_chart(alt.Chart(long.dropna()).mark_line(point=True).encode(
                x=alt.X("month:O", title="Month (0=oldest)"),
                y=alt.Y("value:Q"), color="indicator:N",
            ).properties(height=200), use_container_width=True)

    # --- Tabs: interventions, notes, facilities ---
    inv_tabs = st.tabs(["Open Interventions", "Investigation Notes", "Create Intervention", "Add Note"])
    with inv_tabs[0]:
        existing = store.get_interventions(customer_id=cid)
        if existing.empty:
            st.info("No interventions recorded for this customer.")
        else:
            st.dataframe(existing[["intervention_id", "warning_category", "status",
                                   "priority", "assigned_owner", "target_date", "notes"]],
                         hide_index=True, use_container_width=True)
            sel_int = st.selectbox("Update intervention", existing.intervention_id.tolist())
            if sel_int:
                with st.form("update_int", clear_on_submit=True):
                    new_status = st.selectbox("New status", store.INTERVENTION_STATUSES)
                    upd_notes = st.text_area("Notes")
                    upd_by = st.text_input("Changed by", value=ss.get("analyst", ""))
                    if st.form_submit_button("Update", type="primary"):
                        updates = {"status": new_status}
                        if upd_notes.strip():
                            updates["notes"] = upd_notes.strip()
                        store.update_intervention(sel_int, upd_by, **updates)
                        st.rerun()

    with inv_tabs[1]:
        notes = store.investigation_notes(cid)
        if notes.empty:
            st.info("No investigation notes yet.")
        else:
            for _, note in notes.iterrows():
                st.markdown(f"**{note.note_type}** by {note.analyst_id} "
                            f"— {note.created_at}")
                st.write(note.content)
                st.divider()

    with inv_tabs[2]:
        with st.form("create_int", clear_on_submit=True):
            st.subheader("Create intervention")
            cat = st.text_input("Warning category", value=r.primary_recommendation or "")
            owner = st.text_input("Assigned owner", value=ss.get("analyst", ""))
            pri_level = st.selectbox("Priority", ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
            target = st.date_input("Target date")
            int_notes = st.text_area("Notes")
            if st.form_submit_button("Create intervention", type="primary"):
                if owner.strip():
                    indicators = [rec["id"] for rec in recs] if recs else []
                    iid = store.create_intervention(
                        cid, None, None, f"{AS_OF.date()}-{rules_key(ss.rules)}",
                        cat, indicators,
                        r.primary_recommendation, owner.strip(), pri_level, target, int_notes)
                    ss.analyst = owner.strip()
                    st.success(f"Intervention {iid} created")
                else:
                    st.error("Owner is required.")

    with inv_tabs[3]:
        with st.form("add_note", clear_on_submit=True):
            st.subheader("Add investigation note")
            note_type = st.selectbox("Type", store.NOTE_TYPES)
            content = st.text_area("Content")
            analyst = st.text_input("Analyst", value=ss.get("analyst", ""), key="note_analyst")
            if st.form_submit_button("Add note", type="primary"):
                if content.strip() and analyst.strip():
                    store.add_investigation_note(cid, None, None, note_type,
                                                 content, analyst)
                    ss.analyst = analyst.strip()
                    st.rerun()
                else:
                    st.error("Content and analyst are required.")

# ======================================================================================
# 8. RBI Reporting
# ======================================================================================
elif page == "RBI Reporting":
    st.header("RBI Credit Risk EWS Reporting")
    st.caption("SMA classification per RBI norms · Currency: AED · Thresholds configurable in Rules")
    report = rbi.generate_report(ss.tables, view, AS_OF, ss.rules)

    # --- Compliance KPIs ---
    comp = report["compliance"]
    k = st.columns(5)
    k[0].metric("Total Customers", comp["total_customers"])
    k[1].metric("At Risk (Amber/Red)", comp["at_risk_count"])
    k[2].metric("Pre-Delinquent EWS", comp["pre_delinquent_count"])
    k[3].metric("Already Delinquent", comp["already_delinquent_count"])
    k[4].metric("High Exposure at Risk", comp["high_exposure_at_risk"])
    st.caption(f"TAT: {comp['tat_days']} days · High exposure threshold: "
               f"AED {comp['exposure_threshold']:,.0f}")

    c1, c2 = st.columns(2)
    # --- SMA Distribution ---
    with c1:
        st.subheader("SMA distribution")
        dist = report["distribution"]
        st.dataframe(dist, hide_index=True, use_container_width=True,
                     column_config={
                         "outstanding_amount": st.column_config.NumberColumn("Outstanding (AED)",
                                                                             format="%,.0f"),
                         "pct_of_portfolio": st.column_config.NumberColumn("% of Portfolio",
                                                                           format="%.1f%%")})
        st.altair_chart(alt.Chart(dist).mark_bar().encode(
            x=alt.X("sma_category:N", sort=rbi.SMA_CATEGORIES, title="SMA Category"),
            y=alt.Y("facility_count:Q", title="Facilities"),
            color=alt.Color("sma_category:N", scale=alt.Scale(
                domain=rbi.SMA_CATEGORIES,
                range=["#2e7d32", "#f9a825", "#ef6c00", "#c62828", "#4a148c"]))),
            use_container_width=True)

    # --- SMA by Portfolio ---
    with c2:
        st.subheader("SMA by portfolio")
        by_port = report["distribution_by_portfolio"]
        if not by_port.empty:
            st.altair_chart(alt.Chart(by_port).mark_bar().encode(
                x=alt.X("outstanding:Q", title="Outstanding (AED)"),
                y=alt.Y("portfolio:N"),
                color=alt.Color("sma_category:N", sort=rbi.SMA_CATEGORIES,
                                scale=alt.Scale(domain=rbi.SMA_CATEGORIES,
                                                range=["#2e7d32", "#f9a825", "#ef6c00",
                                                        "#c62828", "#4a148c"])),
                order="sma_category:N"), use_container_width=True)

    # --- Migration Matrix ---
    st.subheader("SMA migration matrix (month-to-month transitions)")
    mig = report["migration_matrix"]
    mig_pct = (mig * 100).round(1)
    mm = mig_pct.reset_index().melt(id_vars="sma", var_name="to", value_name="pct").rename(
        columns={"sma": "from"})
    st.altair_chart(alt.Chart(mm).mark_rect().encode(
        x=alt.X("to:N", sort=rbi.SMA_CATEGORIES, title="Next Period"),
        y=alt.Y("from:N", sort=rbi.SMA_CATEGORIES, title="Current Period"),
        color=alt.Color("pct:Q", scale=alt.Scale(scheme="oranges"))) +
        alt.Chart(mm).mark_text(fontSize=11).encode(
            x=alt.X("to:N", sort=rbi.SMA_CATEGORIES),
            y=alt.Y("from:N", sort=rbi.SMA_CATEGORIES),
            text=alt.Text("pct:Q", format=".1f")),
        use_container_width=True)

    # --- SMA Trend ---
    st.subheader("SMA trend over time")
    trend_data = report["trend"]
    if not trend_data.empty:
        st.altair_chart(alt.Chart(trend_data).mark_area().encode(
            x=alt.X("month:T", title="Month"),
            y=alt.Y("count:Q", title="Facilities", stack=True),
            color=alt.Color("sma_category:N", sort=rbi.SMA_CATEGORIES,
                            scale=alt.Scale(domain=rbi.SMA_CATEGORIES,
                                            range=["#2e7d32", "#f9a825", "#ef6c00",
                                                    "#c62828", "#4a148c"]))),
            use_container_width=True)

    # --- NPA accounts ---
    npa = view[view.sma_category == "NPA"].sort_values("outstanding", ascending=False)
    if not npa.empty:
        st.subheader(f"NPA accounts ({len(npa)})")
        st.dataframe(npa.reset_index()[["customer_id", "customer_name", "outstanding",
                                         "current_dpd", "risk_score", "tier", "trajectory",
                                         "primary_recommendation"]],
                     hide_index=True, use_container_width=True,
                     column_config={"outstanding": st.column_config.NumberColumn(
                         "Outstanding (AED)", format="%,.0f")})

    # --- Download ---
    st.divider()
    c1, c2 = st.columns(2)
    c1.download_button("📥 Download Full Report (Excel)",
                       rbi.report_to_excel(report),
                       "rbi_ews_report.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    c2.download_button("📥 Download SMA Distribution (CSV)",
                       report["distribution"].to_csv(index=False),
                       "sma_distribution.csv", "text/csv")

# ======================================================================================
# 9. AI Assistant
# ======================================================================================
elif page == "AI Assistant":
    st.header("AI Assistant")
    models = llm.available_models()
    model = llm.pick_model(models)
    if not model:
        st.warning("No local LLM available. Start Ollama with a supported model "
                   "(e.g. `ollama pull qwen3:4b-instruct`) to enable the AI Assistant.")
        st.stop()
    st.caption(f"Using **{model}** via Ollama · responses grounded in customer evidence")
    ids = view.sort_values("risk_score", ascending=False).index.tolist()
    default = ids.index(ss.get("customer", ids[0])) if ss.get("customer") in ids else 0
    chat_cid = st.selectbox("Customer context", ids, index=default, key="chat_cid",
                            format_func=lambda c: f"{c} · {view.at[c, 'customer_name']} · "
                                                  f"{view.at[c, 'tier']}")
    r = view.loc[chat_cid]
    evd = r.evidence
    st.markdown(f"**{r.customer_name}** · {tier_badge(r.tier)} · Score {r.risk_score:.0f}",
                unsafe_allow_html=True)

    if "chat_messages" not in ss or ss.get("chat_context_cid") != chat_cid:
        ss.chat_messages = []
        ss.chat_context_cid = chat_cid

    for msg in ss.chat_messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    if prompt := st.chat_input("Ask about this customer's risk…"):
        ss.chat_messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.write(prompt)

        facts = llm.facts_for(evd, r.reasons, r.recommended_action)
        facts["customer_name"] = r.customer_name
        facts["customer_type"] = r.customer_type
        facts["products"] = r.products
        facts["outstanding"] = f"AED {r.outstanding:,.0f}"
        facts["trajectory"] = r.trajectory
        facts["recovery_profile"] = r.recovery_profile
        facts["roll_forward_prob"] = f"{100 * r.roll_prob:.0f}%"

        import json as _json
        sys_prompt = (llm.SYSTEM + " Answer the analyst's question using ONLY the facts below. "
                      "If you don't know, say so.\n\nFACTS:\n" + _json.dumps(facts, indent=1))
        history = "\n".join(f"{m['role'].upper()}: {m['content']}"
                            for m in ss.chat_messages[-6:])
        full_prompt = history + f"\nASSISTANT:"

        with st.chat_message("assistant"):
            with st.spinner(f"Thinking ({model})…"):
                import urllib.request
                body = _json.dumps(dict(model=model, system=sys_prompt, prompt=full_prompt,
                                        stream=False, think=False,
                                        options=dict(temperature=0.2, num_predict=400))).encode()
                req = urllib.request.Request(f"{llm.OLLAMA}/api/generate", data=body,
                                             headers={"Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(req, timeout=120) as resp:
                        import re
                        res = _json.load(resp)
                    text = re.sub(r"<think>.*?</think>", "", res.get("response", ""),
                                  flags=re.S).strip()
                    ok, bad = llm.grounded(text, facts, r.reasons)
                    if not ok:
                        text += f"\n\n⚠️ _Some numbers could not be verified against evidence: {bad}_"
                except Exception as e:
                    text = f"Error communicating with LLM: {e}"
            st.write(text)
        ss.chat_messages.append({"role": "assistant", "content": text})
