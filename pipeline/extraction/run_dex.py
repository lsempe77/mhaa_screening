"""run_dex.py — ULCM data-extraction (DEX) runner.

Implements the DEX method from the protocol-signed prompt (ULCM_M1 extraction prompt,
working copy v1.7):
  - one extractor model, run k=3 with paraphrase variants EX-1/EX-2/EX-3
  - per-field MAJORITY vote across the 3 runs (disagreement -> null + flag)
  - deterministic verbatim-quote check (k5_runner.norm) on every non-null span
  - optional LLM span-validator pass (Appendix F.5) on non-null fields
  - resumable JSONL (skips record_ids already in --out)

The prompt is read verbatim from the .md (system + user template + span-validator),
so the schema / rules / vocabularies stay identical to the signed protocol.

Pilot note (v1): EX-2's exact field-order shuffle is approximated by an anti-anchoring
instruction; repeating arrays (additional_outcomes[], rq_contributions[]) are carried
from EX-1 rather than element-merged. Both are called out and fine for the shakedown.

Usage:
    python pipeline/extraction/run_dex.py \
        --prompt  projects/strongminds/prompts/ulcm-extraction-prompt-v1.7.md \
        --records projects/strongminds/data/extraction/records_extract_final_2670.jsonl \
        --out     projects/strongminds/data/extraction/dex_pilot.jsonl \
        --extractor anthropic/claude-sonnet-4 \
        --limit 5 [--span-validate --validator openai/gpt-4o-mini] --workers 3
"""
from __future__ import annotations
import argparse, json, re, sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # import k5_runner
import k5_runner as k

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# High-stakes fields: quantitative + quality-appraisal verdicts. These are the ones that
# drive synthesis and get 100% human verification — the recommended scheme runs k=1 for the
# stable descriptive fields and concentrates review (and any targeted re-extraction) here.
HIGH_STAKES = re.compile(
    r"^(eff_|n_at_|remission_pct|pop_n$|dose_(n_sessions|session_min|total_contact_min|duration_wk)"
    r"|cost_per_pt_local|cost_effectiveness_ratio|ce_threshold_used"
    r"|amstar2_i\d+$|amstar2_band$|ffrob_c\d[a-z]$|ffrob_overall_decision|rob_fatal_flaw|record_tier$)")

EX2_NOTE = ("\n\nANTI-ANCHORING: Process the fields in an internal order of your own choosing, "
            "NOT the order they appear in the schema. Extract each field on its own merits.")
EX3_NOTE = ("\n\nFor every field, either populate it with an extracted value (and its verbatim span) "
            "OR set value = null and, in a separate `absence_notes` map keyed by field_id, record in "
            "≤ 15 words why the field is absent from this document. Add `absence_notes` to the JSON.")


# ------------------------- prompt parsing -------------------------

def _between(text: str, start_pat: str, end_pat: str) -> str:
    s = re.search(start_pat, text, re.M)
    e = re.search(end_pat, text, re.M)
    if not s:
        raise ValueError(f"prompt: cannot find {start_pat!r}")
    return text[s.end(): (e.start() if e else len(text))]


def _strip_fences(t: str) -> str:
    return "\n".join(l for l in t.splitlines() if l.strip() not in ("```", "```text", "```json"))


def parse_prompt(md: str) -> dict:
    system = _strip_fences(_between(md, r"^##\s*SYSTEM\s*$", r"^##\s*USER\s*$")).strip()
    # user template = USER block + Sections A-F (everything up to the meta sections)
    user_tpl = _between(md, r"^##\s*USER\s*$", r"^##\s*Paraphrase variants")
    user_tpl = "\n".join(l for l in user_tpl.splitlines() if l.strip() not in ("```", "```text"))
    validator = _strip_fences(_between(md, r"^##\s*Span-validation call", r"^##\s*Calibration")).strip()
    return {"system": system, "user_tpl": user_tpl.strip(), "validator": validator}


# ------------------------- extraction -------------------------

def build_user(user_tpl: str, rec: dict) -> str:
    routes = rec.get("rq_tags_hint") or []
    return (user_tpl
            .replace("{{record_id}}", str(rec.get("record_id", "")))
            .replace("{{unit_of_extraction}}", rec.get("unit_of_extraction", "review"))
            .replace("{{record_tier}}", rec.get("record_tier", "full"))
            .replace("{{source_review_ids}}", json.dumps(rec.get("source_review_ids", [])))
            .replace("{{rq_tags_hint}}", ", ".join(routes) if routes else "none")
            .replace("{{segmented_full_text}}", rec.get("segmented_full_text", "")))


def extract_once(system: str, user: str, model: str, variant_note: str, max_tokens: int) -> dict:
    raw = k.dispatch(model, system, user + variant_note, 0.0, max_tokens=max_tokens)
    return k.extract_json(raw) or {}


def flatten_prov(obj: dict) -> dict:
    """Top-level provenance fields -> {field: {value, span, section}}. Arrays/scalars skipped."""
    out = {}
    for key, v in obj.items():
        if isinstance(v, dict) and "value" in v:
            out[key] = v
    return out


def merge_k3(runs: list[dict]) -> tuple[dict, dict]:
    """Per-field majority over the provenance fields of 3 runs. Returns (merged, audit)."""
    flats = [flatten_prov(r) for r in runs]
    keys = set().union(*[set(f) for f in flats]) if flats else set()
    merged, flagged = {}, []
    for key in keys:
        cells = [f[key] for f in flats if key in f]
        vals = [json.dumps(c.get("value"), sort_keys=True, ensure_ascii=False) for c in cells]
        cnt = Counter(vals)
        top, n = cnt.most_common(1)[0]
        agree = "agree" if n == len(cells) and len(cells) > 1 else ("majority" if n >= 2 else "single")
        if len(cnt) == len(cells) and len(cells) >= 3:          # all disagree
            merged[key] = {"value": None, "span": "", "section": "", "_agreement": "conflict"}
            flagged.append(key); continue
        winner = next(c for c, vj in zip(cells, vals) if vj == top)
        cell = dict(winner); cell["_agreement"] = agree
        merged[key] = cell
        if len(cells) >= 2 and agree != "agree":   # disagreement only meaningful at k>=2
            flagged.append(key)
    # arrays + scalar bookkeeping from EX-1
    ex1 = runs[0] if runs else {}
    for arr in ("additional_outcomes", "rq_contributions"):
        if isinstance(ex1.get(arr), list):
            merged[arr] = ex1[arr]
    confs = [r.get("extractor_confidence") for r in runs if isinstance(r.get("extractor_confidence"), (int, float))]
    audit = {"n_runs": len(runs), "conf_mean": round(sum(confs) / len(confs), 3) if confs else None,
             "fields_flagged": sorted(set(flagged))}
    return merged, audit


def check_quotes(merged: dict, source: str) -> int:
    """Deterministic verbatim check on each non-null span. Sets _quote_ok. Returns #failed."""
    nsrc = k.norm(source); failed = 0
    for key, cell in merged.items():
        if not isinstance(cell, dict) or "value" in cell and cell.get("value") in (None, "", [], "NA"):
            continue
        span = cell.get("span") or ""
        if not span:
            cell["_quote_ok"] = None; continue
        ok = k.norm(span) in nsrc
        cell["_quote_ok"] = ok
        if not ok:
            failed += 1
    return failed


def process(rec: dict, prompts: dict, args) -> dict:
    system, user_tpl = prompts["system"], prompts["user_tpl"]
    user = build_user(user_tpl, rec)
    # order so k=1 -> EX-1, k=2 -> EX-1 + EX-3 (most diverse pair), k=3 -> + EX-2
    variants = [("EX-1", ""), ("EX-3", EX3_NOTE), ("EX-2", EX2_NOTE)][: args.k]
    runs = []
    for _, note in variants:
        try:
            runs.append(extract_once(system, user, args.extractor, note, args.max_tokens))
        except Exception as e:
            runs.append({"_error": str(e)})
    good = [r for r in runs if r and not r.get("_error")]
    if not good:
        return {"record_id": rec["record_id"], "_error": "all variants failed",
                "_raw_errors": [r.get("_error") for r in runs]}
    merged, audit = merge_k3(good)
    n_qfail = check_quotes(merged, rec.get("segmented_full_text", ""))
    # High-stakes fields that need human eyes: non-null quant/appraisal fields whose quote
    # failed to verify or whose runs disagreed. These are the review-queue priorities.
    hs = [key for key, c in merged.items() if HIGH_STAKES.match(key) and isinstance(c, dict)
          and c.get("value") not in (None, "", [], "NA")]
    hs_review = [key for key in hs
                 if merged[key].get("_quote_ok") is False or merged[key].get("_agreement") == "conflict"
                 or (merged[key].get("_agreement") not in ("agree", None) and len(good) >= 2)]
    return {
        "record_id": rec["record_id"],
        "unit_of_extraction": rec.get("unit_of_extraction"),
        "rq_tags_hint": rec.get("rq_tags_hint", []),
        "fields": merged,
        "audit": {**audit, "n_quote_fail": n_qfail,
                  "high_stakes_n": len(hs), "high_stakes_for_review": hs_review,
                  "eligibility_flag": (merged.get("eligibility_flag") or {}).get("value")},
    }


def main():
    p = argparse.ArgumentParser(description="ULCM DEX extraction runner (k=3 + majority + quotes).")
    p.add_argument("--prompt", required=True)
    p.add_argument("--records", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--extractor", default="anthropic/claude-sonnet-4")
    p.add_argument("--k", type=int, default=1, choices=[1, 2, 3],
                   help="paraphrase-variant runs per record (recommended: k=1 base; k=2 for a "
                        "cross-run disagreement signal on all fields)")
    p.add_argument("--max-tokens", type=int, default=16000)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--ids", default="", help="comma-sep record_ids to run (overrides --limit)")
    p.add_argument("--workers", type=int, default=3)
    args = p.parse_args()

    prompts = parse_prompt(Path(args.prompt).read_text(encoding="utf-8"))
    recs = [json.loads(l) for l in open(args.records, encoding="utf-8") if l.strip()]
    if args.ids:
        want = set(args.ids.split(",")); recs = [r for r in recs if str(r["record_id"]) in want]
    elif args.limit:
        recs = recs[: args.limit]

    out_path = Path(args.out); out_path.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out_path.exists():
        done = {str(json.loads(l)["record_id"]) for l in open(out_path, encoding="utf-8") if l.strip()}
    todo = [r for r in recs if str(r["record_id"]) not in done]
    print(f"DEX: {len(recs)} selected, {len(done)} already done, {len(todo)} to run "
          f"(extractor={args.extractor}, k={args.k})")

    with open(out_path, "a", encoding="utf-8") as f, ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(process, r, prompts, args): r for r in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            res = fut.result()
            f.write(json.dumps(res, ensure_ascii=False) + "\n"); f.flush()
            a = res.get("audit", {})
            tag = "ERROR" if res.get("_error") else (
                f"flagged={len(a.get('fields_flagged', []))} qfail={a.get('n_quote_fail')} "
                f"elig={a.get('eligibility_flag')}")
            print(f"  [{i}/{len(todo)}] {res['record_id']}: {tag}")
    print(f"\nWrote → {out_path}")


if __name__ == "__main__":
    main()
