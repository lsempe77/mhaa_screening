"""figures.py — the figure engine for the per-RQ synthesis report.

All charts are built on the validated CVD-safe palette (the dataviz reference
instance, light surface) carried in chapter_helpers. Each function returns a
matplotlib Figure so a chapter can display it directly. Functions are defensive:
when a field is absent or too sparse they return a small "not recorded" placeholder
rather than raising, so the templated chapters never break.

Colour semantics are fixed and meaningful, never decorative:
  stance      Confirms=good, Qualifies=warning, Neutral=muted, Refutes=critical
  effect dir  Favours-intervention=good, Null=muted, Favours-control=critical, Unclear=warning
  quality     High=good, Moderate=warning, Low=serious, Critically-low=critical
  geography   SSA / other-LMIC / HIC-UMIC / mixed (categorical slots)
"""
from __future__ import annotations
from collections import Counter
import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt

import chapter_helpers as H

# ---------------------------------------------------------------- style
INK, INK2, MUTED, GRID, SURFACE = H.INK, H.INK2, H.MUTED, H.GRID, H.SURFACE
CAT, STATUS = H.CAT, H.STATUS

mpl.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.family": "sans-serif", "font.size": 10.5,
    "text.color": INK, "axes.labelcolor": INK2, "axes.edgecolor": "#c3c2b7",
    "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "axes.axisbelow": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.titlesize": 11.5, "axes.titleweight": "bold", "axes.titlecolor": INK,
    "figure.dpi": 130,
})

STANCE_COL = {"Confirms": STATUS["good"], "Qualifies": STATUS["warning"],
              "Neutral": MUTED, "Refutes": STATUS["critical"]}
EFFDIR_COL = {"Favours-intervention": STATUS["good"], "Null": MUTED,
              "Favours-control": STATUS["critical"], "Unclear": STATUS["warning"]}
AM_COL = {"High": STATUS["good"], "Moderate": STATUS["warning"],
          "Low": STATUS["serious"], "Critically-low": STATUS["critical"]}
GEO_COL = {"SSA": CAT[0], "other-LMIC": CAT[2], "HIC-UMIC": CAT[1], "mixed": MUTED}
GEO_ORDER = ["SSA", "other-LMIC", "HIC-UMIC", "mixed"]


def _placeholder(msg="Not recorded for this question", h=1.3):
    fig, ax = plt.subplots(figsize=(6.6, h))
    ax.axis("off")
    ax.text(0.5, 0.5, msg, ha="center", va="center", color=MUTED, style="italic", fontsize=10)
    return fig


def _hbar_labels(ax, bars, values, fmt="{:.0f}", pad=0.4):
    for b, v in zip(bars, values):
        if v > 0:
            ax.text(b.get_width() + pad, b.get_y() + b.get_height() / 2, fmt.format(v),
                    va="center", ha="left", fontsize=9, color=INK2)


# ---------------------------------------------------------------- per-RQ
def stance(n):
    c = H._contrib(n)
    if not len(c):
        return _placeholder("No study-to-question contributions recorded")
    cnt = Counter(c["rq_contribution_direction"])
    order = [s for s in ["Confirms", "Qualifies", "Neutral", "Refutes"] if cnt.get(s)]
    vals = [cnt[s] for s in order]
    fig, ax = plt.subplots(figsize=(6.6, 0.5 + 0.42 * len(order)))
    y = np.arange(len(order))[::-1]
    bars = ax.barh(y, vals, color=[STANCE_COL[s] for s in order], height=0.62)
    ax.set_yticks(y); ax.set_yticklabels(order)
    tot = sum(vals)
    _hbar_labels(ax, bars, vals, fmt="{:.0f}")
    ax.set_xlim(0, max(vals) * 1.15)
    ax.set_xlabel(f"contributions (n = {tot})")
    ax.set_title("Stance of the contributing records")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    return fig


def effect_direction(n):
    ids = set(H._sub(n)["record_id"])
    _n, cnt, _num = H.field_report("eff_direction", ids)
    order = [s for s in ["Favours-intervention", "Null", "Favours-control", "Unclear"] if cnt.get(s)]
    if not order:
        return _placeholder("Direction of effect not recorded")
    vals = [cnt[s] for s in order]
    fig, ax = plt.subplots(figsize=(6.6, 0.5 + 0.42 * len(order)))
    y = np.arange(len(order))[::-1]
    bars = ax.barh(y, vals, color=[EFFDIR_COL[s] for s in order], height=0.62)
    ax.set_yticks(y); ax.set_yticklabels([s.replace("-", " ") for s in order])
    _hbar_labels(ax, bars, vals)
    ax.set_xlim(0, max(vals) * 1.15)
    ax.set_xlabel(f"records (n = {sum(vals)})")
    ax.set_title("Reported direction of effect")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    return fig


def numeric_hist(n, field, label=None, unit=""):
    ids = set(H._sub(n)["record_id"])
    nrep, _c, nums = H.field_report(field, ids)
    if nums is None or len(nums) < 8:
        return _placeholder(f"{label or H.humanize(field)}: too few records to plot")
    label = label or H.humanize(field)
    fig, ax = plt.subplots(figsize=(6.6, 3.0))
    ax.hist(nums, bins=min(20, max(6, len(nums) // 6)), color=CAT[0], edgecolor=SURFACE, linewidth=0.7)
    med = float(nums.median())
    ax.axvline(med, color=STATUS["critical"], lw=1.6)
    ax.text(med, ax.get_ylim()[1] * 0.94, f" median {H.fmt_num(med)}{unit}",
            color=STATUS["critical"], fontsize=9, va="top")
    ax.set_xlabel(f"{label}{(' (' + unit.strip() + ')') if unit else ''}")
    ax.set_ylabel("records")
    ax.set_title(f"{label[0].upper() + label[1:]} (n = {len(nums)})")
    ax.grid(axis="x", visible=False)
    fig.tight_layout()
    return fig


def category_bar(n, field, label=None, top=8):
    ids = set(H._sub(n)["record_id"])
    nrep, cnt, _num = H.field_report(field, ids)
    items = [(k, v) for k, v in cnt.most_common(top) if k.lower() not in H._JUNK_MODAL and len(k) <= 40]
    if not items:
        return _placeholder(f"{label or H.humanize(field)}: not recorded")
    label = label or H.humanize(field)
    ks = [k for k, _ in items][::-1]; vs = [v for _, v in items][::-1]
    fig, ax = plt.subplots(figsize=(6.6, 0.6 + 0.4 * len(ks)))
    y = np.arange(len(ks))
    bars = ax.barh(y, vs, color=CAT[0], height=0.66)
    ax.set_yticks(y); ax.set_yticklabels([H.short(k, 34) for k in ks])
    _hbar_labels(ax, bars, vs)
    ax.set_xlim(0, max(vs) * 1.15)
    ax.set_xlabel(f"records (n reporting = {nrep})")
    ax.set_title(label[0].upper() + label[1:])
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    return fig


def robustness(n):
    """Direction of effect (share favouring the intervention) across the subsets that
    test whether the headline survives the caveats. The 75% line is the protocol's
    consistency threshold; bars are coloured consistent / mixed / inconsistent."""
    data = H.direction_subsets(n)
    if not data:
        return _placeholder("Direction of effect not recorded")
    labs = [d[0] for d in data]
    shares = [100 * d[1] / d[2] for d in data]
    ns = [d[2] for d in data]
    fig, ax = plt.subplots(figsize=(6.8, 0.7 + 0.46 * len(labs)))
    y = np.arange(len(labs))[::-1]
    # subsets with fewer than 10 records are too small to classify — shown in grey so the
    # consistent/mixed/inconsistent colour is not read as a verdict on a handful of records.
    SMALL = 10
    colors = [MUTED if nn < SMALL else
              STATUS["good"] if s >= 75 else STATUS["warning"] if s >= 50 else STATUS["critical"]
              for s, nn in zip(shares, ns)]
    bars = ax.barh(y, shares, color=colors, height=0.6)
    ax.axvline(75, color=MUTED, ls="--", lw=1)
    for b, s, nn in zip(bars, shares, ns):
        tag = f"{s:.0f}%  (n={nn})" + ("*" if nn < SMALL else "")
        ax.text(min(b.get_width() + 1.5, 99), b.get_y() + b.get_height() / 2,
                tag, va="center", ha="left", fontsize=8.5, color=INK2)
    ax.set_yticks(y); ax.set_yticklabels(labs, fontsize=9)
    ax.set_xlim(0, 108)
    xlab = "records favouring the intervention (%)"
    if any(nn < SMALL for nn in ns):
        xlab += "   ·   grey* = fewer than 10 records, too few to classify"
    ax.set_xlabel(xlab)
    ax.set_title("Direction of effect across subsets")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    return fig


def effect_forest(n):
    """Protocol 4.5.2: the distribution of reported standardized effect sizes for a
    dose / ingredient / group-size question, oriented to favour the intervention and
    shown WITHOUT pooling. One dot per record (mostly review-level pooled effects),
    coloured by geographic focus; the black line marks the median and the bar the
    interquartile range. The sign is reconstructed from the coded direction of effect,
    so the picture is a distribution to read, not a meta-analytic estimate."""
    d, s = H.oriented_effects(n)
    if s["n"] < 5:
        return _placeholder("Too few standardized effect sizes to plot a distribution")
    fig, ax = plt.subplots(figsize=(6.8, 3.1))
    rng = np.random.default_rng(n)          # deterministic jitter, seeded by RQ number
    yj = rng.uniform(-1, 1, size=len(d))
    geos = d["geo_focus"].tolist()
    ax.axvline(0, color=MUTED, lw=1, zorder=1)
    ax.scatter(d["oriented"], yj, s=20, c=[GEO_COL.get(g, MUTED) for g in geos],
               alpha=0.55, edgecolor=SURFACE, linewidth=0.4, zorder=3)
    ax.plot([s["q1"], s["q3"]], [0, 0], color=INK, lw=3, solid_capstyle="round", zorder=4)
    ax.plot([s["median"], s["median"]], [-1.3, 1.3], color=INK, lw=2, zorder=5)
    ax.text(s["median"], 1.55,
            f"median {s['median']:.2f}   (IQR {s['q1']:.2f}–{s['q3']:.2f})",
            color=INK, fontsize=9, va="bottom", ha="center")
    ax.set_ylim(-2, 2.5); ax.set_yticks([])
    ax.set_xlabel("standardized effect, oriented to favour the intervention  →")
    ax.set_title(f"Distribution of reported effect sizes (n = {s['n']}, not pooled)")
    ax.grid(axis="y", visible=False)
    present = [g for g in GEO_ORDER if g in set(geos)]
    handles = [mpl.lines.Line2D([], [], marker="o", ls="", color=GEO_COL[g],
               markersize=7, label=g) for g in present]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.22),
              frameon=False, fontsize=8.5, ncol=len(present))
    fig.tight_layout()
    return fig


def harvest_geo(n):
    """Protocol 4.5.1: the harvest of reported direction of effect stratified by
    geographic focus. Each row is a geography band; the stacked segments are the count
    of records by direction (favours intervention / null / favours control / unclear).
    Stream stratification is omitted because the stream field is degenerate."""
    rows = H.harvest_geo_counts(n)
    if not rows:
        return _placeholder("Direction of effect not recorded by geography")
    dirs = ["Favours-intervention", "Null", "Favours-control", "Unclear"]
    tot = [sum(r[1]) for r in rows]
    maxtot = max(tot)
    fig, ax = plt.subplots(figsize=(6.8, 0.8 + 0.5 * len(rows)))
    y = np.arange(len(rows))[::-1]
    left = np.zeros(len(rows))
    for j, dd in enumerate(dirs):
        vals = np.array([r[1][j] for r in rows], float)
        if vals.sum() == 0:
            continue
        ax.barh(y, vals, left=left, color=EFFDIR_COL[dd], height=0.6,
                label=dd.replace("-", " "))
        for yi, v, l in zip(y, vals, left):
            if v > 0 and v >= 0.09 * maxtot:
                ax.text(l + v / 2, yi, f"{int(v)}", va="center", ha="center",
                        fontsize=8, color=SURFACE, fontweight="bold")
        left += vals
    for yi, t in zip(y, tot):
        ax.text(t + 0.4, yi, f"n={t}", va="center", ha="left", fontsize=8.5, color=INK2)
    ax.set_yticks(y); ax.set_yticklabels([r[0] for r in rows])
    ax.set_xlim(0, maxtot * 1.16)
    ax.set_xlabel("records, by reported direction of effect")
    ax.set_title("Direction of effect by geographic focus")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.30), frameon=False,
              fontsize=8, ncol=4)
    fig.tight_layout()
    return fig


_DET_NORM = {
    "poor social support": "lack of social support", "lack of social support": "lack of social support",
    "female gender": "female sex/gender", "female sex": "female sex/gender", "gender": "female sex/gender",
    "intimate partner violence": "intimate partner violence",
    "low education": "low education", "education": "low education",
}


def determinants_bar(n, top=12):
    """Most frequently named determinants for RQ1, case-normalised and lightly merged
    (gender, social-support and education families). Counts are coverage — how often a
    determinant is named across reviews — not effect sizes."""
    ids = set(H._sub(n)["record_id"])
    _k, cnt, _num = H.field_report("driver_named", ids)
    merged = Counter()
    for k, v in cnt.items():
        kk = k.lower().strip()
        merged[_DET_NORM.get(kk, kk)] += v
    # drop the outcome-as-determinant noise ("depression") and blanks
    for junk in ("depression", "", "other"):
        merged.pop(junk, None)
    items = merged.most_common(top)
    if not items:
        return _placeholder("No named determinants recorded")
    ks = [k for k, _ in items][::-1]; vs = [v for _, v in items][::-1]
    fig, ax = plt.subplots(figsize=(6.8, 0.6 + 0.4 * len(ks)))
    y = np.arange(len(ks))
    bars = ax.barh(y, vs, color=CAT[0], height=0.66)
    ax.set_yticks(y); ax.set_yticklabels([k[0].upper() + k[1:] for k in ks], fontsize=9)
    _hbar_labels(ax, bars, vs)
    ax.set_xlim(0, max(vs) * 1.15)
    ax.set_xlabel("times named across the contributing reviews (coverage, not effect size)")
    ax.set_title("Most frequently named determinants")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    return fig


def quality_geography(n):
    """Two small panels: AMSTAR-2 quality of reviews, and geographic focus."""
    sub = H._sub(n); revs = sub[sub["unit"] == "review"]
    amc = Counter(b for b in revs["amstar2_band"] if b in H.AM_ORDER)
    geoc = Counter(sub["geo_focus"])
    fig, axes = plt.subplots(1, 2, figsize=(6.8, 2.5))
    # quality
    ax = axes[0]
    q = [b for b in H.AM_ORDER if amc.get(b)]
    if q:
        bars = ax.barh(np.arange(len(q))[::-1], [amc[b] for b in q],
                       color=[AM_COL[b] for b in q], height=0.62)
        ax.set_yticks(np.arange(len(q))[::-1]); ax.set_yticklabels(q)
        _hbar_labels(ax, bars, [amc[b] for b in q])
        ax.set_xlim(0, max(amc.values()) * 1.18)
    else:
        ax.text(0.5, 0.5, "no rated reviews", ha="center", color=MUTED, style="italic")
        ax.set_yticks([])
    ax.set_title("Review quality (AMSTAR-2)"); ax.grid(axis="y", visible=False)
    # geography
    ax = axes[1]
    g = [x for x in GEO_ORDER if geoc.get(x)]
    if g:
        bars = ax.barh(np.arange(len(g))[::-1], [geoc[x] for x in g],
                       color=[GEO_COL[x] for x in g], height=0.62)
        ax.set_yticks(np.arange(len(g))[::-1]); ax.set_yticklabels(g)
        _hbar_labels(ax, bars, [geoc[x] for x in g])
        ax.set_xlim(0, max(geoc.values()) * 1.18)
    ax.set_title("Geographic focus"); ax.grid(axis="y", visible=False)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------- cross-cutting
def volume_by_rq():
    rq = H.rqstats
    fig, ax = plt.subplots(figsize=(6.8, 5.4))
    y = np.arange(18)[::-1]
    bars = ax.barh(y, rq["records"], color=CAT[0], height=0.72)
    ax.set_yticks(y)
    ax.set_yticklabels([f"RQ{n}  {H.short(H.RQ_LABEL[n], 30)}" for n in range(1, 19)], fontsize=8.5)
    _hbar_labels(ax, bars, rq["records"].tolist())
    ax.set_xlim(0, rq["records"].max() * 1.13)
    ax.set_xlabel("contributing records")
    ax.set_title("Evidence volume by research question")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    return fig


def quality_by_rq():
    rq = H.rqstats
    fig, ax = plt.subplots(figsize=(6.8, 5.4))
    y = np.arange(18)[::-1]
    hq = rq["hq"].to_numpy(float); lq = rq["lowq"].to_numpy(float)
    tot = np.where((hq + lq) == 0, 1, hq + lq)
    hqs, lqs = 100 * hq / tot, 100 * lq / tot
    ax.barh(y, hqs, color=STATUS["good"], height=0.72, label="high / moderate")
    ax.barh(y, lqs, left=hqs, color=STATUS["critical"], height=0.72, label="low / critically low")
    ax.set_yticks(y); ax.set_yticklabels([f"RQ{n}" for n in range(1, 19)], fontsize=8.5)
    ax.set_xlim(0, 100); ax.set_xlabel("share of rated reviews (%)")
    ax.set_title("Methodological quality of reviews, by question")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.10), frameon=False, fontsize=9, ncol=2)
    fig.tight_layout()
    return fig


def geography_by_rq():
    fig, ax = plt.subplots(figsize=(6.8, 5.4))
    y = np.arange(18)[::-1]
    data = {g: [] for g in GEO_ORDER}
    for n in range(1, 19):
        sub = H._sub(n); c = Counter(sub["geo_focus"]); t = max(len(sub), 1)
        for g in GEO_ORDER:
            data[g].append(100 * c.get(g, 0) / t)
    left = np.zeros(18)
    for g in GEO_ORDER:
        ax.barh(y, data[g], left=left, color=GEO_COL[g], height=0.72, label=g)
        left += np.array(data[g])
    ax.set_yticks(y); ax.set_yticklabels([f"RQ{n}" for n in range(1, 19)], fontsize=8.5)
    ax.set_xlim(0, 100); ax.set_xlabel("share of records (%)")
    ax.set_title("Geographic focus of the evidence, by question")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="lower right", frameon=False, fontsize=8, ncol=2)
    fig.tight_layout()
    return fig


def egm_bubble():
    """Evidence-and-gap bubble: volume (x) against the *share* of reviews that are
    high/moderate quality (y), bubble size by evidence volume. The story is that most
    questions sit low on the quality axis regardless of how much evidence they have."""
    rq = H.rqstats
    hq = rq["hq"].to_numpy(float); lq = rq["lowq"].to_numpy(float)
    rated = np.where((hq + lq) == 0, 1, hq + lq)
    yshare = 100 * hq / rated
    x = rq["records"].to_numpy(float)
    sizes = 30 + 3.0 * x
    fig, ax = plt.subplots(figsize=(6.8, 5.0))
    ax.axhline(20, color=MUTED, lw=1, ls="--", zorder=1)
    ax.text(x.max(), 21, "20% high/moderate", color=MUTED, fontsize=8, ha="right", va="bottom")
    ax.scatter(x, yshare, s=sizes, c=CAT[0], alpha=0.5, edgecolor=SURFACE, linewidth=1.1, zorder=3)
    for i, nn in enumerate(range(1, 19)):
        ax.annotate(f"RQ{nn}", (x[i], yshare[i]), fontsize=7.5, color=INK,
                    ha="center", va="center", zorder=4)
    ax.set_ylim(0, max(60, yshare.max() * 1.12))
    ax.set_xlabel("contributing records (evidence volume)")
    ax.set_ylabel("high/moderate-quality reviews (% of rated)")
    ax.set_title("Evidence and gap map: volume against quality")
    fig.tight_layout()
    return fig
