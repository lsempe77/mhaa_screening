"""GT-noise ceiling test (Step 1).

Re-adjudicate the 67 pipeline-vs-GT disagreement records with two INDEPENDENT
blind adjudicators from model families OUTSIDE the v1.6 panel (panel = Claude
Sonnet 4 + GLM 5.2). Each adjudicator applies the exact ULCM TAS protocol
rubric, blind to both the ground-truth label and the pipeline decision, using
the protocol's own "uncertain -> INCLUDE" rule.

Logic:
  - Both adjudicators agree with GT           -> GT robust, genuine pipeline error
  - Both adjudicators agree with pipeline      -> strong GT-error candidate
    (against GT)                                  (GT likely wrong / protocol includes it)
  - Split                                      -> genuinely fuzzy boundary case

The fraction that are (GT-error candidate + fuzzy) bounds how much of the
sensitivity/kappa gap is attributable to GT noise + irreducible fuzziness
rather than model deficiency.
"""
import json, os, sys, concurrent.futures as cf
from pathlib import Path
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from k5_runner import call_openrouter, extract_json  # reuse existing infra

ADJUDICATORS = ["google/gemini-2.5-pro", "openai/gpt-4o"]  # both outside the panel
TEMPERATURE = 0.0  # deterministic adjudication

RUBRIC = """You are an independent senior systematic-review screener adjudicating a
Title & Abstract (TAS) eligibility decision for the StrongMinds Ultra-Low-Cost
Model (ULCM) rapid review of adult depression. Apply the protocol EXACTLY as
written below. Decide INCLUDE or EXCLUDE.

RESEARCH-QUESTION ROUTES (a record may fit more than one; no intervention is
required for RQ1 determinants or RQ18 measurement):
- Determinants (RQ1): drivers/risk factors for adult depression, esp. LMICs. No intervention required.
- Intervention effectiveness/design (RQ2-6, RQ10, RQ13-15): brief structured psychological intervention, group format, non-specialist/lay/peer/task-shared delivery.
- Dose/SSI/temporal/stepped care (RQ7-9, RQ12, RQ14): session number, intensity, timing, durability, mechanism, sequencing, stepped care. Specialist-delivered and HIC evidence MAY be eligible here.
- Spillover (RQ11): effects of an in-scope light-touch intervention on non-cases/household, depression-relevant outcome.
- Cost (RQ16): cost/resource use/cost-effectiveness for an otherwise in-scope intervention.
- Safety/referral (RQ17): safety monitoring, adverse events, escalation, referral in lay-delivered brief psychological interventions in low-resource settings.
- Measurement (RQ18): validity/reliability/cross-cultural performance of depression measures in LMICs. No intervention required.

EXCLUSION CRITERIA (apply in order; first CLEAR failure wins):
1. POPULATION: adults 18+ with depression, mixed anxiety-depression, common mental disorder, depressive symptoms, or psychological distress; perinatal women with depression eligible. Mixed adult/adolescent RETAINED unless clearly adolescent-only or clearly no adult data. RQ11 may include non-cases; RQ18 = LMIC validation populations.
2. STUDY DESIGN (screening_level=review): require a systematic review or eligible evidence synthesis. Protocols without results, editorials, commentaries, non-systematic narrative reviews FAIL.
3. INTERVENTION/TOPIC: for standard intervention routes require a brief structured psychological/psychosocial intervention plausibly group-delivered by a non-specialist/lay/peer/task-shared facilitator. Specialist delivery eligible only for RQ7,8,9,12,14. EXCLUDE pharmacotherapy-only, neurostimulation-only, purely diagnostic/screening (outside RQ18), fully digital/self-guided without human facilitator, clearly individual-only specialist treatment outside the carve-out, topics unrelated to any RQ. RQ1 and RQ18 do NOT require an intervention.
4. OUTCOME: standard intervention routes require depression symptoms/response/remission/depression-relevant clinical outcome. Functional/well-being/engagement/cost outcomes eligible when they directly answer an assigned RQ for an in-scope intervention. RQ1 = depression determinants; RQ18 = validity/reliability of a depression measure.
5. CONTEXT/GEOGRAPHY: standard scope = LMIC adult-depression evidence. HIC/UMIC eligible for RQ7,8,9,12,14. Exclude HIC-only for other routes only when the abstract makes the restriction explicit. If geography absent/mixed, RETAIN.
6. TIME/LANGUAGE: exclude if clearly non-English or published before 2000. If year/language missing, RETAIN.

GOVERNING RULE: If NO criterion CLEARLY fails, the decision is INCLUDE. Uncertain,
ambiguous, or under-reported records are INCLUDED (retained for full-text). Only
exclude when the title/abstract CLEARLY establishes a failure.

You are blind to any prior decision on this record. Judge only from the text and
the protocol. Return ONLY this JSON:
{
  "decision": "INCLUDE" | "EXCLUDE",
  "fail_criterion": "population|study_design|intervention|outcome|geography|time_language|none",
  "confidence": 0.0-1.0,
  "clarity": "clear" | "borderline",
  "reasoning": "2-3 sentences citing the specific protocol rule applied"
}"""


def load_dataset():
    errors = [json.loads(l) for l in (ROOT / "reports/errors.jsonl").read_text(encoding="utf-8").split("\n") if l.strip()]
    records = {}
    for l in (ROOT / "strongminds/data/records_510.jsonl").read_text(encoding="utf-8").split("\n"):
        if l.strip():
            r = json.loads(l)
            records[str(r["record_id"])] = r
    gt = json.loads((ROOT / "strongminds/data/gt_510.json").read_text(encoding="utf-8"))
    rows = []
    for e in errors:
        rid = str(e["record_id"])
        rec = records.get(rid, {})
        rows.append({
            "record_id": rid,
            "error_type": e["error_type"],                # FP = pipeline INCLUDE, GT EXCLUDE ; FN = pipeline EXCLUDE, GT INCLUDE
            "pipeline_decision": "INCLUDE" if e["error_type"] == "FP" else "EXCLUDE",
            "gt_label": gt.get(rid, e.get("gt_label", "")),
            "gt_binary": "INCLUDE" if "INCLUDE" in gt.get(rid, "").upper() else "EXCLUDE",
            "vote_share_include": e.get("vote_share_include"),
            "title": rec.get("title", ""),
            "abstract": rec.get("abstract", ""),
            "year": rec.get("year", ""),
            "screening_level": rec.get("screening_level", "review"),
        })
    return rows


def adjudicate_one(model, row):
    user = (
        f"screening_level: {row['screening_level']}\n"
        f"year: {row['year']}\n"
        f"TITLE: {row['title']}\n\n"
        f"ABSTRACT: {row['abstract']}\n"
    )
    try:
        raw = call_openrouter(model, RUBRIC, user, TEMPERATURE, max_tokens=2000)
        obj = extract_json(raw) or {}
        dec = str(obj.get("decision", "")).upper()
        if dec not in ("INCLUDE", "EXCLUDE"):
            dec = None
        return {
            "decision": dec,
            "fail_criterion": obj.get("fail_criterion"),
            "confidence": obj.get("confidence"),
            "clarity": obj.get("clarity"),
            "reasoning": obj.get("reasoning"),
        }
    except Exception as ex:
        return {"decision": None, "error": str(ex)[:200]}


def main():
    rows = load_dataset()
    print(f"Loaded {len(rows)} disagreement records", file=sys.stderr)
    out = []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {}
        for row in rows:
            for m in ADJUDICATORS:
                futs[ex.submit(adjudicate_one, m, row)] = (row["record_id"], m)
        results = {}
        done = 0
        for fut in cf.as_completed(futs):
            rid, m = futs[fut]
            results.setdefault(rid, {})[m] = fut.result()
            done += 1
            if done % 20 == 0:
                print(f"  {done}/{len(futs)} calls done", file=sys.stderr)
    for row in rows:
        row["adjudicators"] = results.get(row["record_id"], {})
        # classify
        adj_decs = [results[row["record_id"]].get(m, {}).get("decision") for m in ADJUDICATORS]
        pipe = row["pipeline_decision"]
        gt = row["gt_binary"]
        agree_pipe = [d == pipe for d in adj_decs if d]
        agree_gt = [d == gt for d in adj_decs if d]
        n_valid = len([d for d in adj_decs if d])
        if n_valid == 0:
            verdict = "no_adjudication"
        elif all(agree_gt) and n_valid == len(ADJUDICATORS):
            verdict = "gt_robust"            # both independents back GT -> genuine pipeline error
        elif all(agree_pipe) and n_valid == len(ADJUDICATORS):
            verdict = "gt_error_candidate"   # both independents back pipeline -> GT likely wrong
        else:
            verdict = "fuzzy_boundary"       # split
        row["verdict"] = verdict
        out.append(row)

    outpath = HERE / "gt_adjudication.jsonl"
    with outpath.open("w", encoding="utf-8") as f:
        for row in out:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # summary
    from collections import Counter
    vc = Counter(r["verdict"] for r in out)
    vc_by_err = {}
    for et in ("FP", "FN"):
        vc_by_err[et] = Counter(r["verdict"] for r in out if r["error_type"] == et)
    print("\n=== GT ADJUDICATION SUMMARY ===")
    print(f"Total disagreement records: {len(out)}")
    print(f"Overall: {dict(vc)}")
    print(f"FP (pipeline INCLUDE, GT EXCLUDE): {dict(vc_by_err['FP'])}")
    print(f"FN (pipeline EXCLUDE, GT INCLUDE): {dict(vc_by_err['FN'])}")
    n = len(out)
    gt_err = vc.get("gt_error_candidate", 0)
    fuzzy = vc.get("fuzzy_boundary", 0)
    robust = vc.get("gt_robust", 0)
    print(f"\nGT-error candidates: {gt_err}/{n} ({gt_err/n:.0%})")
    print(f"Fuzzy boundary:      {fuzzy}/{n} ({fuzzy/n:.0%})")
    print(f"GT robust (real err):{robust}/{n} ({robust/n:.0%})")
    print(f"\nWrote {outpath}")


if __name__ == "__main__":
    main()
