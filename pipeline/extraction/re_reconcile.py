"""
re_reconcile.py — Re-run reconciliation over STORED extractions (no re-extraction).

extract.py keeps both raw extractions (A and B) in every cell. This script
re-derives the consensus from those stored extractions with the deterministic
verbatim-quote gate enabled — so ungrounded values (a model asserting something
its quote doesn't support) can neither drive consensus nor be surfaced as
conflicts a human must adjudicate. It mirrors the screening engine's
merge_results.py / run_critic.py pattern: reuse stored per-model objects, only
the (cheap) reconciler step re-runs; extraction is never re-paid for.

Use it to (a) apply the quote-gate to a run made before it existed, and
(b) A/B the review-queue size with vs. without the gate (--no-gate).

Usage:
    python pipeline/extraction/re_reconcile.py \
        --project girl_effect \
        --in  projects/girl_effect/full_text/extraction/output/extraction_v1.jsonl \
        --records projects/girl_effect/full_text/data/records_388.jsonl \
        --out projects/girl_effect/full_text/extraction/output/extraction_v1_gated.jsonl \
        --reconciler-model anthropic/claude-sonnet-4 --workers 8
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

import config
import reconcile
from framework import load_framework

from k5_runner import dispatch, extract_json, verify_quote  # type: ignore

reconcile.bind_llm(dispatch, extract_json, verify_quote)

_write_lock = threading.Lock()


def load_records(path: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
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


def _append(out_path: str, line: dict) -> None:
    with _write_lock:
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")


def process_cell(fw, records, cell: dict, args) -> dict:
    """Re-reconcile one stored cell. Gated-out cells pass through unchanged."""
    if cell.get("gated_out"):
        return cell
    # Skip cells already produced by a gated reconciliation (the live run applies
    # the gate), unless --force. Saves re-paying for reconciler calls on cells
    # that are already correct; only the pre-gate `llm` cells get re-reconciled.
    method = (cell.get("reconciled") or {}).get("_method", "")
    if not args.force and not args.no_gate and method.endswith("gated"):
        return cell
    rid = str(cell["record_id"])
    group = fw.group(cell["group_id"])
    record = records.get(rid, {"record_id": rid})
    extractors = cell.get("extractors") or {}
    models = list(extractors.keys())
    if len(models) < 2:
        return cell  # nothing to reconcile
    ex_a, ex_b = extractors[models[0]], extractors[models[1]]

    if args.no_reconcile:
        # Gate then heuristic-merge (deterministic, no API call).
        if not args.no_gate:
            ex_a = reconcile.quote_gate(group, ex_a, record)
            ex_b = reconcile.quote_gate(group, ex_b, record)
        recon = reconcile.heuristic_reconcile(group, ex_a, ex_b)
    else:
        recon = reconcile.reconcile_group(
            fw, group, record, ex_a, ex_b, args.reconciler_model,
            args.temperature, args.reconcile_max_tokens, gate=not args.no_gate)

    n_reported = sum(1 for f in group.fields
                     if recon.get("fields", {}).get(f.id, {}).get("reported"))
    n_human = sum(1 for f in group.fields
                  if recon.get("fields", {}).get(f.id, {}).get("needs_human"))
    new = dict(cell)
    new["reconciled"] = recon
    new["audit"] = {
        **(cell.get("audit") or {}),
        "n_reported": n_reported,
        "n_needs_human": n_human,
        "recon_method": recon.get("_method"),
        "gated": not args.no_gate,
    }
    return new


def main():
    p = argparse.ArgumentParser(description="Re-reconcile stored extractions with the quote-gate.")
    p.add_argument("--project", default=config.DEFAULT_PROJECT,
                   choices=list(config.PROJECT_DIRS.keys()))
    p.add_argument("--framework")
    p.add_argument("--in", dest="infile", required=True, help="Extraction JSONL from extract.py.")
    p.add_argument("--records", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--reconciler-model", default=config.DEFAULT_RECONCILER)
    p.add_argument("--no-gate", action="store_true", help="Disable the quote-gate (for A/B).")
    p.add_argument("--force", action="store_true",
                   help="Re-reconcile ALL cells, even ones already gated (default skips them).")
    p.add_argument("--no-reconcile", action="store_true",
                   help="Heuristic merge instead of the reconciler LLM (deterministic, no API).")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--reconcile-max-tokens", type=int, default=config.RECONCILE_MAX_TOKENS)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--limit", type=int)
    args = p.parse_args()

    config.load_repo_env()
    fw = load_framework(args.framework or config.framework_path(args.project))
    records = load_records(args.records)

    cells = []
    with open(args.infile, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cells.append(json.loads(line))
    if args.limit:
        cells = cells[:args.limit]

    done = load_done(args.out)
    todo = [c for c in cells if (str(c["record_id"]), str(c["group_id"])) not in done]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    print(f"Re-reconciling {len(todo)}/{len(cells)} cells "
          f"(gate={'off' if args.no_gate else 'ON'}, "
          f"{'heuristic' if args.no_reconcile else args.reconciler_model})")
    if done:
        print(f"  Resuming: {len(done)} cells already written.")

    n_gated_out = sum(1 for c in todo if c.get("gated_out"))
    completed = 0

    def _work(cell):
        return process_cell(fw, records, cell, args)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_work, c): c for c in todo}
        for fut in as_completed(futs):
            completed += 1
            try:
                _append(args.out, fut.result())
            except Exception as e:  # noqa: BLE001
                c = futs[fut]
                print(f"  ERROR {c.get('record_id')}/{c.get('group_id')}: {e}")
            if completed % 25 == 0:
                print(f"  {completed}/{len(todo)}")

    print(f"Done. {completed} cells → {args.out} ({n_gated_out} passed through gated-out)")
    print(f"Next: python pipeline/extraction/export.py --project {args.project} "
          f"--extraction {args.out} --records {args.records}")


if __name__ == "__main__":
    main()
