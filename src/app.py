"""Sentinel: Early Warning System prototype.

    uv run streamlit run src/app.py
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import altair as alt
import pandas as pd
import streamlit as st

from src import forecast, ingest, insights, llm, metrics, ml, scoring, store

# Bundled portfolios: label -> (folder of CSVs, ground-truth labels or None).
SOURCES = {
    "Synthetic Gulf Bank · 600 customers (clean)":
        (ROOT / "data/sample/csv", ROOT / "data/_ground_truth/customer_labels.csv"),
    "Synthetic Gulf Bank · dirty (validation test)":
        (ROOT / "data/dirty/csv", None),
    "Northern Emirates Commercial Bank · 1,200 customers, SME-heavy (clean)":
        (ROOT / "data/northern_sme/sample/csv",
         ROOT / "data/northern_sme/_ground_truth/customer_labels.csv"),
    "Northern Emirates Commercial Bank · dirty (validation test)":
        (ROOT / "data/northern_sme/dirty/csv", None),
}
SOURCES = {k: v for k, v in SOURCES.items() if v[0].exists()}
RULES_PATH = ROOT / "data/sample/rules/risk_signal_definitions.json"
TIER_COLORS = {"Green": "#2e7d32", "Watch": "#f9a825", "Amber": "#ef6c00", "Red": "#c62828"}
TIERS = list(TIER_COLORS)
PAGES = ["Upload", "Summary", "Portfolio", "Watchlist", "Customer", "Review", "Rules",
         "What-If Simulator", "AI Assistant"]

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
    """Train and validate the forecast models against the selected portfolio history."""
    p = json.loads(params_json)
    return forecast.train(forecast.build_panel(_t, p, p["as_of_date"]))


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
    try:
        pred = get_predictions(data_key, pj, t, m_now)
    except Exception as exc:
        ss.forecast_error = f"{type(exc).__name__}: {exc}"
        pred = None
    else:
        ss.forecast_error = None
    for col in ("p_1m", "p_3m", "forecast_drivers"):
        v[col] = pred[col].reindex(v.index) if pred is not None else None
    return v, m_now, prev_risk, as_of, ml_model, ml_features


def ensure_data():
    if "rules" not in ss:
        ss.rules = scoring.load_rules(RULES_PATH)


def accept_upload(files):
    raw = {}
    digest = hashlib.sha256()
    for uploaded_file in sorted(files, key=lambda item: item.name):
        payload = uploaded_file.getvalue()
        raw.update(ingest.read_any(uploaded_file.name, payload))
        digest.update(uploaded_file.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)

    required_tables = [
        table for table in ingest.TABLES
        if table != "exception_evidence" and table not in raw
    ]
    if required_tables:
        ss.upload_error = (
            "Upload all required portfolio tables before continuing. Missing or "
            f"unrecognized files for: {', '.join(required_tables)}."
        )
        return

    tables, issues, summary = ingest.validate(raw)
    empty_core_tables = [
        table for table in ("customers", "facilities", "repayments", "delinquency",
                            "exposure", "financials")
        if table not in tables or tables[table].empty
    ]
    if empty_core_tables:
        ss.upload_error = (
            "The uploaded dataset has no accepted rows for: "
            f"{', '.join(empty_core_tables)}. Fix the validation errors and upload again."
        )
        ss.upload_issues = issues
        return

    ss.pop("upload_error", None)
    ss.pop("upload_issues", None)
    ss.tables = tables
    ss.issues = issues
    ss.summary = summary
    ss.source = f"Uploaded dataset ({len(files)} files)"
    ss.truth = None
    ss.data_key = f"upload-{digest.hexdigest()}"
    ss.dataset_uploaded = True
    latest_snapshot = pd.to_datetime(
        tables["delinquency"]["snapshot_date"], errors="coerce"
    ).max()
    if pd.notna(latest_snapshot):
        ss.rules = copy.deepcopy(ss.rules)
        ss.rules["parameters"]["as_of_date"] = latest_snapshot.date().isoformat()
    ss.page = "Summary"
    ss.pop("forecast_error", None)


def render_upload_form():
    st.header("Upload")
    st.write(
        "Upload customers, facilities, repayments, delinquency, exposure, financials, "
        "and bank holidays as CSV, Excel (one sheet per table), or JSON. Exception "
        "evidence is optional. Predictions are trained from and displayed for this dataset."
    )
    files = st.file_uploader(
        "Upload files",
        type=["csv", "xlsx", "json"],
        accept_multiple_files=True,
        key="portfolio_upload",
    )
    if ss.get("upload_error"):
        st.error(ss.upload_error)
        if "upload_issues" in ss:
            st.dataframe(ss.upload_issues, hide_index=True, use_container_width=True)
    if files:
        st.button(
            "Validate dataset and continue",
            type="primary",
            on_click=accept_upload,
            args=(files,),
        )


ensure_data()
if not ss.get("dataset_uploaded", False):
    ss.page = "Upload"

# ======================================================================================
# Sidebar
# ======================================================================================
with st.sidebar:
    st.title("🛡️ Sentinel EWS")
    st.caption("Early Warning System · prototype")
    page = st.radio("Navigate", PAGES, key="page")
    st.divider()
    st.markdown(
        f"**Data:** {ss.source if ss.get('dataset_uploaded', False) else 'Upload required'}"
    )
    st.markdown(f"**As-of date:** {ss.rules['parameters']['as_of_date']}")
    st.markdown(f"**Rules version:** `{rules_key(ss.rules)}`")
    models = llm.available_models()
    st.markdown(f"**Local LLM:** {'🟢 ' + llm.pick_model(models) if models else '⚪ off (template text)'}")
    st.caption("Score 0–100 · higher = riskier")

if not ss.get("dataset_uploaded", False):
    st.title("Upload a dataset to begin")
    st.info("The dashboard and model predictions are enabled after the uploaded data validates.")
    render_upload_form()
    st.stop()

view, M, PREV_RISK, AS_OF, ML_MODEL, ML_FEATURES = build_view(
    ss.tables, ss.rules, ss.data_key
)


def tier_badge(t):
    return f"<span style='background:{TIER_COLORS[t]};color:white;padding:2px 10px;" \
           f"border-radius:10px;font-weight:600'>{t}</span>"


# ======================================================================================
# 1. Upload
# ======================================================================================
FORECAST_FLAG = 0.20  # P(30+ DPD next quarter) at which a performing customer is red-flagged


def open_customer(cid):
    ss.customer = cid
    ss.page = "Customer"


def flag_table(df, cols, key, config=None):
    """Selectable table with an Open-customer button (used by Summary red-flag tabs)."""
    ev = st.dataframe(df.reset_index()[cols], hide_index=True, use_container_width=True,
                      on_select="rerun", selection_mode="single-row", key=key,
                      column_config=config or {}, height=min(38 * (len(df) + 1) + 3, 360))
    rows = ev.selection.rows if ev and ev.selection else []
    if rows:
        cid = df.index[rows[0]]
        st.button(f"Open {cid} →", type="primary", key=f"{key}_open",
                  on_click=open_customer, args=(cid,))


if page == "Summary":
    st.header("Early warning briefing")
    st.caption(f"As of {AS_OF.date()} · {ss.source} · {len(view)} customers monitored")
    has_fc = view.p_3m.notna().any()
    if ss.get("forecast_error"):
        st.error(
            "The LightGBM forecast could not be trained or run for this uploaded dataset: "
            f"{ss.forecast_error}"
        )
    elif has_fc:
        st.caption(
            "LightGBM was trained on the uploaded portfolio's historical snapshots and "
            "scored eligible customers from that same dataset at the as-of date."
        )
    else:
        st.warning(
            "No LightGBM predictions are available for this upload. Check that the "
            "dataset has enough monthly history and includes customers currently below "
            "30 DPD to score."
        )
    alerts_df = store.alerts()
    alerts_df = alerts_df[alerts_df.customer_id.isin(view.index)]  # this dataset only
    open_alerts = int(alerts_df.latest_decision.isna().sum()) if len(alerts_df) else 0
    red = view.tier == "Red"
    amber = view.tier == "Amber"
    exp_1m = float(view.p_1m.sum()) if has_fc else None
    exp_3m = float(view.p_3m.sum()) if has_fc else None
    ear_3m = float((view.outstanding * view.p_3m.fillna(0)).sum()) if has_fc else None

    k = st.columns(3)
    k[0].metric("Red today", int(red.sum()), f"AED {view[red].outstanding.sum() / 1e6:,.1f}M",
                delta_color="off")
    k[1].metric("Amber today", int(amber.sum()),
                f"AED {view[amber].outstanding.sum() / 1e6:,.1f}M", delta_color="off")
    k[2].metric("Open alerts", open_alerts, help="Published alerts without an analyst decision")
    k = st.columns(3)
    k[0].metric("New 30+ DPD · 1m", f"{exp_1m:.1f}" if has_fc else "n/a",
                help="Expected customers newly reaching 30+ DPD next month: sum of forecast "
                     "probabilities for customers currently below 30 DPD")
    k[1].metric("New 30+ DPD · 3m", f"{exp_3m:.1f}" if has_fc else "n/a",
                help="Expected customers newly reaching 30+ DPD within the next quarter")
    k[2].metric("At-risk AED · 3m", f"{ear_3m / 1e6:,.1f}M" if has_fc else "n/a",
                help="Expected exposure at risk next quarter: Σ outstanding × P(30+ DPD "
                     "within 3 months), AED")

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
                  expected_exposure_at_risk_next_quarter_m=round((ear_3m or 0) / 1e6, 1),
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
            "outstanding": st.column_config.NumberColumn("Outstanding (AED)", format="%,.0f"),
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

elif page == "Upload":
    render_upload_form()

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
    cols = ["customer_id", "customer_name", "products", "outstanding",
            "risk_score", "tier", "confidence", "trajectory", "top_indicators",
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
            "outstanding": st.column_config.NumberColumn("Outstanding (AED)", format="%,.0f"),
            "risk_score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100,
                                                          format="%d"),
            "top_indicators": st.column_config.TextColumn("Contributing indicators",
                                                          width="large"),
            "roll_prob": st.column_config.NumberColumn("Roll Prob (30d)", format="%.0%%"),
            "ml_prob": st.column_config.NumberColumn("ML P(det.)", format="%.0%%"),
            "p_3m": st.column_config.ProgressColumn("P(30+ DPD) 3m", min_value=0, max_value=1,
                                                    format="percent")})
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

    # Delinquency forecast (advisory; never changes the rule-based score or tier)
    if pd.notna(r.get("p_3m")):
        st.subheader("🔮 Delinquency forecast")
        f1, f2, f3 = st.columns([1, 1, 3])
        f1.metric("P(30+ DPD) next month", f"{100 * r.p_1m:.0f}%")
        f2.metric("P(30+ DPD) next quarter", f"{100 * r.p_3m:.0f}%")
        f3.markdown(f"**Why:** {r.forecast_drivers or 'no strong drivers'}")
        f3.caption("LightGBM trained point-in-time on past month-ends; drivers are the "
                   "features pushing this customer's probability up (SHAP). See the model "
                   "card on Summary.")
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
# 7. What-If Simulator
# ======================================================================================
elif page == "What-If Simulator":
    st.header("What-If Simulator")
    st.write("Adjust a customer's key metrics and see how the risk score and tier change "
             "in real time. Only Stage 2 (scoring) re-runs — metrics are not recomputed.")
    ids = view.sort_values("risk_score", ascending=False).index.tolist()
    default = ids.index(ss.get("customer", ids[0])) if ss.get("customer") in ids else 0
    cid = st.selectbox("Customer", ids, index=default, key="sim_customer",
                       format_func=lambda c: f"{c} · {view.at[c, 'customer_name']} · "
                                             f"{view.at[c, 'tier']} {view.at[c, 'risk_score']:.0f}")
    original = M.loc[cid].copy()

    # Metrics that are NaN ("not evaluated", e.g. salary for SMEs) or outside a slider's range
    # must not crash the slider, and an untouched slider must leave the metric exactly as-is.
    def slider(label, col, lo, hi, key):
        v = original.get(col)
        start = int(min(max(v, lo), hi)) if pd.notna(v) else 0
        val = st.slider(label, lo, hi, start, key=f"{key}_{cid}",
                        help=None if pd.notna(v) else "Not evaluated for this customer (no "
                                                      "data); moving it adds a value")
        return col, val, start

    st.divider()
    st.subheader("Adjust metrics")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.caption("**Repayment behaviour**")
        wi_late = slider("Late/missed months (6m)", "late_or_missed_instalments", 0, 6, "wi_late")
        wi_dpd = slider("Current DPD", "current_dpd", 0, 180, "wi_dpd")
        wi_partial = slider("Partial payment months (6m)", "partial_payments", 0, 6,
                            "wi_partial")
    with c2:
        st.caption("**Cash flow**")
        wi_inflow = slider("Inflow change (3m vs prior 3m) %",
                           "inflow_change_3m_vs_prior_3m_pct", -80, 50, "wi_inflow")
        wi_balance = slider("Avg balance change %", "avg_balance_change_pct", -90, 50,
                            "wi_balance")
        wi_salary_miss = slider("Months without salary (3m)", "months_without_salary", 0, 3,
                                "wi_salary")
    with c3:
        st.caption("**Exposure & leverage**")
        wi_util = slider("Revolving utilisation %", "revolving_utilisation_pct", 0, 120, "wi_util")
        wi_dbr = slider("Debt burden ratio %", "debt_burden_ratio_pct", 0, 100, "wi_dbr")
        wi_newf = slider("New facilities / enquiries (6m)", "new_facilities_or_enquiries", 0, 10,
                         "wi_newf")

    modified = M.copy()
    for col, val, start in (wi_late, wi_dpd, wi_partial, wi_inflow, wi_balance, wi_salary_miss,
                            wi_util, wi_dbr, wi_newf):
        if val != start:
            modified.loc[cid, col] = val
    if wi_late[1] != wi_late[2]:
        # Consecutive misses can't exceed total irregular months.
        modified.loc[cid, "_consecutive_missed_months"] = min(
            original.get("_consecutive_missed_months", 0) or 0, wi_late[1])

    wi_score = scoring.score(modified, ss.rules, ss.tables["customers"])
    orig_row = view.loc[cid]
    wi_row = wi_score.loc[cid]

    st.divider()
    st.subheader("Result comparison")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**Original** &nbsp; {tier_badge(orig_row.tier)}", unsafe_allow_html=True)
        st.metric("Score", f"{orig_row.risk_score:.0f}")
        st.metric("Confidence", orig_row.confidence)
        for reason in orig_row.reasons[:5]:
            st.markdown(f"- {reason}")
    with c2:
        st.markdown(f"**What-If** &nbsp; {tier_badge(wi_row.tier)}", unsafe_allow_html=True)
        delta = wi_row.risk_score - orig_row.risk_score
        st.metric("Score", f"{wi_row.risk_score:.0f}", f"{delta:+.0f}",
                  delta_color="inverse")
        st.metric("Confidence", wi_row.confidence)
        for reason in wi_row.reasons[:5]:
            st.markdown(f"- {reason}")
    if wi_row.trigger:
        st.error(f"Hard trigger active: {wi_row.trigger}")

    with st.expander("Signal-by-signal comparison"):
        orig_sigs = pd.DataFrame(orig_row.evidence["signals"])[
            ["id", "name", "value", "strength", "points"]].rename(
            columns={"value": "orig_value", "strength": "orig_str", "points": "orig_pts"})
        wi_sigs = pd.DataFrame(wi_row.evidence["signals"])[
            ["id", "value", "strength", "points"]].rename(
            columns={"value": "wi_value", "strength": "wi_str", "points": "wi_pts"})
        cmp = orig_sigs.merge(wi_sigs, on="id")
        cmp["delta_pts"] = cmp.wi_pts - cmp.orig_pts
        st.dataframe(cmp[cmp.delta_pts.abs() > 0.01], hide_index=True, use_container_width=True)

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
