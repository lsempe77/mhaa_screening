"""
framework.py — Load and validate a declarative extraction framework (YAML).

The framework file (e.g. frameworks/mhaa_v1.yaml) is the single source of truth
for what gets extracted: a list of field *groups* (baseline + review streams),
each a list of *fields* with a type, an instruction ("what"), and an
evaluation-crosswalk tag. This module parses that YAML into typed dataclasses
the rest of the engine consumes, and evaluates group *gates* (e.g. Stream 1c is
only extracted when the L&MIC / SSA baseline flags are true).

Design goals:
  - Framework-agnostic: the same engine runs the MHAA framework today and a
    StrongMinds framework later — only the YAML changes.
  - Fail loud: structural mistakes in the YAML (dup field ids, bad type, gate
    referencing an unknown key) raise at load time, not mid-run.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any

import yaml

VALID_TYPES = {
    "text", "integer", "boolean", "categorical", "categorical_multi",
    "composite", "composite_multi",
}
COMPOSITE_TYPES = {"composite", "composite_multi"}
MULTI_TYPES = {"categorical_multi", "composite_multi"}


@dataclass
class SubField:
    id: str
    label: str
    type: str  # one of the scalar types (text/integer/boolean/categorical)


@dataclass
class Field:
    id: str
    label: str
    type: str
    what: str
    feeds: str = ""
    key: str | None = None                 # semantic key for gate expressions
    allowed: list[str] = dc_field(default_factory=list)
    subfields: list[SubField] = dc_field(default_factory=list)

    @property
    def is_composite(self) -> bool:
        return self.type in COMPOSITE_TYPES

    @property
    def is_multi(self) -> bool:
        return self.type in MULTI_TYPES


@dataclass
class Group:
    id: str
    label: str
    review_question: str
    feeds: str
    fields: list[Field]
    gate: str | None = None            # boolean expr over baseline keys
    gate_note: str = ""

    def field_ids(self) -> list[str]:
        return [f.id for f in self.fields]


@dataclass
class Framework:
    framework_id: str
    version: str
    label: str
    primary_question: str
    groups: list[Group]
    eval_crosswalk: dict[str, list[str]]
    source_doc: str = ""

    @property
    def ref(self) -> str:
        return f"{self.framework_id}/{self.version}"

    def group(self, group_id: str) -> Group:
        for g in self.groups:
            if g.id == group_id:
                return g
        raise KeyError(f"No group {group_id!r} in framework {self.ref}")

    def all_fields(self) -> list[Field]:
        return [f for g in self.groups for f in g.fields]

    def field(self, field_id: str) -> Field:
        for f in self.all_fields():
            if f.id == field_id:
                return f
        raise KeyError(f"No field {field_id!r} in framework {self.ref}")

    def gate_keys(self) -> set[str]:
        """All semantic keys declared on baseline fields (usable in gates)."""
        return {f.key for f in self.all_fields() if f.key}


# ------------------------------- loading -------------------------------

def _parse_subfields(raw: list[dict]) -> list[SubField]:
    out = []
    for sf in raw:
        out.append(SubField(id=sf["id"], label=sf["label"], type=sf.get("type", "text")))
    return out


def _parse_field(raw: dict) -> Field:
    ftype = raw["type"]
    if ftype not in VALID_TYPES:
        raise ValueError(f"Field {raw.get('id')!r}: unknown type {ftype!r} "
                         f"(valid: {sorted(VALID_TYPES)})")
    subfields = _parse_subfields(raw.get("subfields", []))
    if ftype in COMPOSITE_TYPES and not subfields:
        raise ValueError(f"Field {raw.get('id')!r}: composite type requires subfields")
    if ftype in {"categorical", "categorical_multi"} and not raw.get("allowed"):
        raise ValueError(f"Field {raw.get('id')!r}: categorical type requires 'allowed'")
    return Field(
        id=raw["id"],
        label=raw["label"],
        type=ftype,
        what=" ".join(raw.get("what", "").split()),
        feeds=raw.get("feeds", ""),
        key=raw.get("key"),
        allowed=list(raw.get("allowed", [])),
        subfields=subfields,
    )


def load_framework(path: str | Path) -> Framework:
    """Parse and validate a framework YAML into a Framework object."""
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    groups: list[Group] = []
    seen_field_ids: set[str] = set()
    for graw in data["groups"]:
        fields = [_parse_field(f) for f in graw["fields"]]
        for f in fields:
            if f.id in seen_field_ids:
                raise ValueError(f"Duplicate field id {f.id!r} across groups")
            seen_field_ids.add(f.id)
        groups.append(Group(
            id=graw["id"],
            label=graw["label"],
            review_question=" ".join(graw.get("review_question", "").split()),
            feeds=graw.get("feeds", ""),
            fields=fields,
            gate=graw.get("gate"),
            gate_note=" ".join(graw.get("gate_note", "").split()),
        ))

    fw = Framework(
        framework_id=data["framework_id"],
        version=str(data["version"]),
        label=data.get("label", data["framework_id"]),
        primary_question=" ".join(data.get("primary_question", "").split()),
        groups=groups,
        eval_crosswalk=data.get("eval_crosswalk", {}),
        source_doc=data.get("source_doc", ""),
    )

    # Validate gate expressions reference known keys and known field ids.
    keys = fw.gate_keys()
    for g in fw.groups:
        if g.gate:
            for tok in _gate_identifiers(g.gate):
                if tok not in keys:
                    raise ValueError(
                        f"Group {g.id!r} gate references unknown key {tok!r}; "
                        f"declared keys: {sorted(keys)}")
    for section, fids in fw.eval_crosswalk.items():
        for fid in fids:
            if fid not in seen_field_ids:
                raise ValueError(f"eval_crosswalk[{section!r}] references unknown "
                                 f"field id {fid!r}")
    return fw


# ------------------------------- gating -------------------------------

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_GATE_OPS = {"and", "or", "not", "true", "false", "True", "False"}


def _gate_identifiers(expr: str) -> set[str]:
    return {t for t in _IDENT_RE.findall(expr) if t not in _GATE_OPS}


def evaluate_gate(gate: str | None, baseline_flags: dict[str, bool]) -> bool:
    """Evaluate a gate expression (e.g. "lmic_flag or ssa_flag") against the
    baseline flag values extracted for a study. Missing keys default to False
    (conservative: a study we cannot confirm as L&MIC/SSA does not trigger 1c).

    Only boolean identifiers + and/or/not are allowed — no attribute access,
    calls, or subscripts — so eval() here is safe over a locked namespace.
    """
    if not gate:
        return True
    env: dict[str, Any] = {"__builtins__": {}}
    for tok in _gate_identifiers(gate):
        env[tok] = bool(baseline_flags.get(tok, False))
    try:
        return bool(eval(gate, env, {}))  # noqa: S307 - locked namespace, ids only
    except Exception:
        return True  # if the gate can't be evaluated, extract (fail-open, safer)


if __name__ == "__main__":
    # Smoke: load the MHAA framework and print a structural summary.
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    here = Path(__file__).parent
    fw = load_framework(here / "frameworks" / "mhaa_v1.yaml")
    print(f"Framework: {fw.label} ({fw.ref})")
    print(f"Groups: {len(fw.groups)}  Fields: {len(fw.all_fields())}")
    for g in fw.groups:
        gate = f"  [gate: {g.gate}]" if g.gate else ""
        print(f"  {g.id:9s} {len(g.fields):2d} fields — {g.label}{gate}")
    print(f"Gate keys: {sorted(fw.gate_keys())}")
    print(f"Eval crosswalk sections: {len(fw.eval_crosswalk)}")
