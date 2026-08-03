"""chapter_helpers.py — shared data + section builders for the per-RQ book.

Each chapter .qmd is thin: it sets ``N = <rq number>`` and calls the builders here,
so the eighteen chapters stay consistent and the logic lives in one place. Data is
read from the DEX extraction reports; the path is anchored to this file's location
so it works regardless of Quarto's execution directory.

Vocabularies (from the extraction schema):
  contribution type      Primary / Supporting / Contextual / Descriptive-only / Contradictory
  contribution strength  High / Moderate / Low / Insufficient / Critically-low
  contribution direction Confirms / Qualifies / Neutral / Refutes / Not-applicable  (stance to the RQ)
"""
from __future__ import annotations
import ast, json, re
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np
import pandas as pd

REPORTS = Path(__file__).resolve().parent.parent.parent / "data" / "extraction" / "reports"

# ---- validated (CVD-safe) palette: dataviz reference instance, light surface ----
CAT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
INK, INK2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#fcfcfb"

RQ_LABEL = {
    1: "Determinants of adult depression (LMIC)",
    2: "Which low-intensity group interventions are effective",
    3: "Parameters determining effectiveness",
    4: "Facilitator training, supervision and background",
    5: "Components associated with symptom reduction",
    6: "Components that can be dropped",
    7: "Minimum viable dose (length × sessions)",
    8: "Durability: short- versus longer-term effects",
    9: "When single- versus multi-session is appropriate",
    10: "Group-size ranges",
    11: "Spillover to general populations",
    12: "Stepped-care model design",
    13: "Non-specialist, lay and peer delivery",
    14: "Therapeutic model and active ingredients",
    15: "Design choices and engagement",
    16: "Cost drivers and cost-per-participant",
    17: "Safety monitoring and referral pathways",
    18: "Measurement tools: validity and reliability (LMIC)",
}
# the review's own question — used to open each chapter
RQ_QUESTION = {
    1: "What are the determinants of adult depression in low- and middle-income countries?",
    2: "Which low-intensity, group-based psychological interventions are effective for adult depression?",
    3: "What parameters determine whether these interventions are effective?",
    4: "What facilitator training, supervision and background are associated with effectiveness?",
    5: "Which intervention components are associated with symptom reduction?",
    6: "Which components can be dropped without losing effectiveness?",
    7: "What is the minimum viable dose (session length × number of sessions)?",
    8: "How durable are the effects, short- versus longer-term?",
    9: "When is a single-session appropriate versus a multi-session course?",
    10: "What group-size ranges are effective?",
    11: "Do effects spill over to general (non-case) populations?",
    12: "How should a stepped-care model be designed?",
    13: "Can non-specialist, lay or peer providers deliver effectively?",
    14: "Which therapeutic model and active ingredients matter?",
    15: "Which design choices affect engagement and completion?",
    16: "What are the cost drivers and the cost per participant?",
    17: "What safety monitoring and referral pathways are used?",
    18: "Which measurement tools are valid and reliable in LMIC settings?",
}

# study-level extraction was performed only for these RQs (prompt §A); the rest are
# answered from review-level records and get a review-synthesis table, not a trial table.
STUDY_LEVEL = {5, 6, 7, 8, 9, 10, 12, 13, 14, 16}

AM_ORDER = ["High", "Moderate", "Low", "Critically-low"]
STANCE_ORDER = ["Confirms", "Qualifies", "Neutral", "Refutes"]
STRENGTH_ORDER = ["High", "Moderate", "Low", "Insufficient", "Critically-low"]

# ---------------------------------------------------------------- load once
summ = pd.read_csv(REPORTS / "dex_summary.csv", dtype=str).fillna("")
long = pd.read_csv(REPORTS / "dex_long.csv", dtype=str,
                   usecols=["record_id", "field", "value"]).fillna("")
wide = pd.read_csv(REPORTS / "dex_wide.csv", dtype=str).fillna("")
contrib = pd.read_csv(REPORTS / "dex_rq_contributions_long.csv", dtype=str).fillna("")
ov_by_rq = pd.read_csv(REPORTS / "dex_overlap_by_rq.csv")


def rq_list(v: str):
    return [t for t in re.sub(r"[\[\]'\"]", "", v).replace(" ", "").split(",")
            if re.fullmatch(r"RQ\d{1,2}", t)]


summ["rq"] = summ["rq_tags"].map(rq_list)
wide["rq"] = wide["rq_tags"].map(rq_list)


def rq_has(df, tag):
    return df["rq"].map(lambda x: tag in x)


# per-RQ headline stats (mirrors the evidence-map report)
_rows = []
for n in range(1, 19):
    tag = f"RQ{n}"; sub = summ[rq_has(summ, tag)]; revs = sub[sub["unit"] == "review"]
    _rows.append({
        "rq": tag, "n": n, "records": len(sub),
        "reviews": int((sub["unit"] == "review").sum()),
        "primary": int((sub["unit"] == "primary_study").sum()),
        "ssa": int((sub["geo_focus"] == "SSA").sum()),
        "hq": int(revs["amstar2_band"].isin(["High", "Moderate"]).sum()),
        "lowq": int(revs["amstar2_band"].isin(["Low", "Critically-low"]).sum()),
    })
rqstats = pd.DataFrame(_rows)
rqstats["answerable"] = (rqstats["hq"] >= 1) & (rqstats["records"] >= 5)
rqstats["status"] = np.where(rqstats["answerable"], "Answerable",
                    np.where(rqstats["records"] > 0, "Attempted (low-certainty)", "No evidence"))


def _fieldsets(min_freq=3):
    fld = defaultdict(Counter)
    for _, r in contrib.iterrows():
        rq = r["rq_id"]; raw = r["rq_contribution_data_fields"] or ""
        try:
            vals = ast.literal_eval(raw); vals = vals if isinstance(vals, list) else [vals]
        except Exception:
            vals = raw.split(",")
        for f in vals:
            f = str(f).strip(" []'\"")
            if f:
                fld[rq][f] += 1
    return {rq: [f for f, c in cnt.most_common() if c >= min_freq] for rq, cnt in fld.items()}


FIELDSETS = _fieldsets()


# ---------------------------------------------------------------- helpers
def _tag(n):
    return f"RQ{n}"


def is_study_level(n):
    return n in STUDY_LEVEL


def short(s, k=70):
    s = str(s or "").strip()
    return s if len(s) <= k else s[: k - 1].rstrip() + "…"


def field_report(field, ids=None):
    """(n_reported, top-values-Counter, numeric-Series-or-None) for an extracted field.
    Restricted to ``ids`` (a set of record_ids) when given, so per-RQ chapters report
    counts within their own evidence base rather than across the whole corpus."""
    rows = long.loc[long["field"] == field]
    if ids is not None:
        rows = rows[rows["record_id"].isin(ids)]
    s = rows["value"]
    s = s[~s.isin(["", "NA", "null", "[]", "Not-reported", "Not-applicable"])]
    nums = pd.to_numeric(s, errors="coerce").dropna()
    c = Counter()
    for v in s:
        try:
            j = json.loads(v); items = j if isinstance(j, list) else [j]
        except Exception:
            items = [v]
        for x in items:
            xs = str(x).strip()
            if xs and not xs.lower().startswith("not-reported"):
                c[xs] += 1
    return len(s), c, (nums if len(nums) >= 5 else None)


# human-readable labels for the recurring RQ-specific fields (prose reads better than
# raw column names). Anything not listed falls back to the de-underscored field name.
FIELD_LABEL = {
    "dose_n_sessions": "the number of sessions", "dose_duration_wk": "course length in weeks",
    "dose_session_min": "session length in minutes", "dose_total_contact_min": "total contact time in minutes",
    "dose_freq": "session frequency", "dose_band": "the dose band", "dose_response_curve": "the dose–response shape",
    "dose_response_inflection": "the dose–response inflection point", "dose_delivered_pct": "the proportion of the dose delivered",
    "dose_homework_min": "between-session practice in minutes", "dose_response_by_severity": "dose–response by baseline severity",
    "driver_named": "the named determinant", "driver_domain": "the determinant domain",
    "driver_direction": "the direction of association", "driver_effect_size": "the determinant effect size",
    "mechanism_claim": "the proposed mechanism", "mediation_analysis": "a mediation analysis", "mediator_named": "the named mediator",
    "psychom_instrument": "the measurement instrument", "psychom_construct": "the construct measured",
    "psychom_reliability": "reliability", "psychom_sensitivity": "sensitivity", "psychom_specificity": "specificity",
    "psychom_cutoff": "the cut-off score", "psychom_setting": "the validation setting", "psychom_language": "the validation language",
    "cost_reported": "whether cost is reported", "cost_per_pt_local": "cost per participant",
    "cost_effectiveness_ratio": "the cost-effectiveness ratio", "cost_metric": "the cost metric",
    "cost_currency": "the currency", "cost_perspective": "the costing perspective", "cost_year": "the cost year",
    "cost_components": "the cost components", "cost_driver": "the main cost driver", "ce_threshold_used": "the cost-effectiveness threshold",
    "safety_pathway": "the referral/safety pathway", "safety_monitoring_practice": "the safety-monitoring practice",
    "ae_reported": "whether adverse events are reported", "ae_type": "the adverse-event type", "ae_rate": "the adverse-event rate",
    "engage_completion_pct": "the completion rate", "engage_attendance_pct": "the attendance rate",
    "engagement_driver": "the engagement driver", "engagement_barrier": "the engagement barrier",
    "component_contribution": "the component's contribution", "specialist_delivered_flag": "specialist delivery",
    "stepped_care_steps": "the stepped-care steps", "eff_direction": "the direction of effect",
    "out_primary": "the primary outcome", "out_scale": "the outcome scale", "pop_n": "the sample size",
    "pop_descr": "the population", "int_techniques": "the intervention techniques",
}


def humanize(f):
    return FIELD_LABEL.get(f, f.replace("_", " "))


def fmt_num(x):
    if abs(x) >= 1000:
        return f"{round(x):,}"
    if abs(x - round(x)) < 0.05:
        return f"{int(round(x))}"
    return f"{x:.1f}"


def numeric_profile(n, specs):
    """A tidy median [IQR], n table for a set of numeric fields within an RQ's records.
    ``specs`` is a list of (field, label, unit). Fields with fewer than 5 values are
    dropped. Reusable across questions (dose for RQ7, group size for RQ10, cost RQ16…)."""
    ids = set(_sub(n)["record_id"])
    w = wide[wide["record_id"].isin(ids)]
    rows = []
    for field, label, unit in specs:
        if field not in w.columns:
            continue
        s = pd.to_numeric(w[field], errors="coerce").dropna()
        if len(s) < 5:
            continue
        rows.append({"Dimension": label,
                     "Median [IQR]": f"{fmt_num(s.median())} [{fmt_num(s.quantile(.25))}–{fmt_num(s.quantile(.75))}]",
                     "Unit": unit, "n": len(s)})
    return pd.DataFrame(rows) if rows else None


def smd_magnitude(n):
    """Absolute standardized effect sizes (metric = SMD) within an RQ's records.
    Sign conventions differ across the extracted records, so magnitude is reported
    unsigned; direction is taken from eff_direction, not the SMD sign. Returns
    (n, median, q1, q3, share_at_least_0_2) or None."""
    ids = set(_sub(n)["record_id"])
    w = wide[wide["record_id"].isin(ids)]
    s = pd.to_numeric(w.loc[w["eff_metric"] == "SMD", "eff_value"], errors="coerce").dropna()
    s = s[(s > -5) & (s < 5)].abs()
    if len(s) < 8:
        return None
    return len(s), float(s.median()), float(s.quantile(.25)), float(s.quantile(.75)), float((s >= 0.2).mean())


def direction_subsets(n):
    """Share favouring the intervention, overall and in the subsets that bear on
    robustness: higher-quality reviews, LMIC and SSA focus, and non-specialist
    delivery. Returns [(label, n_favouring, n_directional), ...] for subsets with
    at least three directional records."""
    ids = set(_sub(n)["record_id"]); w = wide[wide["record_id"].isin(ids)]

    def share(df):
        c = Counter(df["eff_direction"]); fav = c.get("Favours-intervention", 0)
        tot = fav + c.get("Null", 0) + c.get("Favours-control", 0) + c.get("Unclear", 0)
        return fav, tot

    cand = [("All records", w),
            ("High/moderate-quality reviews", w[w["amstar2_band"].isin(["High", "Moderate"])]),
            ("LMIC-focused records", w[w["geo_focus"].isin(["SSA", "other-LMIC"])]),
            ("Sub-Saharan Africa only", w[w["geo_focus"] == "SSA"]),
            ("Non-specialist delivery", w[w["specialist_delivered_flag"] == "False"])]
    out = []
    for lab, df in cand:
        fav, tot = share(df)
        if tot >= 3:
            out.append((lab, fav, tot))
    return out


def comparator_summary(n):
    """(Counter of comparators, n_active_psychological, n_reporting) for an RQ."""
    ids = set(_sub(n)["record_id"])
    nrep, cnt, _num = field_report("comparator", ids)
    return cnt, cnt.get("Active-psychological", 0), nrep


def anchor_reviews(n, k=4):
    """The highest-quality (AMSTAR-2 High/Moderate) reviews behind an RQ: [(title, year)]."""
    ids = set(_sub(n)["record_id"]); w = wide[wide["record_id"].isin(ids)]
    hq = w[(w["amstar2_band"].isin(["High", "Moderate"])) & (w["unit"] == "review")]
    return [(short(r["title"], 82), r["year"]) for _, r in hq.head(k).iterrows()
            if str(r["title"]).strip()]


def eff_consistency(n):
    """Apply the protocol's pre-specified vote-count rule to the reported direction of
    effect: >=75% favouring the intervention = consistent; 50-74% = mixed; <50% =
    inconsistent. Returns (label, n_favouring, n_directional)."""
    ids = set(_sub(n)["record_id"])
    _n, cnt, _num = field_report("eff_direction", ids)
    fav = cnt.get("Favours-intervention", 0)
    tot = fav + cnt.get("Null", 0) + cnt.get("Favours-control", 0) + cnt.get("Unclear", 0)
    if not tot:
        return "not assessable", 0, 0
    share = fav / tot
    lab = "consistent" if share >= 0.75 else "mixed" if share >= 0.5 else "inconsistent"
    return lab, fav, tot


def grade_lite(n):
    """Conservative provisional certainty band. Confidence is capped at 'Low' throughout,
    because the evidence base is (i) predominantly low or critically-low quality on
    AMSTAR-2, (ii) machine-extracted and not yet human-verified, and (iii) summarised by
    review-level vote-counting — a method that counts overlapping, non-independent reviews
    as if they were separate studies and is insensitive to effect size and precision. No
    question earns more than 'Low' under these conditions. Questions with mixed or
    inconsistent direction, no high- or moderate-quality review, or a very small base are
    'Very low'."""
    rs = rqstats.iloc[n - 1]
    cons, _f, _t = eff_consistency(n)
    hq = int(rs["hq"]); recs = int(rs["records"])
    if cons in ("inconsistent", "mixed") or hq == 0 or recs < 20:
        return "Very low"
    return "Low"


def overview_table_df():
    """Cross-RQ summary for the overview chapter (one row per question)."""
    rows = []
    for n in range(1, 19):
        rs = rqstats.iloc[n - 1]
        rows.append({"RQ": f"RQ{n}", "Question": RQ_LABEL[n], "Records": int(rs["records"]),
                     "Reviews": int(rs["reviews"]), "High/mod. reviews": int(rs["hq"]),
                     "SSA": int(rs["ssa"]), "Status": rs["status"]})
    return pd.DataFrame(rows)


def _sub(n):
    return summ[rq_has(summ, _tag(n))]


def _contrib(n):
    return contrib[contrib["rq_id"] == _tag(n)]


def _pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "—"


# ---------------------------------------------------------------- section builders
def headline_md(n):
    """Opening synthesis: a short, data-driven statement of what the evidence supports."""
    rs = rqstats.iloc[n - 1]; c = _contrib(n)
    ans = ("On the criteria used here the question is answerable in principle"
           if rs["answerable"] else
           "On the criteria used here the question can only be attempted, on low-certainty evidence")
    d = Counter(c["rq_contribution_direction"]); tot = len(c) or 1
    conf = d.get("Confirms", 0)
    if conf / tot >= 0.7:
        dir_txt = (f"the direction of the evidence is consistent: {_pct(conf, tot)} of the {tot} recorded "
                   f"contributions confirm the proposition the question tests")
    else:
        dir_txt = (f"the evidence is directionally mixed, with {_pct(conf, tot)} of {tot} contributions "
                   f"confirming the proposition the question tests")
    lead = ""
    for f in FIELDSETS.get(_tag(n), []):
        if (f.endswith("_quote") or f in _PARAM_SKIP or f in _META_SKIP
                or f in ("pop_n", "n_included_studies")):
            continue
        nrep, _c, nums = field_report(f, set(_sub(n)["record_id"]))
        if nums is not None and nrep >= 10:
            lead = (f" Where reported, {humanize(f)} centres on a median of {fmt_num(nums.median())} "
                    f"(IQR {fmt_num(nums.quantile(.25))}–{fmt_num(nums.quantile(.75))}, n={nrep}).")
            break
    return (f"The question draws on {rs['records']:,} records ({rs['reviews']:,} reviews), of which "
            f"{rs['hq']} reach high or moderate methodological quality. {ans}. Across {tot:,} recorded "
            f"contributions {dir_txt}, though most are of low individual strength.{lead}")


def evidence_base_md(n):
    rs = rqstats.iloc[n - 1]
    sub = _sub(n)
    geo = Counter(sub["geo_focus"])
    des = Counter(d for d in sub["design"] if d and d != "Not-reported")
    revs = sub[sub["unit"] == "review"]
    nrev = int(revs.shape[0])
    rated = int(revs["amstar2_band"].isin(AM_ORDER).sum())
    ssa, olmic = geo.get("SSA", 0), geo.get("other-LMIC", 0)
    hic, mixed = geo.get("HIC-UMIC", 0), geo.get("mixed", 0)
    lmic = ssa + olmic
    des_txt = ", ".join(f"{v} {k.lower()}" for k, v in des.most_common(4))
    if rs["primary"] == 0:
        comp = (f"The question is addressed by **{rs['records']:,} included records**, all of them systematic "
                f"reviews or meta-analyses; study-level extraction was not performed for it, so the evidence "
                f"is entirely review-level. Their most common designs are {des_txt or 'not reported'}.")
    else:
        comp = (f"The question is addressed by **{rs['records']:,} included records** ({rs['reviews']:,} "
                f"systematic reviews or meta-analyses and {rs['primary']:,} primary studies), with "
                f"{des_txt or 'unspecified'} the most common designs.")
    geo_s = (f"Geographically the evidence sits mostly outside the review's priority setting: "
             f"**{ssa} records ({_pct(ssa, rs['records'])}) focus on sub-Saharan Africa** and {olmic} on other "
             f"low- and middle-income countries, against {hic} in high- or upper-middle-income countries and "
             f"{mixed} spanning mixed settings. Evidence generated in, or explicitly about, low-income African "
             f"settings is a small minority ({lmic} of {rs['records']:,}).")
    if nrev:
        q_s = (f"Methodological quality is a binding constraint on what can be concluded: of {rated} review-level "
               f"records carrying an AMSTAR-2 rating, **{rs['hq']} are high or moderate** and {rs['lowq']} "
               f"({_pct(rs['lowq'], rated)}) are low or critically low, so most of the evidence enters at low certainty.")
    else:
        q_s = "There are no review-level records for this question to rate for methodological quality."
    return comp + " " + geo_s + " " + q_s


def stance_md(n):
    c = _contrib(n)
    if not len(c):
        return "_No study-to-question contributions were recorded for this question._"
    d = Counter(c["rq_contribution_direction"]); st = Counter(c["rq_contribution_strength"])
    ty = Counter(c["rq_contribution_type"]); tot = len(c)
    conf, qual = d.get("Confirms", 0), d.get("Qualifies", 0)
    neu, refu = d.get("Neutral", 0), d.get("Refutes", 0)
    hi = st.get("High", 0) + st.get("Moderate", 0)
    lo = st.get("Low", 0) + st.get("Insufficient", 0) + st.get("Critically-low", 0)
    prim, supp = ty.get("Primary-evidence", 0), ty.get("Supporting-evidence", 0)
    consistent = conf / tot >= 0.7
    p1 = (f"The {tot:,} recorded study-to-question contributions point in a "
          f"{'consistent' if consistent else 'mixed'} direction: **{conf} ({_pct(conf, tot)}) confirm** the "
          f"proposition the question tests, {qual} qualify it, {neu} are neutral, and {refu} refute it.")
    p2 = (f"This agreement must be read against its weight. Only {hi} of {tot} contributions are rated high or "
          f"moderate strength, against {lo} low or insufficient, and {prim} rest on primary evidence rather "
          f"than the {supp} that are supporting or contextual. The evidence for this question is best "
          f"described as consistent in direction but weak in individual weight: many records agree, few carry "
          f"decisive weight. Stance here is the extractor's judgement of how each record bears on the "
          f"question, not a graded effect estimate.")
    return p1 + " " + p2


def params_prose_md(n):
    """Read the RQ-specific parameter table into two interpretive sentences."""
    ids = set(_sub(n)["record_id"])
    nums, cats = [], []
    for f in FIELDSETS.get(_tag(n), []):
        if f.endswith("_quote") or f in _PARAM_SKIP or f in _META_SKIP or f == "eff_direction":
            continue
        nrep, cnt, numeric = field_report(f, ids)
        if not nrep:
            continue
        if numeric is not None:
            nums.append((f, nrep, numeric))
        elif cnt.most_common(1):
            (val, k) = cnt.most_common(1)[0]
            # keep only clean, genuinely-modal short categories: skip free-text fields
            # (long modal value), high-cardinality fields (no dominant category), boolean
            # flags, and uninformative modal values (they stay in the table).
            if (len(val) <= 30 and k / nrep >= 0.25 and not f.endswith("_flag")
                    and val.lower() not in _JUNK_MODAL):
                cats.append((f, nrep, val, k))
    nums.sort(key=lambda x: -x[1]); cats.sort(key=lambda x: -x[1])
    parts = []
    if nums:
        seg = "; ".join(f"{humanize(f)} at a median of {fmt_num(s.median())} "
                        f"(IQR {fmt_num(s.quantile(.25))}–{fmt_num(s.quantile(.75))}, n={nrep})"
                        for f, nrep, s in nums[:3])
        parts.append(f"Where these are quantified, the records report {seg}.")
    if cats:
        seg = "; ".join(f"{humanize(f)} is most often *{val.lower()}* ({_pct(k, nrep)} of the {nrep} reporting it)"
                        for f, nrep, val, k in cats[:3])
        parts.append(f"Among the categorical descriptors, {seg}.")
    return " ".join(parts) if parts else "Few question-specific parameters were recorded for this question."


def stance_table_df(n):
    c = _contrib(n)
    if not len(c):
        return None
    m = pd.crosstab(c["rq_contribution_direction"], c["rq_contribution_strength"])
    m = m.reindex(index=[d for d in STANCE_ORDER if d in m.index],
                  columns=[s for s in STRENGTH_ORDER if s in m.columns], fill_value=0)
    m.insert(0, "Total", m.sum(axis=1))
    return m.reset_index().rename(columns={"rq_contribution_direction": "Stance ↓ / strength →"})


_PARAM_SKIP = {"eff_quote", "eff_value", "eff_ci_lo", "eff_ci_hi"}
# descriptive metadata already covered in the evidence-base paragraph; not question parameters
_META_SKIP = {"geo_focus", "country", "design", "unit", "rq_tags", "doctype", "title", "year",
              "amstar2_band", "rob_fatal_flaw", "eff_metric", "comparator"}
# uninformative modal categories to keep out of the prose (they remain in the table)
_JUNK_MODAL = {"other", "other-technique", "unclear", "not-reported", "not-applicable",
               "none", "mixed", "unspecified", "true", "false", ""}


def params_table_df(n, max_rows=12):
    ids = set(_sub(n)["record_id"])
    rows = []
    for f in FIELDSETS.get(_tag(n), []):
        if f.endswith("_quote") or f in _PARAM_SKIP or f in _META_SKIP:
            continue
        nrep, cnt, nums = field_report(f, ids)
        if not nrep:
            continue
        if nums is not None:
            summary = (f"median {nums.median():.0f} "
                       f"(IQR {nums.quantile(.25):.0f}–{nums.quantile(.75):.0f}, "
                       f"n={len(nums)})")
        else:
            summary = "; ".join(f"{k} ({v})" for k, v in cnt.most_common(3))
        rows.append({"field": f, "n reported": nrep, "most common / summary": short(summary, 60)})
    if not rows:
        return None
    df = pd.DataFrame(rows).sort_values("n reported", ascending=False).head(max_rows)
    return df.reset_index(drop=True)


def _best_strength(c):
    order = {s: i for i, s in enumerate(STRENGTH_ORDER)}
    by = {}
    for _, r in c.iterrows():
        rid = r["record_id"]; s = r["rq_contribution_strength"]
        if rid not in by or order.get(s, 9) < order.get(by[rid], 9):
            by[rid] = s
    return by


# compact labels so the evidence table fits a portrait PDF page
QUAL_ABBR = {"High": "High", "Moderate": "Mod", "Low": "Low", "Critically-low": "Crit-low",
             "No-fatal-flaw": "no flaw", "Some-concerns": "some", "No-concerns": "low ROB",
             "Not-applicable": "n/a", "": "—"}
STR_ABBR = {"High": "High", "Moderate": "Mod", "Low": "Low", "Insufficient": "Insuff",
            "Critically-low": "Crit", "": "—"}


def evidence_table_df(n, max_rows=15):
    tag = _tag(n); sub = _sub(n).copy()
    if not len(sub):
        return None, 0
    best = _best_strength(_contrib(n))
    sub["_s"] = sub["record_id"].map(lambda r: {s: i for i, s in enumerate(STRENGTH_ORDER)}
                                     .get(best.get(r, ""), 9))
    sub = sub.sort_values(["_s", "year"], ascending=[True, False])
    rows = []
    for _, r in sub.head(max_rows).iterrows():
        rid = r["record_id"]
        qual = r["amstar2_band"] or r["rob_fatal_flaw"] or ""
        rows.append({
            "id": rid, "title": short(r["title"], 34), "yr": r["year"],
            "design": short(r["design"], 12), "geo": r["geo_focus"],
            "quality": QUAL_ABBR.get(qual, short(qual, 10)),
            "strength": STR_ABBR.get(best.get(rid, ""), "—")})
    return pd.DataFrame(rows), len(sub)


def quotes_md(n, k=5):
    c = _contrib(n)
    if not len(c):
        return ""
    c = c[c["rq_contribution_summary"].str.len() > 25].copy()
    c["_s"] = c["rq_contribution_strength"].map(
        lambda s: {x: i for i, x in enumerate(STRENGTH_ORDER)}.get(s, 9))
    c = c.sort_values("_s").drop_duplicates("rq_contribution_summary").head(k)
    out = []
    for _, r in c.iterrows():
        summ_txt = r["rq_contribution_summary"].strip().rstrip(".")
        q = r["rq_contribution_quote"].strip()
        line = f"- {summ_txt}. *({r['record_id']}, {r['rq_contribution_strength'] or 'unrated'} strength)*"
        if q and len(q) > 15:
            line += f"\n  > “{short(q, 240)}”"
        out.append(line)
    return "\n".join(out)


def evidence_quotes_md(n, k=6):
    """Representative extracted findings, each presented as a cited blockquote: the
    verbatim sentence from the source first, then the extracted finding, source record
    and extraction strength. Cleaner and more scannable than a raw bullet list."""
    c = _contrib(n)
    if not len(c):
        return "_No representative evidence was recorded for this question._"
    c = c[c["rq_contribution_summary"].str.len() > 25].copy()
    c["_s"] = c["rq_contribution_strength"].map(
        lambda s: {x: i for i, x in enumerate(STRENGTH_ORDER)}.get(s, 9))
    c = c.sort_values("_s").drop_duplicates("rq_contribution_summary").head(k)
    out = []
    for _, r in c.iterrows():
        q = r["rq_contribution_quote"].strip()
        summ = r["rq_contribution_summary"].strip().rstrip(".")
        s = r["rq_contribution_strength"] or "unrated"
        rid = r["record_id"]
        if q and len(q) > 10:
            out.append(f"> “{short(q, 260)}”\n>\n> {summ}. — Source `{rid}` · extraction strength: {s}.")
        else:
            out.append(f"> {summ}. — Source `{rid}` · extraction strength: {s}.")
    return "\n\n".join(out)


def concordance_md(n):
    row = ov_by_rq[ov_by_rq["rq"] == _tag(n)]
    if not len(row):
        return "_No overlap statistics available for this question._"
    r = row.iloc[0]
    nrev = int(r["n_reviews_with_studies"]) if str(r["n_reviews_with_studies"]).strip() else 0
    # a standing caveat on what the direction counts in this chapter can and cannot bear
    caveat = (" This should not be read as independence. The direction counts in this chapter are "
              "dominated by systematic reviews rather than primary studies, and those reviews re-analyse "
              "heavily overlapping sets of the same trials, so a given trial is represented many times over. "
              "Across a review pool this large the corrected covered area is diluted toward zero as an "
              "artefact of scale rather than a sign of separate evidence. The near-uniform agreement in "
              "direction is therefore close to what this review-level vote-count — and the reporting bias "
              "typical of the depression-intervention literature — would produce in any case, and is weaker "
              "than the raw counts imply (see Methods).")
    if nrev < 2 or not str(r["cca_pct"]).strip():
        return (f"Only {nrev} review(s) addressing this question report their pooled trial lists, so "
                f"cross-review overlap cannot be estimated." + caveat)
    shared = int(r["shared_pairs"]) if str(r["shared_pairs"]).strip() else 0
    disc = r["discordance_pct"]
    txt = (f"Among the {nrev} reviews that report their pooled trial lists, the corrected covered area "
           f"(CCA; Pieper et al. 2014) is **{r['cca_pct']}% ({r['overlap_rating']})** across "
           f"{r['n_unique_studies']} unique trials")
    if shared and str(disc).strip():
        txt += f", and among {shared} review pairs sharing trials, opposing directions are rare ({disc}%)."
    else:
        txt += "."
    return txt + caveat


def gaps_md(n):
    rs = rqstats.iloc[n - 1]
    sub = _sub(n); revs = sub[sub["unit"] == "review"]
    lowshare = _pct(rs["lowq"], revs.shape[0]) if revs.shape[0] else "—"
    bits = []
    if rs["hq"] == 0:
        bits.append("no high- or moderate-quality review addresses this question")
    elif rs["hq"] < 3:
        bits.append(f"only {rs['hq']} high- or moderate-quality review(s) are available")
    if revs.shape[0] and rs["lowq"] / revs.shape[0] >= 0.5:
        bits.append(f"{lowshare} of reviews are low or critically-low quality")
    if rs["ssa"] == 0:
        bits.append("no record is focused on sub-Saharan Africa")
    elif rs["ssa"] < 5:
        bits.append(f"only {rs['ssa']} record(s) focus on sub-Saharan Africa")
    if rs["records"] < 30:
        bits.append(f"the evidence base is thin ({rs['records']} records)")
    if not bits:
        return ("This question is comparatively well covered; the main residual limitation "
                "is that findings rest on machine-extracted, not yet human-verified, data.")
    return "The main gaps for this question: " + "; ".join(bits) + "."

