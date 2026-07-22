"""criterion_screener.py — ULCM criterion-decomposed TAS screening (prototype).

Bold architecture: replaces the monolithic screener with 4 criterion-specific prompts,
deterministic objective gates, and BM25 exemplar retrieval. Eliminates the router — the
intervention criterion self-determines whether an intervention is required.

Pipeline per record:
  Stage 0 (Python, deterministic): year < 2000 → EXCLUDE (no model call)
  Stage 1 (Python, BM25): retrieve 4 label-balanced exemplars (LOO)
  Stage 2 (model, 4 criterion prompts × N models, parallel, temp 0):
    - Population, Study Design, Intervention/Topic, Outcome
    - Each returns {pass, uncertain, reasoning}
  Stage 3 (Python, combiner): walk P→S→I→O, first FAIL wins, UNCERTAIN→flag
  Stage 3b (Python, aggregate): reuse k5_runner.aggregate_one_model + combine_models
  Stage 4 (critic): adjudicate disagreement/uncertain records

Output JSONL is compatible with `k5_runner.py --calibrate`.
"""
from __future__ import annotations
import argparse, json, re, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from collections import Counter

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]        # repo root
sys.path.insert(0, str(ROOT / "pipeline"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import k5_runner as k

try:
    from rank_bm25 import BM25Okapi
except ImportError:
    sys.exit("pip install rank-bm25")


# ===================== Criterion system prompts =====================

POPULATION_SYS = """You are evaluating ONE screening criterion for the StrongMinds ULCM rapid review of brief psychological interventions for adult depression.

YOUR TASK: Evaluate ONLY the POPULATION criterion. Ignore all other criteria (study design, intervention, outcome, geography, time).

POPULATION RULE:
- PASS: adults 18+ with depression/CMD/distress; perinatal women with depression; mixed adult/adolescent samples; older adults (65+) with a depression focus (incl. dementia+depression, cognitive impairment+depression, loneliness+depression in elderly).
- PASS comorbid populations when the study targets depression (heart failure+CBT for depression, post-Ebola psychosocial support, IPV with depression outcomes).
- FAIL: children/adolescents-only under 18 with no adult component.
- MIXED-AGE RULE: when the stated age range includes adults (e.g. 12-25, 15-25, 16-30), PASS — the adult component makes it in-scope. Do NOT hard-exclude merely because the range also covers adolescents.
- UNSTATED AGE RULE: when the population is described as "adolescents" or "youth" WITHOUT a stated age range, mark UNCERTAIN (do not hard-exclude — late adolescence 18-24 is includable, and uncertainty defaults to inclusion).
- When the population is ambiguous, partly reported, or an unusual comorbid group, mark UNCERTAIN (do not hard-exclude).

SUB-POPULATION SCOPE RULE:
- Prisoners / incarcerated adults: FAIL (community-scope grounds, regardless of depression focus).
- Military personnel / veterans: FAIL (not in scope — occupational service population, not a community population).
- University / medical students: IN only if clearly 18+; if age unstated, mark UNCERTAIN.
- Refugees (including resettled in HIC): IN when the study examines drivers/risk factors for depression in refugee populations (trauma, displacement, adversity as depression determinants — RQ1). OUT when the study is about refugee health service access, general mental health without depression focus, or refugee prevalence of multiple conditions where depression is not the target.
- A sub-population is OUT when depression is merely measured alongside a different primary subject (the disease, the infant, the incarceration, the student experience); IN when depression is the target of the study.

Return ONLY JSON: {"pass": true/false, "uncertain": true/false, "reasoning": "1-2 sentences"}"""

STUDY_DESIGN_SYS = """You are evaluating ONE screening criterion for the StrongMinds ULCM rapid review.

YOUR TASK: Evaluate ONLY the STUDY DESIGN criterion. Ignore all other criteria.

STUDY DESIGN RULE (screening_level = review — require an evidence synthesis):
- PASS: systematic review, meta-analysis, Cochrane review, umbrella review, scoping review with systematic search, network meta-analysis, meta-synthesis / meta-ethnography with systematic search, integrative review with systematic methods.
- FAIL: narrative reviews (without systematic search), editorials, letters, commentaries, protocols without results, primary studies (RCTs, cohort studies, cross-sectional surveys, qualitative studies).
- UNCERTAIN: if the study design is ambiguous, not clearly stated, or the abstract doesn't specify.

Return ONLY JSON: {"pass": true/false, "uncertain": true/false, "reasoning": "1-2 sentences"}"""

INTERVENTION_SYS = """You are evaluating ONE screening criterion for the StrongMinds ULCM rapid review of brief psychological interventions for adult depression.

YOUR TASK: Evaluate ONLY the INTERVENTION/TOPIC criterion. Ignore all other criteria.

STEP 1 — Does this record require an intervention?
- If the record is about determinants/risk factors for depression (RQ1) or measurement validity/reliability of depression tools (RQ18), NO intervention is required → return {"pass": true, "uncertain": false, "reasoning": "no intervention required for RQ1/RQ18 route"}.
- If the record describes, evaluates, or reviews an intervention (or a class of interventions), proceed to STEP 2.

STEP 2 — Does the intervention fit the ULCM delivery model?
ULCM scope: BRIEF, STRUCTURED, GROUP-DELIVERED, LAY/PEER-FACILITATED psychological interventions.

FAIL signals:
- Non-psychological exposures: pharmacotherapy-only, neurostimulation-only, dietary/pharmacological/environmental exposures (trace elements, vegetarian diet, spiritual healing); yoga, art therapy, music therapy, dance therapy (unless framed as structured psychological/behavioural).
- Specialist individual/dyadic modalities: psychodynamic psychotherapy (STPP, ISTDP, dynamic psychotherapy, psychodynamic for children/adolescents), couple/marital therapy, psychoanalysis. FAIL whenever named in title/abstract, regardless of target condition.
- Substance-use interventions: target = cannabis/alcohol/opioid use, not depression.
- Resilience/adversity-promotion programs: target = resilience, not depression.
- Suicide/suicidal-ideation as primary focus: unless intervention explicitly treats depression as the mechanism.
- Fully digital/self-guided apps or internet-based interventions without human facilitation.

PASS signals:
- CBT, IPT, behavioral activation, PM+, psychoeducation, peer support, motivational interviewing, SSI, guided self-help with human support, stepped care, ACT, mindfulness, psychosocial support, psychological first aid, coping skills, stress management, cognitive rehabilitation for depression.
- Working-memory / cognitive training programs that explicitly target depressive symptoms.
- Task-sharing / capacity-building programs (non-specialist professionals trained to detect/screen/manage depression).
- Process / mechanism studies of an in-scope intervention (e.g. "in-session affect experience in CBT for depression" — the intervention is CBT, the process variable is secondary).
- Computerized CBT with human support; serious games for depression.

When the intervention is non-standard but plausibly fits the ULCM model, mark UNCERTAIN.
When the record is about healthcare access, service delivery, or policy (not an intervention), and depression is not the focus, FAIL.

Return ONLY JSON: {"pass": true/false, "uncertain": true/false, "reasoning": "1-2 sentences"}"""

OUTCOME_SYS = """You are evaluating ONE screening criterion for the StrongMinds ULCM rapid review of brief psychological interventions for adult depression.

YOUR TASK: Evaluate ONLY the OUTCOME criterion. Ignore all other criteria.

FIRST CHECK — Does the study target "depression AND anxiety" or "common mental disorders (CMD)"?
→ If yes, PASS immediately. Depression is a target even when anxiety is co-primary. Do NOT exclude a study merely because it also measures anxiety.

THEN CHECK — Does the intervention/study TARGET DEPRESSION (not merely measure it as secondary)?
- PASS: depression-targeting intervention applied to a general adult population, even if depression is co-primary/secondary alongside another condition (CBT in heart failure measuring depression, psychosocial support for stroke caregivers measuring depression).
- FAIL: intervention targets a NON-depression condition as its primary target (mindfulness for coronary artery disease, meditation for chronic neuropathy, CBT for cardiometabolic disease, ACT for chronic pain, parenting-skills programs where parenting is the target) and depression is merely one measured outcome among several.

DISEASE-SPECIFIC COHORT RULE:
- IN when the intervention EXPLICITLY TARGETS DEPRESSION (e.g. "treatment of depression in CKD", "interventions for depression after cancer"). The test is whether the intervention targets depression, NOT whether the abstract mentions the disease.
- OUT when the intervention targets the disease and depression is merely measured (e.g. "mindfulness for coronary artery disease").

DETERMINANTS ROUTE (RQ1): PASS when the record studies drivers/risk factors for depression as primary focus (social, psychological, economic, contextual risk factors; prevalence with associated factors).
- SLEEP QUALITY can be a determinant of depression — PASS when the study examines sleep as a risk factor / driver of depression.
- FAIL: IPV (intimate partner violence) as a primary subject is a SEPARATE TOPIC, not a depression determinant — even if depression is measured as one consequence among many. IPV studies are about IPV prevalence/risk factors/consequences, not about depression drivers.
- FAIL: healthcare-access inequality / service-delivery barriers are NOT social determinants of depression — they are about service utilization, not about what causes or drives depression.
- FAIL biological/neurobiological/genetic mechanism studies (neuroimaging correlates of MDD, biomarkers, genetic polymorphisms, Mendelian randomization) unless they validate a community-usable depression screen (RQ18 measurement exception).

When the depression focus is ambiguous, mark UNCERTAIN.

Return ONLY JSON: {"pass": true/false, "uncertain": true/false, "reasoning": "1-2 sentences"}"""

CRITERIA = [
    ("population",      POPULATION_SYS,      "EXCLUDE_POPULATION"),
    ("study_design",    STUDY_DESIGN_SYS,    "EXCLUDE_STUDY_DESIGN"),
    ("intervention",    INTERVENTION_SYS,    "EXCLUDE_INTERVENTION_TOPIC"),
    ("outcome",         OUTCOME_SYS,         "EXCLUDE_OUTCOME"),
]

USER_TEMPLATE = (
    "{exemplar_block}\n\n"
    "=== TARGET RECORD ===\n"
    "RECORD_ID: {record_id}\n"
    "SCREENING_LEVEL: {screening_level}\n"
    "PUBLICATION_YEAR: {year}\n"
    "TITLE: {title}\n\n"
    "ABSTRACT: {abstract}\n"
)

CRITIC_USER_TEMPLATE = (
    "RECORD_ID: {record_id}\n\n"
    "PUBLICATION_YEAR: {year}\n\n"
    "TITLE: {title}\n\n"
    "ABSTRACT: {abstract}\n\n"
    "PRIMARY_SCREENER_VERDICT:\n"
    "  screening_code: {primary_code}\n"
    "  screening_decision: {primary_decision}\n"
    "  explanation: {primary_explanation}\n\n"
    "Independently re-screen this record. Confirm or override. Return the JSON object now."
)


# ===================== BM25 retrieval (LOO) =====================

def build_index(recs: dict, gt: dict):
    ids = sorted(recs.keys())
    def toks(rid):
        t = (recs[rid].get("title", "") + " " + recs[rid].get("abstract", "")).lower()
        return [w for w in "".join(c if c.isalnum() else " " for c in t).split() if len(w) > 2]
    corpus = [toks(rid) for rid in ids]
    return BM25Okapi(corpus), ids, toks


def retrieve_loo(rid, bm25, ids, toks, gt, recs, n=4):
    scores = bm25.get_scores(toks(rid))
    ranked = sorted(range(len(ids)), key=lambda i: scores[i], reverse=True)
    top = [ids[i] for i in ranked if ids[i] != rid][:16]
    inc = [x for x in top if "INCLUDE" in gt.get(x, "").upper()]
    exc = [x for x in top if "INCLUDE" not in gt.get(x, "").upper()]
    picked, i, j = [], 0, 0
    while len(picked) < n and (i < len(inc) or j < len(exc)):
        if j < len(exc): picked.append(exc[j]); j += 1
        if len(picked) < n and i < len(inc): picked.append(inc[i]); i += 1
    return picked


def fmt_exemplars(ex_ids, gt, recs):
    out = []
    for x in ex_ids:
        r = recs[x]
        out.append(
            f"- GOLD: {gt.get(x)}\n  TITLE: {r.get('title','')[:140]}\n"
            f"  ABSTRACT: {r.get('abstract','')[:420]}"
        )
    return "EXEMPLARS (similar previously-decided records):\n" + "\n".join(out)


# ===================== Stage 0: deterministic gates =====================

def deterministic_gate(rec: dict) -> str | None:
    """Return an exclude code if an objective criterion clearly fails, else None."""
    year = rec.get("year", "")
    try:
        y = int(float(year))
        if y < 2000:
            return "EXCLUDE_TIME_LANGUAGE"
    except (ValueError, TypeError):
        pass
    return None


# ===================== Stage 2: criterion call =====================

def screen_criterion(system, rec, exemplar_block, model, temperature=0.0, context=""):
    user = USER_TEMPLATE.format(
        exemplar_block=exemplar_block,
        record_id=rec["record_id"],
        screening_level=rec.get("screening_level", "review"),
        year=rec.get("year", "NA"),
        title=rec.get("title", ""),
        abstract=rec.get("abstract", "NA"),
    )
    if context:
        user = f"CONTEXT FROM PRIOR CRITERION EVALUATION:\n{context}\n\n" + user
    try:
        raw = k.dispatch(model, system, user, temperature, max_tokens=800)
        obj = k.extract_json(raw) or {}
        return {
            "pass": bool(obj.get("pass", False)),
            "uncertain": bool(obj.get("uncertain", False)),
            "reasoning": str(obj.get("reasoning", ""))[:500],
        }
    except Exception as e:
        return {"pass": False, "uncertain": True, "reasoning": f"API_ERROR: {e}"}


# ===================== Stage 3: combiner =====================

def combine_criteria(criterion_results: dict, rec: dict, model: str) -> dict:
    """Walk P→S→I→O. First clear FAIL wins. Any UNCERTAIN → flag."""
    any_uncertain = False
    reasoning_parts = []
    for name, _, fail_code in CRITERIA:
        r = criterion_results.get(name, {"pass": True, "uncertain": True, "reasoning": "missing"})
        if r.get("uncertain"):
            any_uncertain = True
        if not r.get("pass") and not r.get("uncertain"):
            return {
                "record_id": rec["record_id"],
                "screening_code": fail_code,
                "screening_decision": "EXCLUDE",
                "explanation": f"{name}: {r.get('reasoning','')}",
                "supporting_quote": "NA",
                "needs_second_opinion": any_uncertain,
                "confidence": "High",
                "_model": model,
                "_temperature": 0.0,
            }
        reasoning_parts.append(f"{name}: {'PASS' if r.get('pass') else 'UNCERTAIN'} — {r.get('reasoning','')}")
    return {
        "record_id": rec["record_id"],
        "screening_code": "INCLUDE_TA",
        "screening_decision": "INCLUDE",
        "explanation": " | ".join(reasoning_parts),
        "supporting_quote": "NA",
        "needs_second_opinion": any_uncertain,
        "confidence": "Medium" if any_uncertain else "High",
        "_model": model,
        "_temperature": 0.0,
    }


# ===================== Per-record orchestration =====================

CRITIC_SYS = None  # loaded lazily from v1.8.1 prompt file

def load_critic_prompt():
    global CRITIC_SYS
    if CRITIC_SYS is not None:
        return CRITIC_SYS
    path = HERE.parent / "prompts" / "ulcm-orchestrator-prompts-v1.8.1.md"
    try:
        from orchestrator import _extract_section, _extract_system_message
        text = path.read_text(encoding="utf-8")
        CRITIC_SYS = _extract_system_message(_extract_section(text, "4."))
    except Exception:
        CRITIC_SYS = OUTCOME_SYS  # fallback
    return CRITIC_SYS


def process_one_record(rec, bm25_data, gt, recs_map, args):
    rid = str(rec["record_id"])

    # Stage 0: deterministic gate
    gate = deterministic_gate(rec)
    if gate:
        return {
            "record_id": rid,
            "screening_code": gate,
            "screening_decision": "EXCLUDE",
            "vote_share_include": 0.0,
            "n_runs": 0,
            "code_counts": {gate: 1},
            "in_uncertainty_band": False,
            "model_agreement": "agree",
            "needs_second_opinion": False,
            "per_model": [],
            "runs": [{"record_id": rid, "screening_code": gate, "screening_decision": "EXCLUDE",
                       "explanation": "deterministic gate: year < 2000", "supporting_quote": "NA",
                       "needs_second_opinion": False, "confidence": "High",
                       "_model": "deterministic", "_temperature": 0.0}],
            "critic": {"applied": False, "adjudication": None, "model": None},
            "_gate": gate,
        }

    # Stage 1: retrieve exemplars
    bm25, ids, toks = bm25_data
    ex_ids = retrieve_loo(rid, bm25, ids, toks, gt, recs_map, n=args.n_exemplars)
    exemplar_block = fmt_exemplars(ex_ids, gt, recs_map)

    # Stage 2: evaluate OUTCOME first (needed as context for population), then the rest in parallel
    band = tuple(args.uncertainty_band)
    runs_by_model: dict[str, list[dict]] = {m: [] for m in args.models}

    # Map criterion name -> (system_msg, fail_code)
    crit_map = {name: (sys_msg, fc) for name, sys_msg, fc in CRITERIA}

    results_by_model: dict[str, dict] = {m: {} for m in args.models}

    # Phase A: outcome criterion first (both models in parallel)
    outcome_name = "outcome"
    outcome_sys = crit_map[outcome_name][0]
    with ThreadPoolExecutor(max_workers=len(args.models)) as inner:
        out_futs = {inner.submit(screen_criterion, outcome_sys, rec, exemplar_block, m, args.temperature): m
                     for m in args.models}
        for fut in as_completed(out_futs):
            m = out_futs[fut]
            try:
                results_by_model[m][outcome_name] = fut.result()
            except Exception as e:
                results_by_model[m][outcome_name] = {"pass": False, "uncertain": True, "reasoning": f"ERROR: {e}"}

    # Build context string from outcome results (per model)
    # Phase B: remaining 3 criteria in parallel, with outcome context
    remaining = [(n, s, fc) for n, s, fc in CRITERIA if n != outcome_name]
    n_inner = len(remaining) * len(args.models)
    with ThreadPoolExecutor(max_workers=n_inner) as inner:
        futures = {}
        for model in args.models:
            out_res = results_by_model[model][outcome_name]
            ctx = (f"OUTCOME CRITERION RESULT: pass={out_res.get('pass')}, uncertain={out_res.get('uncertain')}, "
                   f"reasoning={out_res.get('reasoning','')}. "
                   f"Use this to determine whether depression is the study's target — if the outcome criterion "
                   f"confirmed depression is the target, do NOT exclude a comorbid/occupational population on "
                   f"population grounds alone (the intervention targets their depression).")
            for name, sys_msg, _ in remaining:
                fut = inner.submit(screen_criterion, sys_msg, rec, exemplar_block, model, args.temperature, context=ctx)
                futures[fut] = (model, name)

        for fut in as_completed(futures):
            model, name = futures[fut]
            try:
                results_by_model[model][name] = fut.result()
            except Exception as e:
                results_by_model[model][name] = {"pass": False, "uncertain": True, "reasoning": f"ERROR: {e}"}

    # Stage 3: combine per model
    for model in args.models:
        run = combine_criteria(results_by_model[model], rec, model)
        runs_by_model[model] = [run]

    # Stage 3b: aggregate across models
    per_model = [k.aggregate_one_model(runs_by_model[m], rec, band, m) for m in args.models]
    agg = k.combine_models(per_model, rec, band)

    # Stage 4: critic on disagreement/uncertain
    critic_block = {"applied": False, "adjudication": None, "model": None}
    if agg["needs_second_opinion"] and args.critic_model:
        critic_sys = load_critic_prompt()
        primary_code = agg["screening_code"]
        primary_decision = agg["screening_decision"]
        primary_expl = agg["runs"][0].get("explanation", "") if agg["runs"] else ""
        user = CRITIC_USER_TEMPLATE.format(
            record_id=rid, year=rec.get("year", "NA"),
            title=rec.get("title", ""), abstract=rec.get("abstract", "NA"),
            primary_code=primary_code, primary_decision=primary_decision,
            primary_explanation=primary_expl,
        )
        critic_result = None
        for _ in range(2):
            raw = k.dispatch(args.critic_model, critic_sys, user, args.temperature, max_tokens=args.max_tokens)
            obj = k.extract_json(raw)
            if not obj:
                continue
            obj = k.normalize_response(obj)
            critic_code = obj.get("screening_code", primary_code)
            critic_result = obj
            critic_result["adjudication"] = "override" if critic_code != primary_code else "confirm"
            critic_result.setdefault("overridden_code", primary_code if critic_result["adjudication"] == "override" else "NA")
            critic_result.setdefault("_model", args.critic_model)
            break
        if critic_result is None:
            critic_result = {"screening_code": primary_code, "screening_decision": primary_decision,
                             "adjudication": "confirm", "overridden_code": "NA",
                             "needs_second_opinion": True, "_flags": ["parse_error"]}
        critic_block = {"applied": True, "adjudication": critic_result.get("adjudication"),
                        "model": args.critic_model}
        if critic_result.get("adjudication") == "override":
            agg["screening_code"] = critic_result.get("screening_code", agg["screening_code"])
            agg["screening_decision"] = critic_result.get("screening_decision", agg["screening_decision"])
            critic_block["overridden_code"] = critic_result.get("overridden_code", "NA")
        agg["runs"].append({**critic_result, "_role": "critic"})
        agg["needs_second_opinion"] = bool(critic_result.get("needs_second_opinion"))

    agg["critic"] = critic_block
    agg["_criteria_results"] = {m: results_by_model[m] for m in args.models}
    return agg


# ===================== Main =====================

def run(args):
    records = [json.loads(l) for l in open(args.records, encoding="utf-8") if l.strip()]
    recs_map = {str(r["record_id"]): r for r in records}
    gt = json.loads(Path(args.gt).read_text(encoding="utf-8"))

    done = k.load_done_records(args.out)
    if done:
        print(f"Resume: {len(done)} record(s) already done, skipping.")

    pending = [r for r in records if str(r["record_id"]) not in done]
    total = len(records)

    # Build BM25 index once (all records)
    print(f"Building BM25 index over {len(records)} records...", file=sys.stderr)
    bm25_data = build_index(recs_map, gt)

    print(f"Processing {len(pending)} records | {len(args.models)} models × {len(CRITERIA)} criteria | "
          f"critic: {args.critic_model or 'none'} | workers: {args.workers}", file=sys.stderr)

    mode = "a" if done else "w"
    with open(args.out, mode, encoding="utf-8") as f_out:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            future_to_rec = {ex.submit(process_one_record, rec, bm25_data, gt, recs_map, args): rec
                             for rec in pending}
            for fut in as_completed(future_to_rec):
                rec = future_to_rec[fut]
                try:
                    agg = fut.result()
                except Exception as e:
                    agg = {
                        "record_id": rec["record_id"], "screening_code": "INCLUDE_TA",
                        "screening_decision": "INCLUDE", "vote_share_include": 1.0,
                        "n_runs": 0, "code_counts": {}, "in_uncertainty_band": False,
                        "model_agreement": "disagree", "needs_second_opinion": True,
                        "per_model": [], "runs": [],
                        "critic": {"applied": False, "adjudication": None, "model": None},
                        "_flags": ["record_error"], "_error": str(e),
                    }
                f_out.write(json.dumps(agg, ensure_ascii=False) + "\n")
                f_out.flush()
                done.add(str(rec["record_id"]))
                remaining = total - len(done)
                code = agg.get("screening_code", "?")
                vote = agg.get("vote_share_include", 0)
                agree = agg.get("model_agreement", "?")
                gate = f" [GATE:{agg.get('_gate','')}]" if agg.get("_gate") else ""
                critic_tag = f" critic={agg['critic']['adjudication']}" if agg.get("critic", {}).get("applied") else ""
                print(f"[{rec['record_id']}] code={code} vote={vote:.1f} agree={agree}{gate}{critic_tag} ({remaining} left)")

    print(f"\nWrote {total} records to {args.out}")


def main():
    p = argparse.ArgumentParser(description="ULCM criterion-decomposed TAS screener (prototype).")
    p.add_argument("--records", required=True)
    p.add_argument("--gt", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--models", nargs="+", default=["anthropic/claude-sonnet-4", "z-ai/glm-5.2"])
    p.add_argument("--critic-model", default="mistralai/mistral-large")
    p.add_argument("--temperature", type=float, default=0.0, help="temp 0 recommended (§13: +0.10 κ vs 0.3)")
    p.add_argument("--uncertainty-band", nargs=2, type=float, default=[0.4, 0.6])
    p.add_argument("--n-exemplars", type=int, default=4)
    p.add_argument("--max-tokens", type=int, default=4000)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--calibrate", type=str, default=None)
    args = p.parse_args()

    if args.calibrate:
        ns = type("NS", (), {"calibrate": args.calibrate, "gt": args.gt})()
        k.calibrate(ns)
        return

    run(args)


if __name__ == "__main__":
    main()
