"""
orchestrator.py — ULCM orchestrated TAS screening runner.

Two-stage pipeline:
  1. Router (cheap model): classifies record into scope_route(s).
  2. Route-specific screener (panel of models, k=5 sampled):
     - no_intervention screener for RQ1 determinants / RQ18 measurement
     - intervention screener for all other routes
  3. (optional) Critic adjudicates flagged records.

Output JSONL is compatible with `k5_runner.py --calibrate` so the same
calibration/ECE/kappa infrastructure is reused.

Usage:
    python orchestrator.py \
        --prompt strongminds/ulcm-orchestrator-prompts.md \
        --records strongminds/data/records_150.jsonl \
        --gt strongminds/data/gt_510.json \
        --out strongminds/data/output/results_orch_150.jsonl \
        --k 5 --temperature 0.3 \
        --router-model openai/gpt-4o-mini \
        --models anthropic/claude-sonnet-4 z-ai/glm-5.2 \
        --uncertainty-band 0.4 0.6 \
        --critic-model mistralai/mistral-large \
        --workers 8
"""
from __future__ import annotations
import argparse, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import k5_runner as k


# ------------------------- Prompt loading -------------------------

def _extract_section(text: str, section_num: str) -> str:
    """Return text from `# N.` heading to the next `# M.` heading or EOF."""
    pattern = re.compile(r"^#\s+" + re.escape(section_num) + r"(?=\s|$|`)", re.MULTILINE)
    m = pattern.search(text)
    if not m:
        raise ValueError(f"Cannot find section {section_num!r}")
    rest = text[m.start():]
    # Find the next top-level section heading after this one
    next_m = re.search(r"\n#\s+\d+\.", rest[1:])
    if next_m:
        rest = rest[: next_m.start() + 1]
    return rest


def _extract_system_message(section_text: str) -> str:
    """Extract the first fenced block after '## System message' in a section."""
    m = re.search(r"^#{1,6}\s*System\s+message\s*$", section_text, re.MULTILINE | re.IGNORECASE)
    if not m:
        raise ValueError("Cannot find '## System message' in section")
    rest = section_text[m.end():]
    fence_m = re.search(r"^```[a-zA-Z]*\s*$", rest, re.MULTILINE)
    if not fence_m:
        raise ValueError("Cannot find opening fence")
    body_start = fence_m.end()
    close_m = re.search(r"^```\s*$", rest[body_start:], re.MULTILINE)
    if not close_m:
        raise ValueError("Cannot find closing fence")
    return rest[body_start: body_start + close_m.start()].strip()


def load_orchestrator_prompts(path: str) -> dict[str, str]:
    """Load the 4 system messages from the orchestrator prompt file."""
    text = Path(path).read_text(encoding="utf-8")
    return {
        "router": _extract_system_message(_extract_section(text, "1.")),
        "no_intervention": _extract_system_message(_extract_section(text, "2.")),
        "intervention": _extract_system_message(_extract_section(text, "3.")),
        "critic": _extract_system_message(_extract_section(text, "4.")),
    }


# ------------------------- Router -------------------------

ROUTER_USER_TEMPLATE = (
    "RECORD_ID: {record_id}\n\n"
    "PUBLICATION_YEAR: {year}\n\n"
    "TITLE: {title}\n\n"
    "ABSTRACT: {abstract}\n\n"
    "Classify this record into one or more ULCM routes. Return the JSON object now."
)

def run_router(router_system: str, record: dict, model: str, temperature: float) -> dict:
    """Single router call. Returns {routes: [...], primary_route: ..., confidence: ...}."""
    user = ROUTER_USER_TEMPLATE.format(
        record_id=record["record_id"],
        year=record.get("year", "NA"),
        title=record.get("title", ""),
        abstract=record.get("abstract", "NA"),
    )
    raw = k.dispatch(model, router_system, user, temperature, max_tokens=500)
    result = k.extract_json(raw)
    if not result or not result.get("routes"):
        # Default to intervention route if router fails — the screener will exclude
        # if the record truly doesn't fit.
        return {"routes": ["intervention"], "primary_route": "intervention", "confidence": "Low", "_router_error": True}
    return result


# ------------------------- Route-specific screeners -------------------------

SCREENER_USER_TEMPLATE = (
    "RECORD_ID: {record_id}\n\n"
    "SCREENING_LEVEL: {screening_level}\n\n"
    "PUBLICATION_YEAR: {year}\n\n"
    "TITLE: {title}\n\n"
    "ABSTRACT: {abstract}\n\n"
    "ROUTER_ASSIGNED_ROUTES: {routes}\n\n"
    "Screen this record. Return the JSON object now."
)

CRITIC_USER_TEMPLATE = (
    "RECORD_ID: {record_id}\n\n"
    "PUBLICATION_YEAR: {year}\n\n"
    "TITLE: {title}\n\n"
    "ABSTRACT: {abstract}\n\n"
    "ROUTER_ASSIGNED_ROUTES: {routes}\n\n"
    "PRIMARY_SCREENER_VERDICT:\n"
    "  screening_code: {primary_code}\n"
    "  screening_decision: {primary_decision}\n"
    "  explanation: {primary_explanation}\n"
    "  supporting_quote: {primary_quote}\n\n"
    "Independently re-screen this record. Confirm or override. Return the JSON object now."
)

NO_INTERVENTION_ROUTES = {"determinants", "measurement"}

def pick_screener(routes: list[str]) -> str:
    """Decide which screener to use based on the router's route assignment.

    If ALL substantive routes are no-intervention routes (determinants, measurement),
    use the no_intervention screener. Otherwise use the intervention screener
    (intervention routes take precedence — a record tagged both determinants+intervention
    needs the intervention test).

    `not_applicable` is ignored in the subset check: a record routed to
    ["determinants", "not_applicable"] should go to the no_intervention screener,
    not the intervention screener.
    """
    route_set = set(routes) - {"not_applicable"}
    if route_set and route_set.issubset(NO_INTERVENTION_ROUTES):
        return "no_intervention"
    return "intervention"


def screen_once_orch(
    screener_system: str,
    record: dict,
    routes_str: str,
    model: str,
    temperature: float,
    max_tokens: int,
) -> dict:
    """One screening call with a route-specific screener."""
    user = SCREENER_USER_TEMPLATE.format(
        record_id=record["record_id"],
        screening_level=record.get("screening_level", "review"),
        year=record.get("year", "NA"),
        title=record.get("title", ""),
        abstract=record.get("abstract", "NA"),
        routes=routes_str,
    )
    for attempt in range(2):
        raw = k.dispatch(model, screener_system, user, temperature, max_tokens=max_tokens)
        result = k.extract_json(raw)
        if not result:
            continue
        result = k.normalize_response(result)
        q = result.get("supporting_quote", "NA")
        if q and q != "NA":
            if not k.verify_quote(q, record.get("title", ""), record.get("abstract", ""), str(record.get("year", ""))):
                if attempt == 0:
                    user = user + "\n\n" + k.REPROMPT_QUOTE
                    continue
                result["needs_second_opinion"] = True
                result.setdefault("_flags", []).append("quote_validation_failed")
        result.setdefault("_model", model)
        result.setdefault("_temperature", temperature)
        return result
    return {
        "record_id": record["record_id"],
        "screening_code": "INCLUDE_TA",
        "screening_decision": "INCLUDE",
        "explanation": "MODEL_ERROR: could not parse JSON after 2 attempts.",
        "supporting_quote": "NA",
        "needs_second_opinion": True,
        "confidence": "Low",
        "_flags": ["parse_error"],
        "_model": model,
        "_temperature": temperature,
    }


# ------------------------- Per-record orchestration -------------------------

def process_one_record(
    rec: dict,
    prompts: dict[str, str],
    args,
) -> dict:
    """Full orchestration for one record: router -> k screened runs -> aggregate -> critic."""
    # 1. Router (single call, cheap model)
    router_result = run_router(prompts["router"], rec, args.router_model, args.temperature)
    routes = router_result.get("routes", ["intervention"])
    routes_str = ", ".join(routes)
    screener_key = pick_screener(routes)
    screener_system = prompts[screener_key]

    # 2. k screened runs per model
    runs_by_model: dict[str, list[dict]] = {m: [] for m in args.models}
    inner_workers = min(args.k * len(args.models), 10)
    with ThreadPoolExecutor(max_workers=inner_workers) as inner:
        futures = []
        for model in args.models:
            for _ in range(args.k):
                futures.append((model, inner.submit(
                    screen_once_orch, screener_system, rec, routes_str, model,
                    args.temperature, args.max_tokens,
                )))
        for model, fut in futures:
            try:
                runs_by_model[model].append(fut.result())
            except Exception as e:
                runs_by_model[model].append({
                    "record_id": rec["record_id"],
                    "screening_code": "INCLUDE_TA",
                    "screening_decision": "INCLUDE",
                    "needs_second_opinion": True,
                    "explanation": f"API_ERROR: {e}",
                    "confidence": "Low",
                    "_flags": ["api_error"],
                    "_model": model,
                })

    # 3. Aggregate (reuse k5_runner's aggregation)
    band = tuple(args.uncertainty_band)
    per_model = [
        k.aggregate_one_model(runs_by_model[m], rec, band, m)
        for m in args.models
    ]
    agg = k.combine_models(per_model, rec, band)

    # 4. Critic for flagged records
    critic_block = {"applied": False, "adjudication": None, "model": None}
    if agg["needs_second_opinion"] and args.critic_model:
        primary_for_critic = {
            "screening_code": agg["screening_code"],
            "screening_decision": agg["screening_decision"],
            "explanation": (agg["runs"][0].get("explanation", "") if agg["runs"] else ""),
            "supporting_quote": (agg["runs"][0].get("supporting_quote", "NA") if agg["runs"] else "NA"),
        }
        user = CRITIC_USER_TEMPLATE.format(
            record_id=rec["record_id"],
            year=rec.get("year", "NA"),
            title=rec.get("title", ""),
            abstract=rec.get("abstract", "NA"),
            routes=routes_str,
            primary_code=primary_for_critic["screening_code"],
            primary_decision=primary_for_critic["screening_decision"],
            primary_explanation=primary_for_critic["explanation"],
            primary_quote=primary_for_critic["supporting_quote"],
        )
        for attempt in range(2):
            raw = k.dispatch(args.critic_model, prompts["critic"], user, args.temperature, max_tokens=args.max_tokens)
            critic_result = k.extract_json(raw)
            if not critic_result:
                continue
            critic_result = k.normalize_response(critic_result)
            critic_code = critic_result.get("screening_code")
            primary_code = primary_for_critic["screening_code"]
            critic_result["adjudication"] = "override" if critic_code != primary_code else "confirm"
            critic_result.setdefault("overridden_code", primary_code if critic_result["adjudication"] == "override" else "NA")
            critic_result.setdefault("_model", args.critic_model)
            break
        else:
            critic_result = {
                "screening_code": primary_for_critic["screening_code"],
                "screening_decision": primary_for_critic["screening_decision"],
                "adjudication": "confirm",
                "overridden_code": "NA",
                "needs_second_opinion": True,
                "confidence": "Low",
                "_flags": ["parse_error"],
            }
        critic_block = {
            "applied": True,
            "adjudication": critic_result.get("adjudication"),
            "model": args.critic_model,
        }
        if critic_result.get("adjudication") == "override":
            agg["screening_code"] = critic_result.get("screening_code", agg["screening_code"])
            agg["screening_decision"] = critic_result.get("screening_decision", agg["screening_decision"])
            critic_block["overridden_code"] = critic_result.get("overridden_code", "NA")
        agg["runs"].append({**critic_result, "_role": "critic"})
        agg["needs_second_opinion"] = bool(critic_result.get("needs_second_opinion"))
    agg["critic"] = critic_block
    agg["_router"] = router_result
    agg["_screener"] = screener_key
    return agg


# ------------------------- Main -------------------------

def run(args):
    prompts = load_orchestrator_prompts(args.prompt)
    records = [json.loads(l) for l in open(args.records, "r", encoding="utf-8") if l.strip()]

    done = k.load_done_records(args.out)
    if done:
        print(f"Resume: {len(done)} record(s) already in {args.out}, will skip.")

    pending = [rec for rec in records if str(rec["record_id"]) not in done]
    total = len(records)
    print(f"Processing {len(pending)} record(s) with {args.workers} parallel workers.")
    print(f"Router: {args.router_model} | Screeners: {', '.join(args.models)} | Critic: {args.critic_model or 'none'}")

    mode = "a" if done else "w"
    with open(args.out, mode, encoding="utf-8") as f_out:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            future_to_rec = {
                ex.submit(process_one_record, rec, prompts, args): rec
                for rec in pending
            }
            for fut in as_completed(future_to_rec):
                rec = future_to_rec[fut]
                try:
                    agg = fut.result()
                except Exception as e:
                    agg = {
                        "record_id": rec["record_id"],
                        "screening_code": "INCLUDE_TA",
                        "screening_decision": "INCLUDE",
                        "vote_share_include": 1.0,
                        "n_runs": 0,
                        "code_counts": {},
                        "in_uncertainty_band": False,
                        "model_agreement": "disagree",
                        "needs_second_opinion": True,
                        "per_model": [],
                        "runs": [],
                        "critic": {"applied": False, "adjudication": None, "model": None},
                        "_flags": ["record_error"],
                        "_error": str(e),
                    }
                f_out.write(json.dumps(agg, ensure_ascii=False) + "\n")
                f_out.flush()
                done.add(str(rec["record_id"]))
                remaining = total - len(done)
                screener = agg.get("_screener", "?")
                routes = ", ".join(agg.get("_router", {}).get("routes", []))
                critic_tag = f" critic={agg['critic']['adjudication']}" if agg.get("critic", {}).get("applied") else ""
                print(f"[{rec['record_id']}] {screener}({routes}) code={agg['screening_code']} vote={agg['vote_share_include']:.2f} agree={agg['model_agreement']}{critic_tag} ({remaining} left)")

    print(f"\nWrote {total} records to {args.out}")


def main():
    p = argparse.ArgumentParser(description="ULCM orchestrated TAS screening runner.")
    p.add_argument("--prompt", required=True, help="Path to ulcm-orchestrator-prompts.md")
    p.add_argument("--records", required=True, help="JSONL of records to screen")
    p.add_argument("--gt", type=str, help="Ground truth JSON (for --calibrate compatibility)")
    p.add_argument("--out", required=True, help="Output JSONL")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--temperature", type=float, default=0.3)
    p.add_argument("--router-model", default="openai/gpt-4o-mini",
                   help="Model for the router (cheap/fast).")
    p.add_argument("--models", nargs="+", default=["anthropic/claude-sonnet-4", "z-ai/glm-5.2"],
                   help="Screener model slugs.")
    p.add_argument("--critic-model", default=None)
    p.add_argument("--uncertainty-band", nargs=2, type=float, default=[0.4, 0.6])
    p.add_argument("--max-tokens", type=int, default=4000)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--calibrate", type=str, default=None,
                   help="Calibrate an existing results JSONL instead of running.")
    args = p.parse_args()

    if args.calibrate:
        if not args.gt:
            sys.exit("--gt required for --calibrate")
        # Delegate to k5_runner's calibrate (same output schema)
        import types
        ns = types.SimpleNamespace(calibrate=args.calibrate, gt=args.gt)
        k.calibrate(ns)
        return

    run(args)


if __name__ == "__main__":
    main()
