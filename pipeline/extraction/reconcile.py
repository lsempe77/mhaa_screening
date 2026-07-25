"""
reconcile.py — Reconcile two independent extractions of one field group.

Given extractor A and extractor B's JSON for the same (study, group), a
reconciler model produces the consensus value per field with an agreement label
and a human-review flag. This is the LLM analogue of a third reviewer resolving
disagreements in a double-data-extraction workflow.

A deterministic fallback (`heuristic_reconcile`) is used when the reconciler call
fails or is disabled — it agrees where A and B match and flags everything else
for a human, so the pipeline degrades safely rather than dropping data.
"""
from __future__ import annotations

import json
from typing import Any

import prompts
from framework import Field, Framework, Group

# Injected by extract.py to avoid a hard import cycle / duplicate OpenRouter code.
# Signature: dispatch(model, system, user, temperature, max_tokens) -> str
_dispatch = None
_extract_json = None
_verify_quote = None


def bind_llm(dispatch_fn, extract_json_fn, verify_quote_fn=None) -> None:
    global _dispatch, _extract_json, _verify_quote
    _dispatch = dispatch_fn
    _extract_json = extract_json_fn
    if verify_quote_fn is not None:
        _verify_quote = verify_quote_fn


def quote_gate(group: Group, extraction: dict, record: dict) -> dict:
    """Deterministic grounding gate applied BEFORE reconciliation.

    An extracted value with no quote that verifies verbatim against the source is
    not evidence — it must not drive consensus or count as a conflict. This demotes
    any reported field whose quotes fail `verify_quote` to reported=false, while
    preserving the original claim under `_demoted` for auditability (a reviewer can
    still see "model X asserted Y but its quote did not verify").

    Returns a NEW extraction dict (does not mutate the input). No-op if the quote
    validator was not bound (bind_llm called without verify_quote_fn).
    """
    if _verify_quote is None or not isinstance(extraction, dict):
        return extraction
    title = record.get("title", "")
    source = record.get("abstract", "")
    year = str(record.get("year", ""))
    fields_in = extraction.get("fields", {})
    fields_out: dict[str, dict] = {}
    n_demoted = 0
    for f in group.fields:
        fld = dict(fields_in.get(f.id) or {})
        if fld.get("reported"):
            quotes = fld.get("quotes") or []
            ok = bool(quotes) and all(_verify_quote(q, title, source, year) for q in quotes)
            fld["_quote_ok"] = ok
            if not ok:
                # Demote: keep the unverified claim for audit, remove it from consensus.
                fld["_demoted"] = {
                    "value": fld.get("value"),
                    "quotes": quotes,
                    "reason": "quote_unverified" if quotes else "reported_without_quote",
                }
                fld["reported"] = False
                fld["value"] = None
                fld["quotes"] = []
                n_demoted += 1
        else:
            fld["_quote_ok"] = True
        fields_out[f.id] = fld
    out = dict(extraction)
    out["fields"] = fields_out
    out["_n_demoted"] = n_demoted
    return out


def _norm_value(v: Any) -> Any:
    """Loose normalisation for A/B value comparison in the heuristic path."""
    if isinstance(v, str):
        return " ".join(v.split()).strip().lower()
    if isinstance(v, list):
        return [_norm_value(x) for x in v]
    if isinstance(v, dict):
        return {k: _norm_value(val) for k, val in sorted(v.items())}
    return v


def _fld(extraction: dict, field_id: str) -> dict:
    return (extraction or {}).get("fields", {}).get(field_id) or {}


def heuristic_reconcile(group: Group, extraction_a: dict, extraction_b: dict) -> dict:
    """Deterministic reconciliation: agree on matches, flag everything else.

    Conservative by design — it never invents a merged value. When A and B differ
    it keeps the higher-confidence / reported side but marks needs_human=true.
    """
    out_fields: dict[str, dict] = {}
    for f in group.fields:
        a, b = _fld(extraction_a, f.id), _fld(extraction_b, f.id)
        a_rep, b_rep = bool(a.get("reported")), bool(b.get("reported"))
        a_val, b_val = a.get("value"), b.get("value")

        if not a_rep and not b_rep:
            out_fields[f.id] = _make(f, False, None, [], "", "high", "both_absent", False)
            continue
        if a_rep and b_rep and _norm_value(a_val) == _norm_value(b_val):
            quotes = list({*(a.get("quotes") or []), *(b.get("quotes") or [])})
            out_fields[f.id] = _make(f, True, a_val, quotes, a.get("location", ""),
                                     "high", "agree", False)
            continue
        # Disagreement (value mismatch or one-sided report): keep the reported side
        # (prefer A on a tie) and flag for a human.
        keep = a if a_rep else b
        out_fields[f.id] = _make(
            f, bool(keep.get("reported")), keep.get("value"),
            keep.get("quotes") or [], keep.get("location", ""),
            "low", "conflict", True,
            notes=f"A.reported={a_rep} B.reported={b_rep}; values differ")
    return {"group_id": group.id, "fields": out_fields, "_method": "heuristic"}


def _make(f: Field, reported, value, quotes, location, confidence,
          agreement, needs_human, notes="") -> dict:
    return {
        "reported": reported,
        "value": value,
        "quotes": quotes,
        "location": location,
        "confidence": confidence,
        "agreement": agreement,
        "needs_human": needs_human,
        "notes": notes,
    }


def _validate_recon(group: Group, recon: dict) -> dict | None:
    """Ensure the reconciler returned a field entry for every field id."""
    if not isinstance(recon, dict) or "fields" not in recon:
        return None
    fields = recon["fields"]
    if not isinstance(fields, dict):
        return None
    for f in group.fields:
        if f.id not in fields:
            return None  # incomplete → caller falls back to heuristic
    return recon


def reconcile_group(fw: Framework, group: Group, record: dict,
                    extraction_a: dict, extraction_b: dict,
                    reconciler_model: str, temperature: float,
                    max_tokens: int, gate: bool = True) -> dict:
    """LLM reconciliation with a deterministic fallback.

    When `gate` is True (default), each extraction is passed through the
    deterministic verbatim-quote gate first, so ungrounded values can neither
    drive consensus nor be surfaced as conflicts. Returns the consensus group
    dict: {group_id, fields:{id:{...}}, _method}.
    """
    if gate:
        extraction_a = quote_gate(group, extraction_a, record)
        extraction_b = quote_gate(group, extraction_b, record)

    if _dispatch is None:
        return heuristic_reconcile(group, extraction_a, extraction_b)

    system = prompts.build_reconciler_system()
    user = prompts.build_reconciler_user(fw, group, record, extraction_a, extraction_b)
    try:
        raw = _dispatch(reconciler_model, system, user, temperature, max_tokens=max_tokens)
        recon = _extract_json(raw)
        recon = _validate_recon(group, recon)
        if recon is None:
            fb = heuristic_reconcile(group, extraction_a, extraction_b)
            fb["_method"] = "heuristic_fallback_parse"
            return fb
        recon["group_id"] = group.id
        recon["_method"] = "llm_gated" if gate else "llm"
        recon["_reconciler_model"] = reconciler_model
        return recon
    except Exception as e:  # noqa: BLE001 - degrade to heuristic on any API/parse failure
        fb = heuristic_reconcile(group, extraction_a, extraction_b)
        fb["_method"] = "heuristic_fallback_error"
        fb["_error"] = str(e)[:300]
        return fb
