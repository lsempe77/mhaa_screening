"""
extract.py — Dual-model, grounded, reconciled data extraction runner.

Runs *after* full-text screening. For each included study it extracts every
field group of a declarative framework (framework.py) with TWO independent LLM
extractors, validates that every reported value carries a verbatim quote from
the source, then reconciles the two extractions into a consensus with a
human-review flag — the evidence-synthesis double-extraction workflow, automated.

Pipeline per (study x group):
    extractor A (e.g. Claude) ─┐
    extractor B (e.g. GLM)    ─┤─> quote-validate ─> reconcile ─> consensus + flags
                               ─┘
Baseline runs first; its L&MIC/SSA consensus flags gate Stream 1c.

Design choices mirrored from the screening engine (k5_runner.py):
  - Same OpenRouter client, JSON extraction, and verbatim-quote validator
    (imported, not duplicated).
  - Resumable: appends to --out and skips (record_id, group_id) already present.
  - Thread-pool across studies; groups run in order within a study (gating).

Usage:
    python pipeline/extraction/extract.py \
        --project girl_effect \
        --records projects/girl_effect/full_text/data/records_388.jsonl \
        --include-from-results projects/girl_effect/full_text/output/results_fts_glm_388.jsonl \
        --out projects/girl_effect/full_text/extraction/output/extraction_v1.jsonl \
        --models anthropic/claude-sonnet-4 z-ai/glm-5.2 \
        --reconciler-model anthropic/claude-sonnet-4 \
        --workers 6
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# extraction/ is on sys.path[0] (script dir); add pipeline/ for k5_runner reuse.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))       # pipeline/

import config
import prompts
import reconcile
from framework import Framework, Group, evaluate_gate, load_framework

# Reuse the battle-tested OpenRouter client + JSON + quote validator.
from k5_runner import dispatch, extract_json, verify_quote  # type: ignore

# Bind the quote validator too so reconciliation applies the deterministic
# verbatim-quote gate (ungrounded values can't drive consensus or become conflicts).
reconcile.bind_llm(dispatch, extract_json, verify_quote)

_write_lock = threading.Lock()


# --------------------------- include set ---------------------------

def load_include_ids(path: str) -> set[str]:
    """Read an include list: JSON array, or one id per line (# comments ok)."""
    text = Path(path).read_text(encoding="utf-8").strip()
    if text.startswith("["):
        return {str(x).strip() for x in json.loads(text)}
    ids = set()
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            ids.add(line.split(",")[0].strip())
    return ids


def includes_from_results(path: str) -> set[str]:
    """Record ids with screening_decision == INCLUDE in a screening results JSONL."""
    ids = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("screening_decision") == "INCLUDE":
                ids.add(str(r.get("record_id")))
    return ids


def load_records(path: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            out[str(r["record_id"])] = r
    return out


def load_done(out_path: str) -> set[tuple[str, str]]:
    done: set[tuple[str, str]] = set()
    p = Path(out_path)
    if not p.exists():
        return done
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                done.add((str(r["record_id"]), str(r["group_id"])))
            except Exception:
                continue
    return done


# --------------------------- extraction ---------------------------

def _empty_field(reason: str = "not_extracted") -> dict:
    return {"reported": False, "value": None, "quotes": [], "location": "",
            "confidence": "low", "notes": reason}


def _coerce_extraction(group: Group, parsed: dict | None) -> dict:
    """Ensure the model's JSON has a `fields` entry for every field id."""
    fields_in = (parsed or {}).get("fields", {}) if isinstance(parsed, dict) else {}
    fields_out: dict[str, dict] = {}
    for f in group.fields:
        fv = fields_in.get(f.id)
        if not isinstance(fv, dict):
            fields_out[f.id] = _empty_field("missing_in_model_output")
            continue
        fields_out[f.id] = {
            "reported": bool(fv.get("reported", False)),
            "value": fv.get("value"),
            "quotes": [q for q in (fv.get("quotes") or []) if isinstance(q, str) and q.strip()],
            "location": fv.get("location", "") or "",
            "confidence": fv.get("confidence", "low") or "low",
            "notes": fv.get("notes", "") or "",
        }
    return {"group_id": group.id, "fields": fields_out}


def _validate_quotes(group: Group, extraction: dict, record: dict) -> int:
    """Mark each field's quotes as verified against the source; return #unverified fields."""
    title = record.get("title", "")
    source = record.get("abstract", "")  # full PDF text
    year = str(record.get("year", ""))
    n_unverified = 0
    for f in group.fields:
        fld = extraction["fields"][f.id]
        if not fld.get("reported"):
            fld["_quote_ok"] = True
            continue
        quotes = fld.get("quotes") or []
        if not quotes:
            fld["_quote_ok"] = False
            fld["_quote_note"] = "reported but no quote"
            n_unverified += 1
            continue
        ok = all(verify_quote(q, title, source, year) for q in quotes)
        fld["_quote_ok"] = ok
        if not ok:
            n_unverified += 1
    return n_unverified


def extract_group(fw: Framework, group: Group, record: dict, model: str,
                  temperature: float, max_tokens: int, max_source_chars: int) -> dict:
    """One extractor's pass over one group. Returns a coerced+quote-validated dict."""
    system = prompts.build_extraction_system(fw, group)
    user = prompts.build_extraction_user(fw, group, record, max_source_chars)
    try:
        raw = dispatch(model, system, user, temperature, max_tokens=max_tokens)
        parsed = extract_json(raw)
        ex = _coerce_extraction(group, parsed)
        ex["_ok"] = parsed is not None
        ex["_error"] = None if parsed is not None else "json_parse_failed"
    except Exception as e:  # noqa: BLE001
        ex = _coerce_extraction(group, None)
        ex["_ok"] = False
        ex["_error"] = str(e)[:300]
    ex["_model"] = model
    ex["_n_unverified_quotes"] = _validate_quotes(group, ex, record)
    return ex


def baseline_flags_from_reconciled(fw: Framework, reconciled: dict) -> dict[str, bool]:
    """Read the L&MIC/SSA/SA consensus booleans (by semantic key) to drive gating."""
    flags: dict[str, bool] = {}
    baseline = fw.group("baseline")
    for f in baseline.fields:
        if f.key:
            fld = reconciled.get("fields", {}).get(f.id, {})
            flags[f.key] = bool(fld.get("value")) if fld.get("reported") else False
    return flags


def process_study(fw: Framework, record: dict, groups: list[Group], args,
                  out_path: str, done: set[tuple[str, str]]) -> list[str]:
    """Extract all (non-done) groups for one study, in order. Returns log lines."""
    rid = str(record["record_id"])
    logs: list[str] = []
    baseline_flags: dict[str, bool] = {}
    model_a, model_b = args.models[0], args.models[1] if len(args.models) > 1 else args.models[0]

    for group in groups:
        if (rid, group.id) in done:
            # Still need baseline flags for gating the rest of this study.
            if group.id == "baseline":
                prev = _read_line(out_path, rid, "baseline")
                if prev and prev.get("reconciled"):
                    baseline_flags = baseline_flags_from_reconciled(fw, prev["reconciled"])
            continue

        # Gate check (baseline flags known by the time we reach gated groups).
        gated_out = not evaluate_gate(group.gate, baseline_flags)
        if gated_out:
            line = {
                "record_id": rid, "group_id": group.id, "framework": fw.ref,
                "gated_out": True, "gate": group.gate, "baseline_flags": baseline_flags,
                "reason": "gate_false", "extractors": {}, "reconciled": None,
            }
            _append(out_path, line)
            logs.append(f"  {rid}/{group.id}: gated out")
            continue

        # Two independent extractions.
        ex_a = extract_group(fw, group, record, model_a, args.temperature,
                             args.extract_max_tokens, args.max_source_chars)
        ex_b = extract_group(fw, group, record, model_b, args.temperature,
                             args.extract_max_tokens, args.max_source_chars) \
            if model_b != model_a else dict(ex_a)

        # Reconcile.
        if args.no_reconcile:
            recon = reconcile.heuristic_reconcile(group, ex_a, ex_b)
        else:
            recon = reconcile.reconcile_group(
                fw, group, record, ex_a, ex_b, args.reconciler_model,
                args.temperature, args.reconcile_max_tokens)

        if group.id == "baseline":
            baseline_flags = baseline_flags_from_reconciled(fw, recon)

        n_reported = sum(1 for f in group.fields
                         if recon.get("fields", {}).get(f.id, {}).get("reported"))
        n_human = sum(1 for f in group.fields
                      if recon.get("fields", {}).get(f.id, {}).get("needs_human"))
        line = {
            "record_id": rid, "group_id": group.id, "framework": fw.ref,
            "gated_out": False,
            "extractors": {model_a: ex_a, model_b: ex_b},
            "reconciled": recon,
            "audit": {
                "n_fields": len(group.fields),
                "n_reported": n_reported,
                "n_needs_human": n_human,
                "a_unverified_quotes": ex_a.get("_n_unverified_quotes"),
                "b_unverified_quotes": ex_b.get("_n_unverified_quotes"),
                "recon_method": recon.get("_method"),
            },
        }
        _append(out_path, line)
        logs.append(f"  {rid}/{group.id}: {n_reported}/{len(group.fields)} reported, "
                    f"{n_human} need human ({recon.get('_method')})")
    return logs


def _append(out_path: str, line: dict) -> None:
    with _write_lock:
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")


def _read_line(out_path: str, rid: str, group_id: str) -> dict | None:
    p = Path(out_path)
    if not p.exists():
        return None
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if str(r.get("record_id")) == rid and str(r.get("group_id")) == group_id:
                return r
    return None


# --------------------------- CLI ---------------------------

def main():
    p = argparse.ArgumentParser(description="Dual-model reconciled data extraction.")
    p.add_argument("--project", default=config.DEFAULT_PROJECT,
                   choices=list(config.PROJECT_DIRS.keys()))
    p.add_argument("--framework", help="Override framework YAML path (else per-project default).")
    p.add_argument("--records", required=True, help="records_<n>.jsonl (full text in `abstract`).")
    p.add_argument("--out", required=True, help="Output extraction JSONL (resumable).")
    p.add_argument("--include-ids", help="File of record_ids to extract (JSON array or one/line).")
    p.add_argument("--include-from-results", help="Screening results JSONL; extract its INCLUDEs.")
    p.add_argument("--all", action="store_true", help="Extract ALL records in --records (no filter).")
    p.add_argument("--groups", nargs="*", help="Subset of group ids (default: all).")
    p.add_argument("--models", nargs="+", default=config.DEFAULT_EXTRACTORS,
                   help="Two extractor models (dual extraction).")
    p.add_argument("--reconciler-model", default=config.DEFAULT_RECONCILER)
    p.add_argument("--no-reconcile", action="store_true",
                   help="Use deterministic heuristic reconciliation (no reconciler LLM call).")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--workers", type=int, default=6, help="Parallel studies.")
    p.add_argument("--limit", type=int, help="Cap number of studies (for testing).")
    p.add_argument("--extract-max-tokens", type=int, default=config.EXTRACT_MAX_TOKENS)
    p.add_argument("--reconcile-max-tokens", type=int, default=config.RECONCILE_MAX_TOKENS)
    p.add_argument("--max-source-chars", type=int, default=config.MAX_SOURCE_CHARS)
    args = p.parse_args()

    config.load_repo_env()
    fw_path = args.framework or config.framework_path(args.project)
    fw = load_framework(fw_path)
    print(f"Framework: {fw.label} ({fw.ref}) — {len(fw.all_fields())} fields, "
          f"{len(fw.groups)} groups")

    groups = fw.groups
    if args.groups:
        groups = [fw.group(g) for g in args.groups]
        # Baseline must lead if any gated group is present (gating needs its flags).
        if any(g.gate for g in groups) and "baseline" not in [g.id for g in groups]:
            groups = [fw.group("baseline")] + groups
    # Enforce baseline-first ordering.
    groups = sorted(groups, key=lambda g: 0 if g.id == "baseline" else 1)

    records = load_records(args.records)
    print(f"Loaded {len(records)} records from {Path(args.records).name}")

    # Determine include set.
    if args.all:
        include = set(records.keys())
        src = "ALL records"
    elif args.include_ids:
        include = load_include_ids(args.include_ids)
        src = f"include-ids {Path(args.include_ids).name}"
    elif args.include_from_results:
        include = includes_from_results(args.include_from_results)
        src = f"INCLUDEs from {Path(args.include_from_results).name}"
    else:
        sys.exit("Specify one of --include-ids / --include-from-results / --all.")

    ids = [rid for rid in records if rid in include]
    missing = include - set(records)
    if missing:
        print(f"  ⚠ {len(missing)} included ids have no record in --records "
              f"(e.g. {sorted(missing)[:3]})")
    if args.limit:
        ids = ids[:args.limit]
    print(f"Extracting {len(ids)} studies ({src}); groups: {[g.id for g in groups]}")
    print(f"Extractors: {args.models}  Reconciler: "
          f"{'heuristic' if args.no_reconcile else args.reconciler_model}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    done = load_done(args.out)
    if done:
        print(f"Resuming: {len(done)} (study,group) cells already done.")

    def _work(rid: str) -> list[str]:
        return process_study(fw, records[rid], groups, args, args.out, done)

    completed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_work, rid): rid for rid in ids}
        for fut in as_completed(futs):
            rid = futs[fut]
            completed += 1
            try:
                logs = fut.result()
                head = f"[{completed}/{len(ids)}] {rid}"
                print(head + ("\n" + "\n".join(logs) if logs else "  (all cells done)"))
            except Exception as e:  # noqa: BLE001
                print(f"[{completed}/{len(ids)}] {rid}: ERROR {e}")

    print(f"\nDone. Extraction written to {args.out}")
    print(f"Next: python pipeline/extraction/export.py --project {args.project} "
          f"--extraction {args.out} --records {args.records}")


if __name__ == "__main__":
    main()
