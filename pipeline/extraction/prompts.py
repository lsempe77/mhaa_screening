"""
prompts.py — Build grounded extraction + reconciliation prompts from a framework.

Two prompt families, both generated per field *group* from the Framework object
(so they stay in lock-step with the YAML — no hand-maintained prompt text):

  1. Extraction prompt  — an evidence-synthesis "judge" prompt. Given the full
     text of one included study and the field specs for one group, the model
     returns a strict JSON object of grounded field values, each with verbatim
     supporting quotes, a location, and a `reported` flag.

  2. Reconciliation prompt — given two independent extractions of the same
     group for the same study, a reconciler produces the consensus value per
     field, an agreement label, and a human-review flag. It may only choose or
     merge from the two inputs — never invent a new value.

Extraction discipline (the anti-hallucination contract):
  - Every field with reported=true MUST carry >=1 verbatim quote copied
    character-for-character from the source.
  - reported=false is a first-class answer ("the source does not state this").
    Never fabricate, infer beyond the text, or fill a plausible-but-absent value.
  - Numbers, CIs, instrument names, proportions are copied exactly.
"""
from __future__ import annotations

import json

from framework import Field, Framework, Group

# ------------------------------------------------------------------ #
# System prompt (shared across groups; the group-specific field specs
# and JSON template go in the user message).
# ------------------------------------------------------------------ #
EXTRACTION_SYSTEM = """\
You are a senior data-extraction reviewer for a rapid evidence mapping conducted \
to PRISMA / Cochrane standards. You extract structured data from the full text of \
one included study, for one thematic group of fields at a time.

FRAMEWORK CONTEXT
Review primary question: {primary_question}
Group: {group_label}
Group review question: {review_question}

NON-NEGOTIABLE EXTRACTION RULES
1. GROUNDING. For every field you mark reported=true, provide at least one
   `quote` copied VERBATIM (character-for-character) from the SOURCE TEXT below.
   Quotes must be short (<= ~40 words) and exact. If you cannot find a verbatim
   quote, the field is NOT reported.
2. "NOT REPORTED" IS AN ANSWER. If the source does not state a field, set
   reported=false, value=null, quotes=[]. Do NOT guess, infer beyond the text,
   or transfer knowledge from other studies. An honest "not reported" is correct
   and expected for most fields in most studies.
3. COPY NUMBERS EXACTLY. Effect sizes, confidence intervals, proportions, N,
   instrument names and versions are transcribed exactly as printed — never
   rounded, converted, or reconstructed.
4. STAY IN SCOPE. Extract only the fields listed for THIS group. Do not add
   fields. Do not restate the whole study.
5. LOCATION. Give a brief `location` for each reported field (e.g. "Abstract",
   "Results, p.7", "Table 2") to speed human verification.
6. CONFIDENCE. Rate your confidence that the extracted value is correct and
   complete: "high", "moderate", or "low". Use "low" when the text is ambiguous.
7. TYPES. Respect each field's type and allowed values exactly (see the field
   specs). For categorical fields use ONLY an allowed value.

OUTPUT
Return ONE JSON object and nothing else, matching the schema in the user message.
"""

RECONCILER_SYSTEM = """\
You are the reconciling reviewer in a double-data-extraction workflow. Two
reviewers (A and B) independently extracted the same group of fields from the
same study. Your job is to produce the consensus extraction.

RULES
1. For each field, compare A and B. Produce the best-supported consensus `value`
   and its `quotes`/`location`. You may adopt A, adopt B, or merge them — but you
   may NOT introduce a value that neither reviewer supports with a quote.
2. Label `agreement` per field:
   - "agree"     : A and B agree on substance (wording may differ).
   - "minor"     : same finding, small differences (units, extra detail) — you
                   reconciled it confidently.
   - "conflict"  : A and B genuinely disagree (different values, or one says
                   reported and the other not) and it needs a human.
   - "both_absent": both marked it not reported.
3. Set needs_human=true whenever agreement="conflict", or when a reported value
   rests on a quote you cannot see supported in the provided quotes.
4. Preserve verbatim quotes exactly. Never fabricate a quote.
5. Respect the field types and allowed values.

Return ONE JSON object and nothing else, matching the schema in the user message.
"""


# ------------------------------------------------------------------ #
# Field-spec rendering
# ------------------------------------------------------------------ #
def _field_spec_line(f: Field) -> str:
    """A compact human+machine readable spec for one field."""
    parts = [f"- {f.id} | {f.label} | type={f.type}"]
    if f.allowed:
        parts.append(f"allowed={f.allowed}")
    if f.subfields:
        sub = ", ".join(f"{s.id}({s.type})" for s in f.subfields)
        parts.append(f"subfields=[{sub}]")
    spec = " | ".join(parts)
    return f"{spec}\n    what: {f.what}"


def _value_template(f: Field):
    """A JSON-serialisable placeholder showing the expected `value` shape."""
    if f.type == "boolean":
        return "true|false|null"
    if f.type == "integer":
        return "<int>|null"
    if f.type == "categorical":
        return f"one of {f.allowed} | null"
    if f.type == "categorical_multi":
        return f"subset of {f.allowed} (list) | []"
    if f.type == "composite":
        return {s.id: "<value>|null" for s in f.subfields}
    if f.type == "composite_multi":
        return [{s.id: "<value>|null" for s in f.subfields}]
    return "<text>|null"


def _field_json_template(f: Field) -> dict:
    return {
        "reported": "true|false",
        "value": _value_template(f),
        "quotes": ["<verbatim quote from source>"],
        "location": "<e.g. Results p.7>",
        "confidence": "high|moderate|low",
        "notes": "<optional>",
    }


def build_extraction_user(fw: Framework, group: Group, record: dict,
                          max_source_chars: int) -> str:
    """User message: field specs + JSON template + the study's full text."""
    specs = "\n".join(_field_spec_line(f) for f in group.fields)
    template = {
        "record_id": record["record_id"],
        "group_id": group.id,
        "fields": {f.id: _field_json_template(f) for f in group.fields},
    }
    source = record.get("abstract", "") or ""
    if len(source) > max_source_chars:
        source = source[:max_source_chars]

    return (
        f"FIELDS TO EXTRACT ({group.id}):\n{specs}\n\n"
        f"RETURN EXACTLY THIS JSON SHAPE (fill every field id; use reported=false "
        f"with value=null and quotes=[] for anything the source does not state):\n"
        f"{json.dumps(template, ensure_ascii=False, indent=1)}\n\n"
        f"=== STUDY METADATA ===\n"
        f"RECORD_ID: {record['record_id']}\n"
        f"YEAR: {record.get('year', 'NA')}\n"
        f"TITLE: {record.get('title', '')}\n\n"
        f"=== SOURCE TEXT (full text of the study; quotes MUST come from here) ===\n"
        f"{source}\n"
        f"=== END SOURCE TEXT ===\n\n"
        f"Extract the {group.id} fields now. Return the JSON object only."
    )


def build_extraction_system(fw: Framework, group: Group) -> str:
    return EXTRACTION_SYSTEM.format(
        primary_question=fw.primary_question,
        group_label=group.label,
        review_question=group.review_question,
    )


# ------------------------------------------------------------------ #
# Reconciliation
# ------------------------------------------------------------------ #
def _recon_field_template(f: Field) -> dict:
    return {
        "reported": "true|false",
        "value": _value_template(f),
        "quotes": ["<verbatim>"],
        "location": "<loc>",
        "confidence": "high|moderate|low",
        "agreement": "agree|minor|conflict|both_absent",
        "needs_human": "true|false",
        "notes": "<why, if conflict>",
    }


def build_reconciler_user(fw: Framework, group: Group, record: dict,
                          extraction_a: dict, extraction_b: dict) -> str:
    """User message pairing the two extractions field-by-field for reconciliation."""
    specs = "\n".join(_field_spec_line(f) for f in group.fields)
    fields_a = (extraction_a or {}).get("fields", {})
    fields_b = (extraction_b or {}).get("fields", {})
    paired = {
        f.id: {"A": fields_a.get(f.id, "MISSING"), "B": fields_b.get(f.id, "MISSING")}
        for f in group.fields
    }
    template = {
        "record_id": record["record_id"],
        "group_id": group.id,
        "fields": {f.id: _recon_field_template(f) for f in group.fields},
    }
    return (
        f"GROUP: {group.label}\n\n"
        f"FIELD SPECS ({group.id}):\n{specs}\n\n"
        f"TWO INDEPENDENT EXTRACTIONS TO RECONCILE (per field id):\n"
        f"{json.dumps(paired, ensure_ascii=False, indent=1)}\n\n"
        f"RETURN EXACTLY THIS JSON SHAPE:\n"
        f"{json.dumps(template, ensure_ascii=False, indent=1)}\n\n"
        f"Reconcile every field id now. Return the JSON object only."
    )


def build_reconciler_system() -> str:
    return RECONCILER_SYSTEM
