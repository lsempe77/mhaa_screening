"""Fix the remaining quote_validation_failed records by re-prompting with cleaned text.

The 10 failing records have the model quoting mojibake it copied from the PDF (e.g.,
"14a€a‹a›19" instead of "14-19"). The fix: Unicode-normalize the PDF text before
sending it to the model, so the model sees clean text and quotes clean text.

Usage:
    python pipeline/fix_quote_failures.py \
        --results projects/girl_effect/full_text/output/results_fts_glm_388.jsonl \
        --records projects/girl_effect/full_text/data/records_388.jsonl \
        --prompt projects/girl_effect/prompts/prompts-screening-mhaa-fulltext-v1.md \
        --model z-ai/glm-5.2
"""
from __future__ import annotations
import argparse, json, sys, unicodedata, re
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import k5_runner


def clean_pdf_text(text: str) -> str:
    """Aggressively clean PDF-extraction mojibake so the model quotes clean text."""
    # NFKC (not NFKD) decomposes AND recomposes, expanding ligatures (ﬁ->fi) and
    # compatibility chars, then strips any leftover combining marks.
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    # De-hyphenate words split across lines
    text = re.sub(r"-\s+", "", text)
    # Replace common mojibake artifacts (UTF-8 misinterpreted as Latin-1 then re-encoded)
    mojibake_map = {
        "\u00e2\u20ac\u2122": "'",   # â€™  -> right single quote
        "\u00e2\u20ac\u02dc": "'",   # â€˜  -> left single quote
        "\u00e2\u20ac\u0153": '"',   # â€œ  -> left double quote
        "\u00e2\u20ac\u009d": '"',   # â€\u009d -> right double quote
        "\u00e2\u20ac\u201c": "-",   # â€"  -> en dash
        "\u00e2\u20ac\u2013": "-",   # â€“  -> en dash
        "\u00e2\u20ac\u201d": "-",   # â€"  -> em dash
        "\u00e2\u20ac\u00a6": "...", # â€¦  -> ellipsis
        "\u00c3\u00a9": "\u00e9",    # Ã©  -> é
        "\u00c3\u00a8": "\u00e8",    # Ã¨  -> è
        "\u00c3\u00a0": "\u00e0",    # Ã   -> à
        "\u00c3\u00a2": "\u00e2",    # Ã¢  -> â
        "\u00c3\u00bc": "\u00fc",    # Ã¼  -> ü
        "\u00c3\u00b6": "\u00f6",    # Ã¶  -> ö
        "\u00c3\u00b1": "\u00f1",    # Ã±  -> ñ
    }
    for bad, good in mojibake_map.items():
        text = text.replace(bad, good)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def main():
    p = argparse.ArgumentParser(description="Re-screen quote-failure records with cleaned PDF text.")
    p.add_argument("--results", required=True)
    p.add_argument("--records", required=True)
    p.add_argument("--prompt", required=True)
    p.add_argument("--model", default="z-ai/glm-5.2")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--max-tokens", type=int, default=4000)
    p.add_argument("--workers", type=int, default=5)
    p.add_argument("--project", default="mhaa")
    args = p.parse_args()

    k5_runner.PROJECT_CONFIG[args.project]["max_tokens"] = args.max_tokens
    system = k5_runner.load_system_message(args.prompt)

    # Load records
    records_map: dict[str, dict] = {}
    with open(args.records, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            records_map[str(r["record_id"])] = r

    # Load results, split good / quote-failing
    good_lines: list[str] = []
    quote_fail_ids: list[str] = []
    with open(args.results, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            run = (r.get("runs") or [{}])[0]
            flags = set(run.get("_flags", []) or [])
            if "quote_validation_failed" in flags:
                quote_fail_ids.append(str(r["record_id"]))
            else:
                good_lines.append(line)

    print(f"Kept {len(good_lines)} good records; re-screening {len(quote_fail_ids)} quote-failures:")
    print("  " + ", ".join(quote_fail_ids))

    # Build cleaned versions of the failing records
    to_rescreen: list[dict] = []
    for rid in quote_fail_ids:
        rec = records_map.get(rid)
        if not rec:
            print(f"  WARNING: {rid} not in records file, skipping")
            continue
        cleaned = dict(rec)
        cleaned["abstract"] = clean_pdf_text(rec.get("abstract", ""))
        to_rescreen.append(cleaned)

    # Re-screen with cleaned text
    new_results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {
            ex.submit(k5_runner.screen_once, system, rec, args.model, args.temperature, args.project): rec
            for rec in to_rescreen
        }
        for fut in as_completed(futures):
            rec = futures[fut]
            rid = str(rec["record_id"])
            try:
                run_result = fut.result()
            except Exception as e:
                run_result = {
                    "record_id": rid,
                    "screening_code": "INCLUDE_TA",
                    "screening_decision": "INCLUDE",
                    "needs_second_opinion": True,
                    "explanation": f"API_ERROR: {e}",
                    "confidence": "Low",
                    "_flags": ["api_error"],
                    "_model": args.model,
                }
            agg = k5_runner.combine_models(
                [k5_runner.aggregate_one_model([run_result], rec, (0.4, 0.6), args.model)],
                rec, (0.4, 0.6),
            )
            agg["critic"] = {"applied": False, "adjudication": None, "model": None}
            new_results[rid] = agg
            flags = run_result.get("_flags", []) or []
            print(f"  [{rid}] {agg['screening_code']} conf={run_result.get('confidence','?')} flags={';'.join(flags) if flags else 'none'}")

    # Write back
    with open(args.results, "w", encoding="utf-8") as f:
        for line in good_lines:
            f.write(line + "\n")
        for rid in quote_fail_ids:
            if rid in new_results:
                f.write(json.dumps(new_results[rid], ensure_ascii=False) + "\n")

    still_bad = sum(
        1 for rid in quote_fail_ids
        if rid in new_results and "quote_validation_failed" in (
            set(new_results[rid].get("runs", [{}])[0].get("_flags", []) or [])
        )
    )
    print(f"\nRe-screened {len(quote_fail_ids)}; {still_bad} still have quote failures.")
    print(f"Wrote {len(good_lines) + len(new_results)} total records to {args.results}")


if __name__ == "__main__":
    main()
