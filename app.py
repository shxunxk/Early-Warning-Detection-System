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

from sentinel import (correlation, forecast, ingest, insights, interventions as interv, llm,
                      metrics, ml, priority, rbi, scoring, store, trends)

ROOT = Path(__file__).parent
# Bundled portfolios: label -> (folder of CSVs, ground-truth labels or None).
SOURCES = {
    "Synthetic Bharat Commercial Bank · 600 customers (clean)":
        (ROOT / "data/sample/csv", ROOT / "data/_ground_truth/customer_labels.csv"),
    "Synthetic Bharat Commercial Bank · dirty (validation test)":
        (ROOT / "data/dirty/csv", None),
    "Northern Plains Commercial Bank · 1,200 customers, MSME-heavy (clean)":
        (ROOT / "data/northern_sme/sample/csv",
         ROOT / "data/northern_sme/_ground_truth/customer_labels.csv"),
    "Northern Plains Commercial Bank · dirty (validation test)":
        (ROOT / "data/northern_sme/dirty/csv", None),
}
SOURCES = {k: v for k, v in SOURCES.items() if v[0].exists()}
RULES_PATH = ROOT / "data/sample/rules/risk_signal_definitions.json"
TIER_COLORS = {"Green": "#2e7d32", "Watch": "#f9a825", "Amber": "#ef6c00", "Red": "#c62828"}
TIERS = list(TIER_COLORS)
SMA_COLORS = ["#2e7d32", "#f9a825", "#ef6c00", "#c62828", "#4a148c"]
PAGES = ["Home", "Upload & Validate", "Portfolio Dashboard", "Watchlist", "Customer",
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


@st.cache_resource(show_spinner="Training delinquency forecast (point-in-time, ~5 s)…")
def get_forecast(data_key: str, params_json: str, _t):
    """Forecast models + validation, or None if LightGBM is unavailable / data too thin."""
    p = json.loads(params_json)
    try:
        return forecast.train(forecast.build_panel(_t, p, p["as_of_date"]))
    except Exception:  # noqa: BLE001 - forecasting is optional; the rest of the app still works
        return None


@st.cache_data(show_spinner=False)
def get_predictions(data_key: str, params_json: str, _t, _m):
    models = get_forecast(data_key, params_json, _t)
    if models is None:
        return None
    pred = forecast.predict(models, _m, _t["customers"], _m.current_dpd)
    pred["forecast_drivers"] = forecast.drivers(models, _m, _t["customers"]).map(", ".join)
    return pred


def rules_key(rules) -> str:
    return hashlib.md5(json.dumps(rules, sort_keys=True).encode()).hexdigest()[:8]


@st.cache_data(show_spinner="Scoring the last 12 months…")
def get_history(data_key: str, rkey: str, as_of: str, _t, _rules):
    return trends.compute_history(_t, _rules, as_of, months_back=12, step_months=3)


@st.cache_data(show_spinner="Building RBI EWS report…")
def get_report(data_key: str, rkey: str, as_of: str, _t, _view, _rules):
    return rbi.generate_report(_t, _view, as_of, _rules)


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
    v = cu[["customer_name", "customer_type", "customer_segment", "portfolio", "region",
            "employment_or_business_category"]].join(now)
    v["products"] = prods
    v["primary_product"] = primary
    v["outstanding"] = m_now["_outstanding"].fillna(0)
    v["current_dpd"] = m_now["current_dpd"].fillna(0)
    v["dpd_bucket"] = insights.bucket(v.current_dpd)
    v["trajectory"] = scoring.trajectory(now, prev)
    v["prev_score"] = prev.risk_score
    v["prev_tier"] = prev.tier
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
    # Delinquency forecast: P(30+ DPD) next month / next quarter for customers below 30 DPD.
    pred = get_predictions(data_key, pj, t, m_now)
    for col in ("p_1m", "p_3m", "forecast_drivers"):
        v[col] = pred[col].reindex(v.index) if pred is not None else None
    # Phase 2: cross-module correlation, composite priority, recommendations, SMA class.
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
        ss.tables, ss.issues, ss.summary = load_folder(str(SOURCES[name][0]))
        ss.source = ss.data_key = name
        ss.truth = SOURCES[name][1]
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


def inr(x, dp: int = 2) -> str:
    """Amount in crore / lakh, the units an Indian credit team actually reads."""
    if x is None or pd.isna(x):
        return "n/a"
    x = float(x)
    sign, a = ("-" if x < 0 else ""), abs(x)
    if a >= 1e7:
        return f"{sign}₹{a / 1e7:,.{dp}f} Cr"
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:,.{dp}f} L"
    return f"{sign}₹{a:,.0f}"


def inr_exact(x) -> str:
    """Exact amount with Indian digit grouping: ₹1,06,00,000 rather than ₹10,600,000."""
    if x is None or pd.isna(x):
        return "n/a"
    n = int(round(float(x)))
    sign, s = ("-" if n < 0 else ""), str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return f"{sign}₹{s}"


# ======================================================================================
# 1. Upload & Validate
# ======================================================================================
FORECAST_FLAG = 0.20  # P(30+ DPD next quarter) at which a performing customer is red-flagged


def open_customer(cid):
    ss.customer = cid
    ss.page = "Customer"


def flag_table(df, cols, key, config=None):
    """Selectable table with an Open-customer button (used by the Home red-flag tabs)."""
    ev = st.dataframe(df.reset_index()[cols], hide_index=True, use_container_width=True,
                      on_select="rerun", selection_mode="single-row", key=key,
                      column_config=config or {}, height=min(38 * (len(df) + 1) + 3, 360))
    rows = ev.selection.rows if ev and ev.selection else []
    if rows:
        cid = df.index[rows[0]]
        st.button(f"Open {cid} →", type="primary", key=f"{key}_open",
                  on_click=open_customer, args=(cid,))


if page == "Home":
    st.header("Early warning briefing")
    st.caption(f"As of {AS_OF.date()} · {ss.source} · {len(view)} customers monitored")
    has_fc = view.p_3m.notna().any()
    alerts_df = store.alerts()
    alerts_df = alerts_df[alerts_df.customer_id.isin(view.index)]  # this dataset only
    open_alerts = int(alerts_df.latest_decision.isna().sum()) if len(alerts_df) else 0
    red = view.tier == "Red"
    amber = view.tier == "Amber"
    exp_1m = float(view.p_1m.sum()) if has_fc else None
    exp_3m = float(view.p_3m.sum()) if has_fc else None
    ear_3m = float((view.outstanding * view.p_3m.fillna(0)).sum()) if has_fc else None

    k = st.columns(3)
    k[0].metric("Red today", int(red.sum()), inr(view[red].outstanding.sum(), 1),
                delta_color="off")
    k[1].metric("Amber today", int(amber.sum()),
                inr(view[amber].outstanding.sum(), 1), delta_color="off")
    k[2].metric("Open alerts", open_alerts, help="Published alerts without an analyst decision")
    k = st.columns(3)
    k[0].metric("New 30+ DPD · 1m", f"{exp_1m:.1f}" if has_fc else "n/a",
                help="Expected customers newly reaching 30+ DPD next month: sum of forecast "
                     "probabilities for customers currently below 30 DPD")
    k[1].metric("New 30+ DPD · 3m", f"{exp_3m:.1f}" if has_fc else "n/a",
                help="Expected customers newly reaching 30+ DPD within the next quarter")
    k[2].metric("At-risk exposure · 3m", inr(ear_3m, 1) if has_fc else "n/a",
                help="Expected exposure at risk next quarter: Σ outstanding × P(30+ DPD "
                     "within 3 months)")

    # ------------------------------------------------------------------ red flags
    likely = view[(view.current_dpd < 30) & (view.p_3m >= FORECAST_FLAG)].sort_values(
        "p_3m", ascending=False) if has_fc else view.iloc[0:0]
    rank = {t: i for i, t in enumerate(TIERS)}
    escalated = view[(view.tier.map(rank) > view.prev_tier.map(rank))
                     & view.tier.isin(["Amber", "Red"])].sort_values("risk_score",
                                                                     ascending=False)
    triggered = view[view.trigger != ""].sort_values("current_dpd", ascending=False)
    clusters = insights.clusters(view, PREV_RISK)
    emerging = clusters[clusters.emerging] if len(clusters) else clusters
    recovering = view[view.trajectory == "Recovering"]

    # ------------------------------------------------------------------ AI briefing
    def pct(x):
        return f"{100 * x:.0f}%"
    facts = dict(
        as_of=str(AS_OF.date()),
        kpis=dict(red=int(red.sum()), amber=int(amber.sum()),
                  expected_new_30dpd_next_month=round(exp_1m or 0, 1),
                  expected_new_30dpd_next_quarter=round(exp_3m or 0, 1),
                  expected_exposure_at_risk_next_quarter_cr=round((ear_3m or 0) / 1e7, 1),
                  open_alerts=open_alerts, hard_trigger_count=len(triggered),
                  likely_to_hit_30dpd_count=len(likely), newly_escalated_count=len(escalated),
                  emerging_segment_count=len(emerging), recovering_count=len(recovering)),
        predicted_to_default=[dict(customer=c, p_next_month=pct(r.p_1m),
                                   p_next_quarter=pct(r.p_3m), tier=r.tier,
                                   current_dpd=int(r.current_dpd), drivers=r.forecast_drivers)
                              for c, r in likely.head(5).iterrows()],
        newly_escalated=[dict(customer=c, from_tier=r.prev_tier, to_tier=r.tier,
                              score=int(r.risk_score)) for c, r in escalated.head(5).iterrows()],
        hard_triggers=[dict(customer=c, trigger=r.trigger, current_dpd=int(r.current_dpd))
                       for c, r in triggered.head(5).iterrows()],
        emerging_segments=[dict(segment=r.segment, at_risk_rate_pct=r.at_risk_rate,
                                portfolio_rate_pct=r.portfolio_rate, lift=r.lift)
                           for _, r in emerging.head(3).iterrows()],
    )
    st.subheader("🧠 AI risk briefing")
    model = llm.pick_model(models)
    bkey = f"brief_{ss.data_key}_{rules_key(ss.rules)}_{model}"
    if st.button("Generate briefing" + (f" ({model})" if model else " (template)")):
        if model:
            with st.spinner(f"Running {model} locally…"):
                try:
                    ss[bkey] = llm.briefing(facts, model)
                except Exception as e:  # noqa: BLE001
                    ss[bkey] = dict(text=llm.template_briefing(facts), model=None, error=str(e))
        else:
            ss[bkey] = dict(text=llm.template_briefing(facts), model=None)
    out = ss.get(bkey)
    with st.container(border=True):
        if out and out.get("model") and out["grounded"]:
            st.markdown(out["text"])
            st.caption(f"Source: {out['model']} · numbers verified against the dashboard · "
                       f"{out['prompt_tokens']} in / {out['output_tokens']} out tokens · "
                       f"{out['seconds']} s")
        else:
            if out and out.get("model"):
                st.warning(f"LLM briefing contained numbers not on the dashboard "
                           f"{out['ungrounded_numbers']}; showing the template briefing.")
            st.markdown(llm.template_briefing(facts))
            st.caption("Source: deterministic template · press the button for an AI briefing"
                       if not out else "Source: deterministic template")

    # ------------------------------------------------------------------ red flag tabs
    st.subheader("🚩 Red flags")
    tabs = st.tabs([f"🔮 Likely to hit 30+ DPD ({len(likely)})",
                    f"🔺 Newly escalated ({len(escalated)})",
                    f"⛔ Hard triggers ({len(triggered)})",
                    f"🧩 Emerging segments ({len(emerging)})",
                    f"🔁 Recovering ({len(recovering)})"])
    pcfg = {"p_1m": st.column_config.ProgressColumn("P(30+ DPD) 1m", min_value=0, max_value=1,
                                                    format="percent"),
            "p_3m": st.column_config.ProgressColumn("P(30+ DPD) 3m", min_value=0, max_value=1,
                                                    format="percent"),
            "outstanding": st.column_config.NumberColumn("Outstanding (₹)", format="%,.0f"),
            "forecast_drivers": st.column_config.TextColumn("Why (model drivers)",
                                                            width="large")}
    with tabs[0]:
        st.caption(f"Customers **below 30 DPD today** with ≥ {FORECAST_FLAG:.0%} forecast "
                   "probability of reaching 30+ DPD within 3 months: act before they miss.")
        if has_fc and len(likely):
            flag_table(likely, ["customer_id", "customer_name", "tier", "current_dpd", "p_1m",
                                "p_3m", "outstanding", "forecast_drivers"], "hf_likely", pcfg)
        else:
            st.info("No forecast available." if not has_fc else "None above the threshold.")
    with tabs[1]:
        st.caption("Tier moved up into Amber or Red compared with 3 months ago.")
        flag_table(escalated, ["customer_id", "customer_name", "prev_tier", "tier",
                               "risk_score", "top_indicators"], "hf_esc")
    with tabs[2]:
        st.caption("Overrides applied: 90+ DPD → Red; 30+ DPD or 2+ consecutive missed "
                   "months → at least Amber.")
        flag_table(triggered, ["customer_id", "customer_name", "tier", "trigger",
                               "current_dpd", "outstanding"], "hf_trig", pcfg)
    with tabs[3]:
        st.caption("Segments with an Amber/Red rate significantly above the portfolio.")
        if len(emerging):
            st.dataframe(emerging[["segment", "customers", "at_risk", "at_risk_rate",
                                   "portfolio_rate", "lift", "momentum_pp",
                                   "exposure_at_risk"]], hide_index=True,
                         use_container_width=True)
        else:
            st.info("No emerging segments.")
    with tabs[4]:
        st.caption("Were Amber/Red 3 months ago, now Green/Watch. Candidates to de-escalate.")
        flag_table(recovering, ["customer_id", "customer_name", "prev_tier", "tier",
                                "risk_score"], "hf_rec")

    # ------------------------------------------------------------------ forecast charts
    if has_fc:
        st.subheader("📈 Delinquency outlook")
        c1, c2 = st.columns(2)
        with c1:
            hist = forecast.history(ss.tables, AS_OF).tail(12)
            hist["kind"] = "Actual"
            nxt = pd.DataFrame([dict(month_end=AS_OF + pd.offsets.MonthEnd(1),
                                     new_30plus=round(exp_1m, 1), kind="Forecast")])
            d = pd.concat([hist, nxt])
            st.caption("Customers newly reaching 30+ DPD per month: last 12 months and the "
                       "next-month forecast")
            st.altair_chart(alt.Chart(d).mark_bar().encode(
                x=alt.X("yearmonth(month_end):O", title=None),
                y=alt.Y("new_30plus:Q", title="Customers"),
                color=alt.Color("kind:N", scale=alt.Scale(domain=["Actual", "Forecast"],
                                                          range=["#90a4ae", "#ef6c00"])),
                tooltip=["yearmonth(month_end)", "new_30plus", "kind"]),
                use_container_width=True)
        with c2:
            seg = view.groupby("primary_product").agg(expected=("p_3m", "sum"),
                                                      exposure=("outstanding", "sum"))
            seg["exposure_at_risk"] = (view.outstanding * view.p_3m.fillna(0)).groupby(
                view.primary_product).sum()
            seg = seg.reset_index().sort_values("expected", ascending=False)
            st.caption("Expected new 30+ DPD customers next quarter, by primary product")
            st.altair_chart(alt.Chart(seg).mark_bar(color="#ef6c00").encode(
                x=alt.X("expected:Q", title="Expected customers"),
                y=alt.Y("primary_product:N", sort="-x", title=None),
                tooltip=["primary_product", alt.Tooltip("expected:Q", format=".1f"),
                         alt.Tooltip("exposure_at_risk:Q", format=",.0f")]),
                use_container_width=True)
        fc = get_forecast(ss.data_key, json.dumps(ss.rules["parameters"], sort_keys=True),
                          ss.tables)
        with st.expander("Model card: how the forecast works and how well it performed"):
            st.markdown(
                "- **Target:** customer reaches **30+ DPD** within 1 month / 3 months. Only "
                "customers below 30 DPD are scored; those already 30+ are covered by roll "
                "rates.\n- **Model:** LightGBM on the same 21 behavioural metrics as the "
                "rules engine, recomputed **point-in-time** at each past month-end and "
                "labelled with what happened next.\n- **Validation:** trained on earlier "
                "months, tested on the most recent months it never saw (out-of-time).\n"
                "- **Drivers:** SHAP values: the features pushing each customer's "
                "probability up.\n- The forecast is **advisory**: it never changes the "
                "rule-based score or tier.")
            vt = pd.DataFrame({h: fc[h]["validation"] for h in fc}).T
            vt.index = ["Next month", "Next quarter"]
            st.dataframe(vt.rename(columns={
                "auc": "AUC", "pr_auc": "PR-AUC", "top_decile_capture":
                "Top-10% capture (%)", "base_rate": "Event rate (%)",
                "predicted_events": "Predicted events (test)",
                "actual_events": "Actual events (test)"}), use_container_width=True)
            st.caption("AUC: probability a customer who went 30+ DPD was ranked above one who "
                       "didn't (0.5 = random). Top-10% capture: share of actual events found "
                       "in the 10% highest-scored customers. In back-testing the model "
                       "under-predicted the total count (recent months saw more new "
                       "delinquencies than the training period); use rankings first and "
                       "treat totals as a floor.")

elif page == "Upload & Validate":
    st.header("Upload & Validate")
    st.write("Load the 8 portfolio tables as CSV, Excel (one sheet per table) or JSON. "
             "Rows with errors are rejected; warnings are normalised and kept.")
    c1, c2 = st.columns([1, 1])
    with c1:
        names = list(SOURCES)
        choice = st.selectbox("Bundled dataset", names,
                              index=names.index(ss.source) if ss.source in names else 0)
        if st.button("Load bundled dataset", type="primary"):
            ss.tables, ss.issues, ss.summary = load_folder(str(SOURCES[choice][0]))
            ss.source = ss.data_key = choice
            ss.truth = SOURCES[choice][1]
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
            ss.truth = None
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
    k[4].metric("Outstanding at Amber/Red", inr(view[at_risk].outstanding.sum(), 1))

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

    st.subheader("Pre-delinquent vs delinquent")
    st.caption("Pre-delinquent customers are still paying on time but already show warning "
               "signals — the population the EWS exists to catch.")
    pre_del = view[(view.current_dpd == 0) & view.tier.isin(["Watch", "Amber", "Red"])]
    already_del = view[view.current_dpd > 0]
    pc1, pc2 = st.columns(2)
    pc1.metric("Pre-delinquent (DPD 0, Watch+)", f"{len(pre_del)}",
               f"{inr(pre_del.outstanding.sum(), 1)} exposure", delta_color="off")
    pc2.metric("Already delinquent (DPD > 0)", f"{len(already_del)}",
               f"{inr(already_del.outstanding.sum(), 1)} exposure", delta_color="off")

    st.subheader("Biggest score movers vs 3 months ago")
    movers = view.assign(score_delta=view.risk_score - view.prev_score)
    movers = movers[movers.score_delta.abs() > 0]
    mc1, mc2 = st.columns(2)
    with mc1:
        st.caption("Top 10 deteriorating")
        worse = movers.nlargest(10, "score_delta").reset_index()
        if len(worse):
            st.altair_chart(alt.Chart(worse).mark_bar(color="#c62828").encode(
                x=alt.X("score_delta:Q", title="Score change"),
                y=alt.Y("customer_name:N", sort="-x", title=None),
                tooltip=["customer_id", "customer_name", "risk_score", "score_delta", "tier"]),
                use_container_width=True)
    with mc2:
        st.caption("Top 10 improving")
        better = movers.nsmallest(10, "score_delta").reset_index()
        if len(better):
            st.altair_chart(alt.Chart(better).mark_bar(color="#2e7d32").encode(
                x=alt.X("score_delta:Q", title="Score change"),
                y=alt.Y("customer_name:N", sort="x", title=None),
                tooltip=["customer_id", "customer_name", "risk_score", "score_delta", "tier"]),
                use_container_width=True)

    st.subheader("Intervention workload")
    inv_sum = store.intervention_summary()
    ic = st.columns(4)
    ic[0].metric("Open", int(inv_sum["by_status"].get("OPEN", 0)))
    ic[1].metric("In progress", int(inv_sum["by_status"].get("IN_PROGRESS", 0)))
    ic[2].metric("Overdue", int(inv_sum["overdue"]))
    ic[3].metric("Completed", int(inv_sum["by_status"].get("COMPLETED", 0)))
    if not inv_sum["by_owner"].empty:
        st.caption("By owner")
        st.dataframe(inv_sum["by_owner"], hide_index=True, use_container_width=True)
    else:
        st.caption("No interventions recorded yet — create them from the Investigation page.")

    st.subheader("Priority matrix")
    st.caption("X = probability of deterioration · Y = outstanding · colour = tier · "
               "size = composite priority score. Top-right needs attention first.")
    pm = view[view.tier != "Green"].copy()
    pm["prob"] = pm.p_3m if pm.p_3m.notna().any() else pm.roll_prob
    if len(pm):
        st.altair_chart(alt.Chart(pm.reset_index()).mark_circle(opacity=0.7).encode(
            x=alt.X("prob:Q", title="P(deterioration)", scale=alt.Scale(domain=[0, 1])),
            y=alt.Y("outstanding:Q", title="Outstanding (₹)"),
            color=alt.Color("tier:N", scale=alt.Scale(domain=TIERS,
                                                       range=list(TIER_COLORS.values()))),
            size=alt.Size("priority_score:Q", scale=alt.Scale(range=[30, 500]),
                          title="Priority"),
            tooltip=["customer_id", "customer_name", "tier", "risk_score", "priority_score",
                     "prob", "outstanding", "primary_recommendation"]).interactive(),
            use_container_width=True)

    corr_sum = correlation.correlation_summary(view)
    if corr_sum.customer_count.sum() > 0:
        st.subheader("Multi-signal correlation patterns")
        st.caption("Customers whose stress spans several modules at once — these combinations "
                   "mean more than the individual signals do separately.")
        st.dataframe(corr_sum.reset_index().rename(columns={"index": "pattern_id"}),
                     hide_index=True, use_container_width=True)

    truth = ss.get("truth")
    if truth is not None and truth.exists():
        st.subheader("Accuracy vs planted ground truth")
        ev = insights.evaluate(view, pd.read_csv(truth))
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
    emir = g[1].multiselect("Region", sorted(view.region.dropna().unique()))
    traj = g[2].multiselect("Trajectory", sorted(view.trajectory.unique()))
    conf = g[3].multiselect("Confidence", ["High", "Medium", "Low"])
    sma_f = g[4].multiselect("SMA category", rbi.SMA_CATEGORIES)
    w = view
    for col, sel in [("tier", tiers), ("customer_segment", seg), ("portfolio", port),
                     ("dpd_bucket", dpdb), ("region", emir), ("trajectory", traj),
                     ("confidence", conf), ("sma_category", sma_f)]:
        if sel:
            w = w[w[col].isin(sel)]
    if prod:
        w = w[w.products.fillna("").apply(lambda s: any(p in s for p in prod))]
    w = w.sort_values(["priority_score", "tier_rank", "risk_score"], ascending=False)
    st.caption(f"{len(w)} customers · sorted by composite priority, then tier and score")
    cols = ["customer_id", "customer_name", "products", "outstanding",
            "risk_score", "tier", "priority_score", "sma_category", "confidence",
            "trajectory", "top_indicators", "primary_recommendation",
            "roll_prob", "recovery_profile", "dpd_bucket",
            "monitoring_status", "review_status", "customer_segment", "portfolio"]
    if "ml_prob" in w.columns and w.ml_prob.notna().any():
        cols.insert(cols.index("roll_prob") + 1, "ml_prob")
    if w.p_3m.notna().any():
        cols.insert(cols.index("risk_score") + 1, "p_3m")
    table = w.reset_index()[cols]
    ev = st.dataframe(
        table, hide_index=True, use_container_width=True, height=520, on_select="rerun",
        selection_mode="single-row",
        column_config={
            "outstanding": st.column_config.NumberColumn("Outstanding (₹)", format="%,.0f"),
            "risk_score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100,
                                                          format="%d"),
            "top_indicators": st.column_config.TextColumn("Contributing indicators",
                                                          width="large"),
            "roll_prob": st.column_config.NumberColumn("Roll Prob (30d)", format="%.0%%"),
            "ml_prob": st.column_config.NumberColumn("ML P(det.)", format="%.0%%"),
            "p_3m": st.column_config.ProgressColumn("P(30+ DPD) 3m", min_value=0, max_value=1,
                                                    format="percent"),
            "priority_score": st.column_config.ProgressColumn("Priority", min_value=0,
                                                              max_value=100, format="%d"),
            "primary_recommendation": st.column_config.TextColumn("Recommended intervention",
                                                                  width="medium")})
    rows = ev.selection.rows if ev and ev.selection else []
    if rows:
        cid = table.iloc[rows[0]].customer_id
        b1, b2 = st.columns(2)
        def _open(c=cid):
            ss.customer = c
            ss.page = "Customer"
        def _investigate(c=cid):
            ss.customer = c
            ss.page = "Investigation"
        b1.button(f"Open {cid} →", type="primary", on_click=_open)
        b2.button(f"Investigate {cid} →", on_click=_investigate)
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
               f"{r.customer_segment} · {r.region} · {r.products}")
    k = st.columns(7)
    k[0].metric("Risk score", f"{r.risk_score:.0f}", f"{r.risk_score - r.prev_score:+.0f} vs 3m",
                delta_color="inverse")
    k[1].metric("Confidence", r.confidence, f"DCS {r.dcs:.0f}", delta_color="off")
    k[2].metric("Trajectory", r.trajectory)
    k[3].metric("Current DPD", f"{r.current_dpd:.0f}")
    k[4].metric("Roll-forward prob.", f"{100 * r.roll_prob:.0f}%")
    k[5].metric("Outstanding", inr_exact(r.outstanding))
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

    # Delinquency forecast (advisory; never changes the rule-based score or tier)
    if pd.notna(r.get("p_3m")):
        st.subheader("🔮 Delinquency forecast")
        f1, f2, f3 = st.columns([1, 1, 3])
        f1.metric("P(30+ DPD) next month", f"{100 * r.p_1m:.0f}%")
        f2.metric("P(30+ DPD) next quarter", f"{100 * r.p_3m:.0f}%")
        f3.markdown(f"**Why:** {r.forecast_drivers or 'no strong drivers'}")
        f3.caption("LightGBM trained point-in-time on past month-ends; drivers are the "
                   "features pushing this customer's probability up (SHAP). See the model "
                   "card on Home.")
        if r.p_3m >= FORECAST_FLAG and r.tier in ("Green", "Watch"):
            st.warning("Silent stress: the rules rate this customer "
                       f"{r.tier}, but the forecast puts a {100 * r.p_3m:.0f}% chance on "
                       "30+ DPD within 3 months. Consider early contact.")
    elif r.current_dpd >= 30:
        st.caption("🔮 Forecast not shown: customer is already 30+ DPD (see roll-forward "
                   "probability).")

    # ML second opinion
    if ML_MODEL is not None and pd.notna(r.get("ml_prob")):
        st.subheader("ML second opinion")
        mc1, mc2 = st.columns([1, 2])
        mc1.metric("P(deterioration 90d)", f"{100 * r.ml_prob:.0f}%")
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
    h2.caption("Income / inflows / average balance (₹)")
    h2.altair_chart(alt.Chart(long.dropna()).mark_line(point=True).encode(
        x="month_end_date:T", y="value:Q", color="variable:N"), use_container_width=True)
    h3, h4 = st.columns(2)
    ex = t["exposure"][t["exposure"].customer_id == cid]
    h3.caption("Revolving utilisation % and bureau score")
    h3.altair_chart(alt.Chart(ex.melt(id_vars="snapshot_date", value_vars=[
        "revolving_utilisation_pct"]).dropna()).mark_line(point=True).encode(
        x="snapshot_date:T", y="value:Q"), use_container_width=True)
    h4.caption("Obligations: on-us vs other lenders (₹ / month)")
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
    al = al[al.customer_id.isin(view.index)]  # alerts for the loaded dataset only
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
# 7. Investigation workspace
# ======================================================================================
elif page == "Investigation":
    st.header("Investigation workspace")
    st.caption("Everything needed to act on one early warning: how the risk built up, which "
               "signals reinforce each other, what to do, and a record of what was done.")
    ids = view.sort_values("priority_score", ascending=False).index.tolist()
    default = ids.index(ss.get("customer", ids[0])) if ss.get("customer") in ids else 0
    cid = st.selectbox(
        "Customer", ids, index=default, key="inv_customer",
        format_func=lambda c: f"#{view.at[c, 'priority_rank']:.0f} · {c} · "
                              f"{view.at[c, 'customer_name']} · {view.at[c, 'tier']}")
    ss.customer = cid
    r = view.loc[cid]
    evd = r.evidence

    st.markdown(f"### {r.customer_name} &nbsp; {tier_badge(r.tier)}", unsafe_allow_html=True)
    st.caption(f"{cid} · {r.customer_type} · {r.employment_or_business_category} · "
               f"{r.customer_segment} · {r.region} · {r.products}")

    k = st.columns(6)
    k[0].metric("Priority", f"{r.priority_score:.0f}", f"rank #{r.priority_rank:.0f}",
                delta_color="off")
    k[1].metric("Risk score", f"{r.risk_score:.0f}",
                f"{r.risk_score - r.prev_score:+.0f} vs 3m", delta_color="inverse")
    k[2].metric("SMA category", r.sma_category)
    k[3].metric("Trajectory", r.trajectory)
    k[4].metric("Outstanding", inr_exact(r.outstanding))
    k[5].metric("Modules stressed", f"{int(r.n_modules_stressed)} / 4")
    if r.trigger:
        st.error(f"Hard trigger: {r.trigger}")
    if r.insufficient_evidence:
        st.info("Insufficient evidence: limited history or data. Missing information is not "
                "treated as risk; tier is capped at Watch unless hard evidence appears.")

    # --- Why this is correlated, and what to do about it ---
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Correlated deterioration")
        if r.correlation_patterns:
            corr_row = correlation.detect_correlations(view.loc[[cid]]).iloc[0]
            for name in r.correlation_patterns:
                st.warning(f"**{name}**")
            st.caption(corr_row.narrative)
        else:
            st.success("No multi-module correlation pattern detected.")
        overlap = correlation.overlap_warning(evd)
        if overlap:
            st.caption(f"Double-counting advisory: {overlap}. These signals describe the same "
                       f"underlying problem, so treat the combined score with care.")
        st.subheader("Module indices")
        idx = pd.DataFrame([dict(module=mk, index=mv) for mk, mv in evd["indices"].items()
                            if mv is not None])
        if len(idx):
            st.altair_chart(alt.Chart(idx).mark_bar().encode(
                x=alt.X("index:Q", scale=alt.Scale(domain=[0, 100]), title="Stress %"),
                y=alt.Y("module:N", sort="-x", title=None),
                color=alt.condition("datum.index > 50", alt.value("#c62828"),
                                    alt.value("#f9a825"))), use_container_width=True)
    with c2:
        st.subheader("Recommended interventions")
        recs = r.recommendations
        if not recs:
            st.success("No specific intervention scenario matched. Continue monitoring.")
        for rec in recs:
            mark = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}[rec["urgency"]]
            with st.expander(f"{mark} {rec['name']} · {rec['urgency']}",
                             expanded=rec["urgency"] in ("CRITICAL", "HIGH")):
                for action in rec["actions"]:
                    st.markdown(f"- {action}")
                st.caption(f"Triggered by: {', '.join(rec['supporting_indicators'])}")

    # --- How the risk built up ---
    st.subheader("How the risk built up")
    if st.checkbox("Score the last 12 months (re-scores the portfolio at 5 dates, ~10 s)",
                   key=f"hist_{cid}"):
        hist = get_history(ss.data_key, rules_key(ss.rules), str(AS_OF.date()), ss.tables,
                           ss.rules)
        deltas = trends.score_deltas(hist, cid)
        if len(deltas):
            d = deltas.reset_index()
            hc1, hc2 = st.columns(2)
            hc1.caption("Risk score over time")
            hc1.altair_chart(alt.Chart(d).mark_line(point=True, color="#c62828").encode(
                x=alt.X("period_date:T", title=None),
                y=alt.Y("risk_score:Q", scale=alt.Scale(domain=[0, 100]))),
                use_container_width=True)
            mod_cols = [m for m in scoring.MODULES.values() if m in d.columns]
            if mod_cols:
                hc2.caption("Module indices over time")
                long = d.melt(id_vars="period_date", value_vars=mod_cols,
                              var_name="module", value_name="index")
                hc2.altair_chart(alt.Chart(long.dropna()).mark_line(point=True).encode(
                    x=alt.X("period_date:T", title=None),
                    y=alt.Y("index:Q", scale=alt.Scale(domain=[0, 100])),
                    color="module:N"), use_container_width=True)
            st.caption(f"Multi-period trajectory: "
                       f"**{trends.extended_trajectory(hist).get(cid, 'n/a')}**")
            st.dataframe(d, hide_index=True, use_container_width=True)

    timeline = trends.indicator_timeline(M.loc[cid]) if cid in M.index else pd.DataFrame()
    if len(timeline):
        st.caption("Underlying indicators, last 12 months (0 = oldest)")
        tl = timeline.assign(month=range(len(timeline))).melt(
            id_vars="month", var_name="indicator", value_name="value")
        st.altair_chart(alt.Chart(tl.dropna()).mark_line(point=True).encode(
            x=alt.X("month:O", title=None), y=alt.Y("value:Q", title=None),
            color="indicator:N").properties(height=160).facet(
            facet=alt.Facet("indicator:N", title=None), columns=4).resolve_scale(y="independent"),
            use_container_width=True)

    # --- Case file ---
    st.subheader("Case file")
    tabs = st.tabs(["Interventions", "Notes", "New intervention", "New note", "Evidence"])

    with tabs[0]:
        mine = store.get_interventions(customer_id=cid)
        if mine.empty:
            st.info("No interventions recorded for this customer yet.")
        else:
            st.dataframe(mine[["intervention_id", "warning_category", "status", "priority",
                               "assigned_owner", "target_date", "notes", "outcome"]],
                         hide_index=True, use_container_width=True)
            pick_int = st.selectbox("Update an intervention", mine.intervention_id.tolist())
            with st.form("update_int", clear_on_submit=True):
                u1, u2 = st.columns(2)
                new_status = u1.selectbox("Status", store.INTERVENTION_STATUSES)
                changed_by = u2.text_input("Changed by", value=ss.get("analyst", ""))
                new_outcome = st.text_input("Outcome (optional)")
                if st.form_submit_button("Record update", type="primary"):
                    if not changed_by.strip():
                        st.error("Changed by is required — updates are attributed.")
                    else:
                        fields = {"status": new_status}
                        if new_outcome.strip():
                            fields["outcome"] = new_outcome.strip()
                        store.update_intervention(pick_int, changed_by.strip(), **fields)
                        ss.analyst = changed_by.strip()
                        st.rerun()
            hist_df = store.intervention_history(pick_int)
            if not hist_df.empty:
                st.caption("Change history (append-only)")
                st.dataframe(hist_df[["field_changed", "old_value", "new_value", "changed_by",
                                      "changed_at"]], hide_index=True, use_container_width=True)

    with tabs[1]:
        notes = store.investigation_notes(cid)
        if notes.empty:
            st.info("No investigation notes yet.")
        for _, n in notes.iterrows():
            st.markdown(f"**{n.note_type}** · {n.analyst_id} · {n.created_at:%Y-%m-%d %H:%M}")
            st.write(n.content)
            st.divider()

    with tabs[2]:
        primary = interv.primary_recommendation(r.recommendations)
        with st.form("create_int", clear_on_submit=True):
            f1, f2 = st.columns(2)
            cat = f1.text_input("Warning category", value=primary.get("name", ""))
            owner = f2.text_input("Assign to", value=ss.get("analyst", ""))
            f3, f4 = st.columns(2)
            pri_level = f3.selectbox("Priority", ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
                                     index=["CRITICAL", "HIGH", "MEDIUM", "LOW"].index(
                                         primary.get("urgency", "MEDIUM")))
            target = f4.date_input("Target date",
                                   value=(AS_OF + pd.Timedelta(days=30)).date())
            note_text = st.text_area("Notes", placeholder="What is being done and why")
            if st.form_submit_button("Create intervention", type="primary"):
                if not owner.strip():
                    st.error("An owner is required — interventions must be accountable.")
                else:
                    iid = store.create_intervention(
                        cid, None, None, f"{AS_OF.date()}-{rules_key(ss.rules)}",
                        cat, [s["id"] for s in r.recommendations],
                        primary.get("name", ""), owner.strip(), pri_level, target, note_text)
                    ss.analyst = owner.strip()
                    st.success(f"Created {iid}, owned by {owner.strip()}, due {target}.")

    with tabs[3]:
        with st.form("add_note", clear_on_submit=True):
            n1, n2 = st.columns(2)
            note_type = n1.selectbox("Type", store.NOTE_TYPES)
            analyst = n2.text_input("Analyst", value=ss.get("analyst", ""))
            content = st.text_area("Note")
            st.caption("Use RECLASSIFICATION to record that a signal was a data or processing "
                       "issue. Published alerts stay frozen; the note is the audit trail.")
            if st.form_submit_button("Add note", type="primary"):
                if not content.strip() or not analyst.strip():
                    st.error("Note text and analyst are both required.")
                else:
                    store.add_investigation_note(cid, None, None, note_type, content, analyst)
                    ss.analyst = analyst.strip()
                    st.rerun()

    with tabs[4]:
        for reason in r.reasons:
            st.markdown(f"- {reason}")
        sig = pd.DataFrame(evd["signals"])
        sig["status"] = sig.strength.map(
            lambda s: "not evaluated (no data)" if pd.isna(s) else ("fired" if s > 0 else "ok"))
        st.dataframe(sig[["id", "name", "category", "value", "operator", "threshold",
                          "strength", "points", "status"]], hide_index=True,
                     use_container_width=True)

# ======================================================================================
# 9. RBI reporting
# ======================================================================================
elif page == "RBI Reporting":
    st.header("RBI credit risk EWS reporting")
    cfg = rbi.config(ss.rules)
    st.caption(f"SMA classification by days past due · amounts in INR · "
               f"alert examination TAT {cfg['alert_examination_tat_days']} days · "
               f"escalation {cfg['escalation_deadline_days']} days · "
               f"resolution {cfg['resolution_deadline_days']} days. "
               f"Thresholds are configuration, editable in the rules file.")
    report = get_report(ss.data_key, rules_key(ss.rules), str(AS_OF.date()), ss.tables, view,
                        ss.rules)
    comp = report["compliance"]

    k = st.columns(5)
    k[0].metric("Customers", comp["total_customers"])
    k[1].metric("At risk (Amber/Red)", comp["at_risk_count"],
                inr(comp['at_risk_exposure'], 1), delta_color="off")
    k[2].metric("Pre-delinquent", comp["pre_delinquent_count"],
                inr(comp['pre_delinquent_exposure'], 1), delta_color="off")
    k[3].metric("Already delinquent", comp["already_delinquent_count"],
                inr(comp['already_delinquent_exposure'], 1), delta_color="off")
    k[4].metric(f"At risk over {inr(comp['exposure_threshold'], 1)}",
                comp["high_exposure_at_risk"],
                inr(comp['high_exposure_amount'], 1), delta_color="off")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("SMA distribution")
        dist = report["distribution"]
        st.dataframe(dist, hide_index=True, use_container_width=True, column_config={
            "sma_category": "SMA category",
            "facility_count": "Facilities",
            "customer_count": "Customers",
            "outstanding_amount": st.column_config.NumberColumn("Outstanding (₹)",
                                                                format="%,.0f"),
            "pct_of_portfolio": st.column_config.NumberColumn("% of book", format="%.1f%%")})
        st.altair_chart(alt.Chart(dist).mark_bar().encode(
            x=alt.X("sma_category:N", sort=rbi.SMA_CATEGORIES, title=None),
            y=alt.Y("facility_count:Q", title="Facilities"),
            color=alt.Color("sma_category:N", sort=rbi.SMA_CATEGORIES, legend=None,
                            scale=alt.Scale(domain=rbi.SMA_CATEGORIES, range=SMA_COLORS)),
            tooltip=["sma_category", "facility_count", "customer_count",
                     "outstanding_amount"]), use_container_width=True)
    with c2:
        st.subheader("SMA mix by portfolio")
        by_port = report["distribution_by_portfolio"]
        if len(by_port):
            st.altair_chart(alt.Chart(by_port).mark_bar().encode(
                x=alt.X("outstanding:Q", title="Outstanding (₹)", stack="normalize"),
                y=alt.Y("portfolio:N", title=None),
                color=alt.Color("sma_category:N", sort=rbi.SMA_CATEGORIES,
                                scale=alt.Scale(domain=rbi.SMA_CATEGORIES, range=SMA_COLORS)),
                order=alt.Order("sma_category:N"),
                tooltip=["portfolio", "sma_category", "facility_count", "outstanding"]),
                use_container_width=True)

    st.subheader("SMA migration matrix")
    st.caption("Share of facilities moving from each SMA category (rows) to the next "
               "month's category (columns). The upper-right triangle is deterioration.")
    mig = (report["migration_matrix"] * 100).round(1)
    mm = mig.reset_index().melt(id_vars="sma", var_name="to", value_name="pct").rename(
        columns={"sma": "from"})
    st.altair_chart(alt.Chart(mm).mark_rect().encode(
        x=alt.X("to:N", sort=rbi.SMA_CATEGORIES, title="Next month"),
        y=alt.Y("from:N", sort=rbi.SMA_CATEGORIES, title="This month"),
        color=alt.Color("pct:Q", scale=alt.Scale(scheme="oranges"), title="%")) +
        alt.Chart(mm).mark_text(fontSize=11).encode(
            x=alt.X("to:N", sort=rbi.SMA_CATEGORIES),
            y=alt.Y("from:N", sort=rbi.SMA_CATEGORIES),
            text=alt.Text("pct:Q", format=".1f")), use_container_width=True)

    st.subheader("SMA trend")
    tr = report["trend"]
    if len(tr):
        st.altair_chart(alt.Chart(tr).mark_area().encode(
            x=alt.X("month:T", title=None),
            y=alt.Y("count:Q", title="Facilities", stack=True),
            color=alt.Color("sma_category:N", sort=rbi.SMA_CATEGORIES,
                            scale=alt.Scale(domain=rbi.SMA_CATEGORIES, range=SMA_COLORS)),
            order=alt.Order("sma_category:N"),
            tooltip=["month:T", "sma_category", "count", "outstanding"]),
            use_container_width=True)

    npa = view[view.sma_category.isin(["SMA-2", "NPA"])].sort_values("outstanding",
                                                                     ascending=False)
    if len(npa):
        st.subheader(f"SMA-2 and NPA accounts ({len(npa)})")
        st.dataframe(npa.reset_index()[["customer_id", "customer_name", "sma_category",
                                        "current_dpd", "outstanding", "risk_score", "tier",
                                        "trajectory", "primary_recommendation"]],
                     hide_index=True, use_container_width=True, column_config={
                         "outstanding": st.column_config.NumberColumn("Outstanding (₹)",
                                                                      format="%,.0f")})

    st.divider()
    d1, d2 = st.columns(2)
    d1.download_button("Download full report (Excel)", rbi.report_to_excel(report),
                       f"rbi_ews_report_{AS_OF.date()}.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       type="primary")
    d2.download_button("Download SMA distribution (CSV)",
                       report["distribution"].to_csv(index=False),
                       f"sma_distribution_{AS_OF.date()}.csv", "text/csv")

# ======================================================================================
# 8. AI Assistant
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
        facts["outstanding"] = inr_exact(r.outstanding)
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
