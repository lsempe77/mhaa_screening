"""
k5_runner.py — k=5 sampled MHAA TAS screening runner + calibration.
v3: per-model aggregation, inter-model agreement, §2 critic adjudication, extended calibration.

Implements the protocol's Appendix D §337-339 sampling pipeline:

  - k=5 sampled runs per record at temperature > 0
  - Two independent model families via OpenRouter (e.g. Claude + GPT)
  - Per-model aggregation + inter-model agreement (agree/disagree)
  - Vote-share INCLUDE probability = INCLUDE_count / total_runs (pooled, for ECE)
  - Critic adjudication (§2 prompt) on flagged records:
        model disagreement | uncertainty band | low confidence | quote-fail
  - Verbatim-quote validation with re-prompt on failure
  - True ECE / Brier from vote-share vs 0/1 ground truth
  - Resumable: skips record_ids already in --out

Usage:
    export OPENROUTER_API_KEY=sk-or-...

    # 1. Ingest your dataset (Excel/CSV → records.jsonl + gt.json)
    python ingest.py --input data/ground_truth.xlsx --out-dir data

    # 2. Run k=5 screening via OpenRouter
    python k5_runner.py \
        --prompt prompts-screening-mhaa-unified-v1.4.md \
        --records data/records_462.jsonl \
        --gt data/gt_462.json \
        --out data/output/results_k5_462.jsonl \
        --k 5 \
        --temperature 0.5 \
        --models anthropic/claude-sonnet-4 openai/gpt-4o-mini \
        --uncertainty-band 0.4 0.6 \
        --critic-model mistralai/mistral-large \
        --workers 5

    # 3. Calibrate against ground truth (ECE / kappa / sensitivity + matrix + errors)
    python k5_runner.py --calibrate data/output/results_k5_462.jsonl --gt data/gt_462.json

Requires: `pip install httpx tenacity matplotlib seaborn python-dotenv`
"""
from __future__ import annotations
import argparse, json, os, re, sys, time, statistics, math, random, unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

# Force UTF-8 stdout so the κ (kappa) glyph and other unicode don't crash on cp1252 consoles.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ------------------------- Utility -------------------------

CODES = {
    # MHAA
    "EXCLUDE_LANGUAGE","EXCLUDE_YEAR","EXCLUDE_POPULATION","EXCLUDE_TOPIC",
    "EXCLUDE_EVIDENCE_TYPE","EXCLUDE_DUPLICATE","INCLUDE_TA",
    # ULCM (StrongMinds) — shares EXCLUDE_POPULATION and INCLUDE_TA with MHAA
    "EXCLUDE_STUDY_DESIGN","EXCLUDE_INTERVENTION_TOPIC","EXCLUDE_OUTCOME",
    "EXCLUDE_CONTEXT_GEOGRAPHY","EXCLUDE_TIME_LANGUAGE",
}

def norm(s: str) -> str:
    """Whitespace + typography normalisation used for verbatim-quote match."""
    s = unicodedata.normalize("NFKD", s)
    s = re.sub(r"[‘’ʼʹ′`]", "'", s)
    s = re.sub(r"[‐‑‒–—―]", "-", s)
    # PDF extraction: de-hyphenate words split across lines ("intel-\nligence" → "intelligence").
    s = re.sub(r"-\s+", "", s)
    # PDF extraction: strip combining marks (accents/tildes left by NFKD decomposition).
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower()).strip()

def parse_quote_fragments(q: str) -> list[str]:
    """Extract fragments from a quote field like: "abc"; "def with \\"escaped\\"". """
    if not q or q.strip().upper() == "NA":
        return []
    frags: list[str] = []
    i = 0
    while i < len(q):
        if q[i] == '"':
            j = i + 1
            buf: list[str] = []
            while j < len(q):
                if q[j] == '\\' and j + 1 < len(q):
                    buf.append(q[j+1]); j += 2
                elif q[j] == '"':
                    break
                else:
                    buf.append(q[j]); j += 1
            frags.append("".join(buf))
            i = j + 1
        else:
            i += 1
    if not frags:
        frags = [q.strip().strip('"')]
    return [f for f in frags if f]

# Fuzzy-match threshold: a fragment passes if its similarity to a substring of the haystack
# is at least this high. Handles minor paraphrasing / footnote digits in full-text PDFs.
QUOTE_FUZZY_RATIO = 0.90

def _fuzzy_in_haystack(frag: str, haystack: str, ratio: float = QUOTE_FUZZY_RATIO) -> bool:
    """True if `frag` is an exact substring of `haystack`, OR if any same-length window of
    `haystack` has a difflib ratio >= `ratio` (catches minor PDF-extraction edits)."""
    if frag in haystack:
        return True
    import difflib
    n = len(frag)
    if n == 0 or n > len(haystack) or n > 200:
        # Skip very long fragments (unlikely verbatim quotes; avoid O(haystack×frag) scans).
        return False
    # Slide a window over the haystack. For efficiency, only check windows starting at
    # positions where the first char of frag appears (common case: near-miss typo).
    first = frag[0]
    max_candidates = 500  # cap to bound runtime on 400k-char full-text haystacks
    checked = 0
    for i in range(len(haystack) - n + 1):
        if haystack[i] != first:
            continue
        if checked >= max_candidates:
            break
        checked += 1
        if difflib.SequenceMatcher(None, frag, haystack[i:i+n]).quick_ratio() >= ratio:
            return True
    return False

def verify_quote(quote_field: str, title: str, abstract: str, year: str = "") -> bool:
    """Verify each quote fragment appears verbatim (case-insensitive, normalized) in the
    title/abstract/year text. Falls back to a fuzzy substring match (ratio >= 0.90) to
    tolerate PDF-extraction artifacts: de-hyphenated line breaks, footnote digits attached
    to words, combining accents. The fuzzy match only loosens rejection; it never invents
    a match where no similar text exists."""
    haystack = norm(f"{title} {abstract} {year}")
    for frag in parse_quote_fragments(quote_field):
        nfrag = norm(frag)
        if not nfrag:
            continue
        if nfrag in haystack:
            continue
        if _fuzzy_in_haystack(nfrag, haystack):
            continue
        return False
    return True

# ------------------------- Prompt loader -------------------------

# Flexible markers: match headings like "## System message", "### System message",
# "# System message", possibly with trailing whitespace. The fenced block after the
# heading may use ``` or ```text or ```json — the language tag is stripped uniformly.
SYSTEM_MSG_START_RE = re.compile(r"^#{1,6}\s*System\s+message\s*$", re.MULTILINE | re.IGNORECASE)
SYSTEM_MSG_END_RE = re.compile(r"^#{1,6}\s*User\s+message\s*$", re.MULTILINE | re.IGNORECASE)

def _extract_fenced_block(text: str, start_re: re.Pattern[str]) -> str:
    """Jump past the first heading matching `start_re`, return the first fenced block.

    Works with ``` and ```text / ```json fences. Raises ValueError if the heading
    or a fenced block cannot be found.
    """
    m = start_re.search(text)
    if not m:
        raise ValueError(
            f"Cannot find heading matching {start_re.pattern!r} in prompt file"
        )
    text = text[m.end():]
    # Find the first fence opening.
    fence_m = re.search(r"^```[a-zA-Z]*\s*$", text, re.MULTILINE)
    if not fence_m:
        raise ValueError("Cannot find opening ``` fence after the System message heading")
    body_start = fence_m.end()
    close_m = re.search(r"^```\s*$", text[body_start:], re.MULTILINE)
    if not close_m:
        raise ValueError("Cannot find closing ``` fence for the System message block")
    body = text[body_start: body_start + close_m.start()]
    return body.strip()

def _section_text(text: str, section_number: str) -> str:
    """Return the slice of `text` starting at the `# N.` / `## N.` top-level section heading.

    Matches a level-1 or level-2 heading (1-2 hash marks) followed by the section
    number and a dot, e.g. `## 1.` or `# 2.`. Avoids matching deeper subsection
    headings like `### 2. Study design` (used for criterion detail) by restricting
    to 1-2 hashes.
    """
    pattern = re.compile(r"^#{1,2}\s+" + re.escape(section_number) + r"(?=\s|$)", re.MULTILINE)
    m = pattern.search(text)
    if m:
        text = text[m.start():]
    return text

def load_system_message(prompt_md_path: str) -> str:
    """Extract the fenced system message from the primary-screener section (§1)."""
    text = Path(prompt_md_path).read_text(encoding="utf-8")
    text = _section_text(text, "1.")
    return _extract_fenced_block(text, SYSTEM_MSG_START_RE)

def load_critic_message(prompt_md_path: str) -> str:
    """Extract the fenced system message from the adjudicator/§2 section."""
    text = Path(prompt_md_path).read_text(encoding="utf-8")
    text = _section_text(text, "2.")
    return _extract_fenced_block(text, SYSTEM_MSG_START_RE)

USER_TEMPLATE = (
    "RECORD_ID: {record_id}\n\n"
    "PUBLICATION_YEAR: {year}\n\n"
    "TITLE: {title}\n\n"
    "ABSTRACT: {abstract}\n\n"
    "Screen this record. Return the JSON object now."
)

CRITIC_USER_TEMPLATE = (
    "RECORD_ID: {record_id}\n\n"
    "PUBLICATION_YEAR: {year}\n\n"
    "TITLE: {title}\n\n"
    "ABSTRACT: {abstract}\n\n"
    "PRIMARY_SCREENER_VERDICT:\n"
    "  screening_code: {primary_code}\n"
    "  screening_decision: {primary_decision}\n"
    "  explanation: {primary_explanation}\n"
    "  supporting_quote: {primary_quote}\n\n"
    "Independently re-screen this record. Confirm or override. Return the JSON object now."
)

# ULCM (StrongMinds) user-message templates. Mirrors the schema in
# strongminds/ulcm-tas-screening-prompts-hierarchical.md §1/§2. The caller supplies
# screening_level per record (default "review"); other metadata fields default to NA
# since the ground-truth CSV and RIS exports don't carry them.
ULCM_USER_TEMPLATE = (
    "RECORD_ID: {record_id}\n\n"
    "SCREENING_LEVEL: {screening_level}\n\n"
    "PUBLICATION_YEAR: {year}\n\n"
    "LANGUAGE_METADATA: {language_metadata}\n\n"
    "TITLE: {title}\n\n"
    "ABSTRACT: {abstract}\n\n"
    "KEYWORDS: {keywords}\n\n"
    "SOURCE_REVIEW_ID: {source_review_id}\n\n"
    "SOURCE_REVIEW_IN_SCOPE: {source_review_in_scope}\n\n"
    "Screen this record using the ULCM hierarchical TAS rules. Return the JSON object now."
)

ULCM_CRITIC_USER_TEMPLATE = (
    "RECORD_ID: {record_id}\n\n"
    "SCREENING_LEVEL: {screening_level}\n\n"
    "PUBLICATION_YEAR: {year}\n\n"
    "LANGUAGE_METADATA: {language_metadata}\n\n"
    "TITLE: {title}\n\n"
    "ABSTRACT: {abstract}\n\n"
    "KEYWORDS: {keywords}\n\n"
    "SOURCE_REVIEW_ID: {source_review_id}\n\n"
    "SOURCE_REVIEW_IN_SCOPE: {source_review_in_scope}\n\n"
    "PRIMARY_SCREENER_VERDICT:\n"
    "  screening_code: {primary_code}\n"
    "  screening_decision: {primary_decision}\n"
    "  explanation: {primary_explanation}\n"
    "  supporting_quote: {primary_quote}\n\n"
    "Independently re-screen this record. Confirm or override. Return the JSON object now."
)

# Per-project config: user/critic templates, default screening_level, max_tokens.
# max_tokens is raised for ULCM because its response schema includes a 6-step
# hierarchical_trace with rationale + quote per step.
PROJECT_CONFIG = {
    "mhaa": {
        "user_template": USER_TEMPLATE,
        "critic_template": CRITIC_USER_TEMPLATE,
        "screening_level": None,          # MHAA prompt doesn't use screening_level
        "max_tokens": 1500,
    },
    "strongminds": {
        "user_template": ULCM_USER_TEMPLATE,
        "critic_template": ULCM_CRITIC_USER_TEMPLATE,
        "screening_level": "review",      # default for the review-level GT set
        "max_tokens": 4000,
    },
}

# ------------------------- OpenRouter client -------------------------

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

def _openrouter_headers() -> dict[str, str]:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY env var not set. Get one at https://openrouter.ai/keys")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.environ.get("HTTP_REFERER", "http://localhost/mhaa"),
        "X-Title": os.environ.get("X_TITLE", "MHAA Screening"),
    }

def call_openrouter(model: str, system: str, user: str, temperature: float, max_tokens: int = 1500) -> str:
    """Single OpenRouter chat completion. Returns raw text content."""
    import httpx
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

    @retry(
        stop=stop_after_attempt(6),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError, RuntimeError)),
        reraise=True,
    )
    def _do() -> str:
        payload = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(OPENROUTER_URL, headers=_openrouter_headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()
        content = data["choices"][0]["message"]["content"]
        if content is None:
            raise RuntimeError("API returned null content (model produced no output)")
        return content

    return _do()

def dispatch(model: str, system: str, user: str, temperature: float, max_tokens: int = 1500) -> str:
    """Route to OpenRouter (all model families go through the same endpoint)."""
    return call_openrouter(model, system, user, temperature, max_tokens=max_tokens)

# ------------------------- Screen one record -------------------------

REPROMPT_QUOTE = (
    "The supporting quote you provided does not appear verbatim in the title or abstract. "
    "Re-screen the record using only text that is present in the source. Return the JSON object now."
)

def extract_json(text: str) -> dict[str, Any] | None:
    """Extract the first {...} JSON object from a response."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(json)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try: return json.loads(m.group(0))
            except Exception: return None
    return None

def _fill_ulcm_extras(record: dict, project_cfg: dict) -> dict:
    """Build the ULCM-specific template variables not present in a plain MHAA record.

    ULCM needs screening_level, language_metadata, keywords, source_review_id,
    source_review_in_scope. Each defaults to NA / the project default unless the
    record JSONL carries it (so callers can override per record).
    """
    return {
        "screening_level": record.get("screening_level") or project_cfg["screening_level"] or "review",
        "language_metadata": record.get("language_metadata", "NA"),
        "keywords": record.get("keywords", "NA"),
        "source_review_id": record.get("source_review_id", "NA"),
        "source_review_in_scope": record.get("source_review_in_scope", "NA"),
    }

# Field-name aliases the runner accepts when a model improvises the JSON schema.
# Maps to the canonical top-level keys the aggregation/calibration code reads.
_FIELD_ALIASES = {
    "screening_code": ("final_code", "code", "screening_code", "exclusion_code", "verdict"),
    "screening_decision": ("final_decision", "decision", "screening_decision", "exclusion_decision"),
    "supporting_quote": ("quote", "supporting_quote", "evidence_quote", "exclusion_reason"),
    "explanation": ("rationale", "explanation", "summary", "reasoning", "reason"),
    "needs_second_opinion": ("needs_second_opinion", "flag_for_review", "uncertain"),
    "confidence": ("confidence", "confidence_level"),
}

# Lowercase / non-standard code strings the model may return instead of the
# canonical EXCLUDE_* / INCLUDE_TA codes. Normalized to canonical form.
_CODE_ALIASES = {
    "include": "INCLUDE_TA",
    "include_ta": "INCLUDE_TA",
    "retain": "INCLUDE_TA",
    "exclude": "EXCLUDE_INTERVENTION_TOPIC",  # generic exclude → least-specific code
    "unclear": "INCLUDE_TA",  # unclear defaults to retain per the prompt's uncertainty rule
}

def normalize_response(result: dict) -> dict:
    """Map a model's response onto the canonical schema keys the runner depends on.

    Models sometimes improvise field names (e.g. `final_code` instead of
    `screening_code`, `quote` instead of `supporting_quote`). This copies each
    canonical key from whichever alias is present, leaving the original keys intact
    (so the full hierarchical_trace / scope_route / rq_tags etc. are preserved).

    Also handles two common schema conflation bugs:
      1. Model puts a CODE (e.g. "EXCLUDE_STUDY_DESIGN") into `screening_decision`
         instead of the binary "EXCLUDE". Detect this and route the value to
         `screening_code`, deriving the binary from it.
      2. Model omits `screening_decision` entirely. Derive it from `screening_code`.
    """
    if not isinstance(result, dict):
        return result
    for canonical, aliases in _FIELD_ALIASES.items():
        if canonical in result:
            continue
        for alias in aliases:
            if alias in result and result[alias] not in (None, ""):
                result[canonical] = result[alias]
                break

    # Bug 1: model put a code into screening_decision. A valid binary decision is
    # only "INCLUDE" or "EXCLUDE"; anything else is a misrouted code.
    decision = result.get("screening_decision")
    if decision and decision not in ("INCLUDE", "EXCLUDE"):
        # It's likely a code (e.g. "EXCLUDE_STUDY_DESIGN"). Promote it to screening_code
        # if screening_code is missing, then re-derive the binary.
        if not result.get("screening_code"):
            result["screening_code"] = decision
        # If we already have a screening_code, keep it; just fix the decision below.

    # Derive screening_code from the hierarchical_trace if still missing. The decisive
    # code is the first FAIL criterion's code_if_fail (or INCLUDE_TA if all PASS).
    code = result.get("screening_code")
    if not code:
        trace = result.get("hierarchical_trace")
        if isinstance(trace, dict):
            for key in sorted(trace.keys()):
                crit = trace.get(key)
                if not isinstance(crit, dict):
                    continue
                verdict = crit.get("verdict", "")
                if verdict == "FAIL":
                    fail_code = crit.get("code_if_fail") or crit.get("failed_code")
                    if fail_code and fail_code != "NA":
                        code = fail_code
                        result["screening_code"] = code
                        break
            if not code:
                # No FAIL found in trace → INCLUDE_TA (all PASS or NOT_EVALUATED).
                result["screening_code"] = "INCLUDE_TA"
                code = "INCLUDE_TA"

    # Derive the binary decision from the code.
    if code and result.get("screening_decision") not in ("INCLUDE", "EXCLUDE"):
        result["screening_decision"] = "INCLUDE" if code == "INCLUDE_TA" else "EXCLUDE"

    # Normalize non-canonical code strings (e.g. "include", "exclude", "retain").
    code = result.get("screening_code")
    if code and isinstance(code, str):
        normalized = _CODE_ALIASES.get(code.lower().strip())
        if normalized:
            result["screening_code"] = normalized
            result["screening_decision"] = "INCLUDE" if normalized == "INCLUDE_TA" else "EXCLUDE"

    # Pull the decisive quote from the hierarchical_trace if no top-level quote was given.
    if not result.get("supporting_quote") or result.get("supporting_quote") == "NA":
        trace = result.get("hierarchical_trace")
        if isinstance(trace, dict):
            decisive_quote = None
            for key in sorted(trace.keys()):
                crit = trace.get(key)
                if not isinstance(crit, dict):
                    continue
                verdict = crit.get("verdict", "")
                quote = crit.get("supporting_quote") or crit.get("quote")
                if verdict == "FAIL" and quote and quote != "NA":
                    decisive_quote = quote
                    break
                if verdict == "PASS" and quote and quote != "NA" and not decisive_quote:
                    decisive_quote = quote
            if decisive_quote:
                result["supporting_quote"] = decisive_quote
    return result

def screen_once(system: str, record: dict, model: str, temperature: float, project: str = "mhaa") -> dict:
    project_cfg = PROJECT_CONFIG[project]
    template = project_cfg["user_template"]
    fmt_kwargs = {
        "record_id": record["record_id"],
        "year": record.get("year", "NA"),
        "title": record.get("title", ""),
        "abstract": record.get("abstract", "NA"),
    }
    if project == "strongminds":
        fmt_kwargs.update(_fill_ulcm_extras(record, project_cfg))
    user = template.format(**fmt_kwargs)
    for attempt in range(2):
        raw = dispatch(model, system, user, temperature, max_tokens=project_cfg["max_tokens"])
        result = extract_json(raw)
        if not result: continue
        result = normalize_response(result)
        # Verbatim-quote check
        q = result.get("supporting_quote", "NA")
        if q and q != "NA":
            if not verify_quote(q, record.get("title",""), record.get("abstract",""), str(record.get("year",""))):
                if attempt == 0:
                    user = user + "\n\n" + REPROMPT_QUOTE
                    continue
                # 2 failures → force needs_second_opinion
                result["needs_second_opinion"] = True
                result.setdefault("_flags", []).append("quote_validation_failed")
        result.setdefault("_model", model)
        result.setdefault("_temperature", temperature)
        return result
    # Both attempts failed to produce valid JSON — return an abstain record
    return {
        "record_id": record["record_id"],
        "screening_code": "INCLUDE_TA",
        "screening_decision": "INCLUDE",
        "ssa_lmic_marker": "NA",
        "explanation": "MODEL_ERROR: could not parse JSON after 2 attempts.",
        "supporting_quote": "NA",
        "needs_second_opinion": True,
        "confidence": "Low",
        "_flags": ["parse_error"],
        "_model": model,
        "_temperature": temperature,
    }

def screen_critic(
    critic_system: str,
    record: dict,
    primary_result: dict,
    model: str,
    temperature: float,
    project: str = "mhaa",
) -> dict:
    """Adjudicate a flagged record with the §2 critic prompt.

    `primary_result` is the combined primary-screening verdict the critic reviews.
    Reuses the verbatim-quote validation; on 2 failures keeps the critic's code but
    forces needs_second_opinion (escalates to human).
    """
    project_cfg = PROJECT_CONFIG[project]
    template = project_cfg["critic_template"]
    fmt_kwargs = {
        "record_id": record["record_id"],
        "year": record.get("year", "NA"),
        "title": record.get("title", ""),
        "abstract": record.get("abstract", "NA"),
        "primary_code": primary_result.get("screening_code", "NA"),
        "primary_decision": primary_result.get("screening_decision", "NA"),
        "primary_explanation": primary_result.get("explanation", "NA"),
        "primary_quote": primary_result.get("supporting_quote", "NA"),
    }
    if project == "strongminds":
        fmt_kwargs.update(_fill_ulcm_extras(record, project_cfg))
    user = template.format(**fmt_kwargs)
    for attempt in range(2):
        raw = dispatch(model, critic_system, user, temperature, max_tokens=project_cfg["max_tokens"])
        result = extract_json(raw)
        if not result: continue
        result = normalize_response(result)
        q = result.get("supporting_quote", "NA")
        if q and q != "NA":
            if not verify_quote(q, record.get("title",""), record.get("abstract",""), str(record.get("year",""))):
                if attempt == 0:
                    user = user + "\n\n" + REPROMPT_QUOTE
                    continue
                result["needs_second_opinion"] = True
                result.setdefault("_flags", []).append("quote_validation_failed")
        # Normalize the adjudication field. MHAA returns {adjudication: "confirm"|"override"};
        # ULCM returns an {adjudication: {...}} object. Collapse both to the canonical
        # top-level "confirm"/"override" string the runner stores.
        adj = result.get("adjudication")
        if isinstance(adj, dict):
            # ULCM shape: {prior_disagreement_summary, hierarchy_or_carveout_issue,
            #             final_basis, human_review_priority}. An override is signalled
            # by the critic's screening_code differing from the primary's.
            primary_code = primary_result.get("screening_code")
            critic_code = result.get("screening_code", primary_code)
            adjudication_str = "override" if critic_code != primary_code else "confirm"
            result["adjudication"] = adjudication_str
            result.setdefault("overridden_code", primary_code if adjudication_str == "override" else "NA")
        else:
            result.setdefault("adjudication", "confirm")
            result.setdefault("overridden_code", "NA")
        result.setdefault("_model", model)
        result.setdefault("_temperature", temperature)
        return result
    return {
        "record_id": record["record_id"],
        "screening_code": primary_result.get("screening_code", "INCLUDE_TA"),
        "screening_decision": primary_result.get("screening_decision", "INCLUDE"),
        "ssa_lmic_marker": "NA",
        "explanation": "MODEL_ERROR: critic could not parse JSON after 2 attempts; primary verdict retained.",
        "supporting_quote": "NA",
        "adjudication": "confirm",
        "overridden_code": "NA",
        "needs_second_opinion": True,
        "confidence": "Low",
        "_flags": ["parse_error"],
        "_model": model,
        "_temperature": temperature,
    }

def aggregate_one_model(runs: list[dict], record: dict, uncertainty_band: tuple[float, float], model: str) -> dict:
    """Aggregate the k runs of a SINGLE model family for one record."""
    n = len(runs)
    include_count = sum(1 for r in runs if r.get("screening_code") == "INCLUDE_TA")
    vote_share_include = include_count / n if n else 0.0

    code_counts: dict[str, int] = {}
    for r in runs:
        c = r.get("screening_code", "INCLUDE_TA")
        code_counts[c] = code_counts.get(c, 0) + 1
    majority_code = max(code_counts.items(), key=lambda kv: kv[1])[0]

    max_share = max(code_counts.values()) / n
    if max_share == 1.0: agg_conf = "High"
    elif max_share >= 0.6: agg_conf = "Medium"
    else: agg_conf = "Low"

    lo, hi = uncertainty_band
    in_uncertainty_band = lo <= vote_share_include <= hi

    return {
        "record_id": record["record_id"],
        "model": model,
        "screening_code": majority_code,
        "screening_decision": "INCLUDE" if majority_code == "INCLUDE_TA" else "EXCLUDE",
        "vote_share_include": vote_share_include,
        "n_runs": n,
        "code_counts": code_counts,
        "aggregated_confidence": agg_conf,
        "in_uncertainty_band": in_uncertainty_band,
        "needs_second_opinion": in_uncertainty_band or any(r.get("needs_second_opinion") for r in runs),
        "runs": runs,
    }

def combine_models(per_model: list[dict], record: dict, uncertainty_band: tuple[float, float]) -> dict:
    """Combine per-model aggregations into the final record verdict.

    - model_agreement: whether all models agree on the binary INCLUDE/EXCLUDE decision.
    - Pooled vote_share_include across all runs (backward-compatible with calibration ECE).
    - Triggers needs_second_opinion on: disagreement, any model in uncertainty band,
      or any run-level needs_second_opinion flag.
    """
    all_runs: list[dict] = []
    for pm in per_model:
        all_runs.extend(pm.get("runs", []))

    decisions = {pm["screening_decision"] for pm in per_model}
    agreement = "agree" if len(decisions) <= 1 else "disagree"

    n = len(all_runs)
    include_count = sum(1 for r in all_runs if r.get("screening_code") == "INCLUDE_TA")
    pooled_vote_share = include_count / n if n else 0.0

    lo, hi = uncertainty_band
    in_band = lo <= pooled_vote_share <= hi

    needs_second = (
        agreement == "disagree"
        or in_band
        or any(pm.get("needs_second_opinion") for pm in per_model)
        or any(r.get("needs_second_opinion") for r in all_runs)
    )

    # Combined majority code across all runs
    code_counts: dict[str, int] = {}
    for r in all_runs:
        c = r.get("screening_code", "INCLUDE_TA")
        code_counts[c] = code_counts.get(c, 0) + 1
    majority_code = max(code_counts.items(), key=lambda kv: kv[1])[0]

    return {
        "record_id": record["record_id"],
        "screening_code": majority_code,
        "screening_decision": "INCLUDE" if majority_code == "INCLUDE_TA" else "EXCLUDE",
        "vote_share_include": pooled_vote_share,
        "n_runs": n,
        "code_counts": code_counts,
        "in_uncertainty_band": in_band,
        "model_agreement": agreement,
        "needs_second_opinion": needs_second,
        "per_model": [
            {k: v for k, v in pm.items() if k != "runs"} for pm in per_model
        ],
        "runs": all_runs,
    }

# ------------------------- Resume helpers -------------------------

def load_done_records(out_path: str) -> set[str]:
    """Return the set of record_ids already present in the output JSONL."""
    done: set[str] = set()
    p = Path(out_path)
    if not p.exists():
        return done
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                rid = json.loads(line).get("record_id")
                if rid: done.add(str(rid))
            except Exception:
                continue
    return done

# ------------------------- Main runner -------------------------

def process_one_record(rec: dict, system: str, critic_system: str, args) -> dict:
    """Screen a single record: fire k runs per model, aggregate, critic. Returns aggregated dict."""
    runs_by_model: dict[str, list[dict]] = {m: [] for m in args.models}

    # Fire all k×models runs in a small inner pool (all runs for this record concurrent)
    inner_workers = min(args.k * len(args.models), 10)
    with ThreadPoolExecutor(max_workers=inner_workers) as inner:
        futures = []
        for model in args.models:
            for k in range(args.k):
                futures.append((model, inner.submit(screen_once, system, rec, model, args.temperature, args.project)))
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

    # Aggregate each model independently, then combine
    per_model = [
        aggregate_one_model(runs_by_model[m], rec, tuple(args.uncertainty_band), m)
        for m in args.models
    ]
    agg = combine_models(per_model, rec, tuple(args.uncertainty_band))

    # Critic adjudication for flagged records
    critic_block = {"applied": False, "adjudication": None, "model": None}
    if agg["needs_second_opinion"] and args.critic_model:
        primary_for_critic = {
            "screening_code": agg["screening_code"],
            "screening_decision": agg["screening_decision"],
            "explanation": (agg["runs"][0].get("explanation", "") if agg["runs"] else ""),
            "supporting_quote": (agg["runs"][0].get("supporting_quote", "NA") if agg["runs"] else "NA"),
        }
        critic_result = screen_critic(
            critic_system, rec, primary_for_critic,
            args.critic_model, args.temperature, args.project,
        )
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
    return agg


def run(args):
    system = load_system_message(args.prompt)
    critic_system = load_critic_message(args.prompt)
    records = [json.loads(l) for l in open(args.records, "r", encoding="utf-8") if l.strip()]

    # Resumable: skip already-completed records
    done = load_done_records(args.out)
    if done:
        print(f"Resume: {len(done)} record(s) already in {args.out}, will skip.")

    pending = [rec for rec in records if str(rec["record_id"]) not in done]
    total = len(records)
    print(f"Processing {len(pending)} record(s) with {args.workers} parallel record workers "
          f"(each firing k={args.k} runs × {len(args.models)} model(s)).")

    # Append mode so we don't overwrite prior progress
    mode = "a" if done else "w"
    with open(args.out, mode, encoding="utf-8") as f_out:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            future_to_rec = {
                ex.submit(process_one_record, rec, system, critic_system, args): rec
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
                critic_tag = f" critic={agg['critic']['adjudication']}" if agg.get("critic", {}).get("applied") else ""
                print(f"[{rec['record_id']}] code={agg['screening_code']} vote_share={agg['vote_share_include']:.2f} agree={agg['model_agreement']} n_runs={agg['n_runs']}{critic_tag} (remaining: {remaining})")

    print(f"\nWrote {total} aggregated records to {args.out}")

# ------------------------- Calibration (ECE / Brier / reliability) -------------------------

LABEL_MAP = {
    # MHAA human labels (EPPI TAS decision)
    "EXCLUDE on language":"EXCLUDE_LANGUAGE",
    "EXCLUDE on publication year (< 2015)":"EXCLUDE_YEAR",
    "EXCLUDE on population":"EXCLUDE_POPULATION",
    "EXCLUDE on topic/interest":"EXCLUDE_TOPIC",
    "EXCLUDE on evidence/record type":"EXCLUDE_EVIDENCE_TYPE",
    "EXCLUDE on duplicate publication":"EXCLUDE_DUPLICATE",
    "INCLUDE on title & abstract":"INCLUDE_TA",
    # ULCM (StrongMinds) human labels — match the project's GT CSV verbatim.
    # EXCLUDE_POPULATION and INCLUDE_TA are shared with MHAA; the ones below are
    # ULCM-specific. The StrongMinds CSV uses "EXCLUDE on intervention" etc.
    "EXCLUDE on intervention":"EXCLUDE_INTERVENTION_TOPIC",
    "EXCLUDE on study design":"EXCLUDE_STUDY_DESIGN",
    "EXCLUDE on outcome":"EXCLUDE_OUTCOME",
    "EXCLUDE on context/geography":"EXCLUDE_CONTEXT_GEOGRAPHY",
    "EXCLUDE on time/language":"EXCLUDE_TIME_LANGUAGE",
    "EXCLUDE - duplicate (provide ID in info box)":"EXCLUDE_DUPLICATE",
    # "Screen on Title & Abstract" is the screen-stage marker (not a final decision);
    # treat it as no label (dropped by load_ground_truth) rather than mapping it.
}

def load_ground_truth(path: str) -> dict[str, int]:
    """gt.json: {record_id: 'INCLUDE on title & abstract'} → {record_id: 1 or 0}."""
    raw = json.load(open(path, "r", encoding="utf-8"))
    gt: dict[str, int] = {}
    for rid, v in raw.items():
        code = LABEL_MAP.get((v or "").strip(), None)
        if code is None: continue
        gt[rid] = 1 if code == "INCLUDE_TA" else 0
    return gt

def ece_brier(preds: list[float], ys: list[int], n_bins: int = 10) -> tuple[float, float, list[dict]]:
    """Decile-binned ECE, Brier score, and per-bin reliability rows."""
    N = len(preds)
    bins: list[list[tuple[float,int]]] = [[] for _ in range(n_bins)]
    for p, y in zip(preds, ys):
        b = min(int(p * n_bins), n_bins - 1)
        bins[b].append((p, y))
    rows = []
    ece = 0.0
    for i, entries in enumerate(bins):
        lo = i / n_bins; hi = (i+1) / n_bins
        if not entries:
            rows.append({"bin_lo": lo, "bin_hi": hi, "n": 0, "mean_pred": None, "empirical_rate": None, "abs_gap": 0.0})
            continue
        mean_p = sum(p for p,_ in entries) / len(entries)
        emp_rate = sum(y for _,y in entries) / len(entries)
        gap = abs(mean_p - emp_rate)
        ece += gap * len(entries) / N
        rows.append({"bin_lo": lo, "bin_hi": hi, "n": len(entries), "mean_pred": mean_p, "empirical_rate": emp_rate, "abs_gap": gap})
    brier = sum((p - y)**2 for p,y in zip(preds, ys)) / N
    return ece, brier, rows

def _plot_confusion_matrix(tp, fp, fn, tn, out_path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    cm = np.array([[tp, fn], [fp, tn]])
    labels = [["TP", "FN"], ["FP", "TN"]]
    fig, ax = plt.subplots(figsize=(5, 4.5))
    im = ax.imshow(cm, cmap="Blues", aspect="auto")
    ax.set_xticks([0, 1]); ax.set_yticks([0, 1])
    ax.set_xticklabels(["Pred INCLUDE", "Pred EXCLUDE"])
    ax.set_yticklabels(["True INCLUDE", "True EXCLUDE"])
    for i in range(2):
        for j in range(2):
            ax.text(j, i, f"{labels[i][j]}\n{cm[i, j]}", ha="center", va="center",
                    color="white" if cm[i, j] > cm.max() / 2 else "black", fontsize=11)
    ax.set_title("MHAA Screening — Confusion Matrix")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    plt.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)

def _plot_reliability(rows: list[dict], ece: float, out_path: Path):
    """Reliability diagram: mean predicted vote-share vs empirical INCLUDE rate per decile."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    bins_lo = [r["bin_lo"] for r in rows]
    width = rows[0]["bin_hi"] - rows[0]["bin_lo"] if rows else 0.1
    mean_pred = [r["mean_pred"] if r["mean_pred"] is not None else 0.0 for r in rows]
    emp = [r["empirical_rate"] if r["empirical_rate"] is not None else 0.0 for r in rows]

    fig, ax = plt.subplots(figsize=(6, 4.5))
    # Bars: empirical rate per bin
    ax.bar(bins_lo, emp, width=width, align="edge", alpha=0.55,
           color="tab:blue", label="Empirical INCLUDE rate")
    # Line + markers: mean predicted vote-share
    centers = [lo + width / 2 for lo in bins_lo]
    ax.plot(centers, mean_pred, "o-", color="tab:red", label="Mean predicted vote-share")
    ax.plot([0, 1], [0, 1], "k--", alpha=0.4, label="Perfect calibration")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.set_xlabel("Vote-share INCLUDE probability (decile)")
    ax.set_ylabel("Empirical INCLUDE rate")
    ax.set_title(f"Reliability Diagram — ECE = {ece:.4f}")
    ax.legend(loc="upper left", fontsize=8)
    plt.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)

def _confusion_metrics(preds_bin: list[int], ys: list[int]) -> dict:
    """Binary confusion-matrix metrics (sens/spec/prec/f1/kappa) from 0/1 lists."""
    tp = fp = fn = tn = 0
    for p, y in zip(preds_bin, ys):
        if p == 1 and y == 1: tp += 1
        elif p == 1 and y == 0: fp += 1
        elif p == 0 and y == 1: fn += 1
        else: tn += 1
    N = tp + fp + fn + tn
    sens = tp / (tp + fn) if tp + fn else 0.0
    spec = tn / (tn + fp) if tn + fp else 0.0
    prec = tp / (tp + fp) if tp + fp else 0.0
    f1 = 2 * prec * sens / (prec + sens) if (prec + sens) else 0.0
    Po = (tp + tn) / N if N else 0.0
    pL = (tp + fp) / N if N else 0.0
    pG = (tp + fn) / N if N else 0.0
    Pe = pL * pG + (1 - pL) * (1 - pG)
    kappa = (Po - Pe) / (1 - Pe) if (1 - Pe) else 0.0
    return {"n": N, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "sensitivity": sens, "specificity": spec, "precision": prec,
            "f1": f1, "cohens_kappa": kappa}

def _inter_model_kappa(lm: dict, gt: dict[str, int]) -> dict | None:
    """Cohen's κ between the two primary models' binary decisions, on GT-covered records.

    Reads each model's runs from the per-record `per_model` block. Returns None if
    fewer than 2 models are present.
    """
    model_preds: dict[str, list[int]] = {}
    for rid, rec in lm.items():
        if rid not in gt: continue
        for pm in rec.get("per_model", []):
            # Derive a stable model key (strip provider prefix for readability)
            mk = pm.get("model", "unknown")
            # per_model doesn't carry model name explicitly; infer from runs
            decision = 1 if pm.get("screening_decision") == "INCLUDE" else 0
            model_preds.setdefault(mk, []).append((rid, decision))

    if len(model_preds) < 2:
        return None

    keys = list(model_preds.keys())
    a_rids = {rid: d for rid, d in model_preds[keys[0]]}
    b_rids = {rid: d for rid, d in model_preds[keys[1]]}
    common = sorted(set(a_rids) & set(b_rids))
    if len(common) < 2:
        return None

    preds_a = [a_rids[r] for r in common]
    preds_b = [b_rids[r] for r in common]
    # Treat model A as "truth", model B as "pred" for a symmetric κ
    m = _confusion_metrics(preds_b, preds_a)
    agree = sum(1 for x, y in zip(preds_a, preds_b) if x == y)
    return {
        "model_a": keys[0], "model_b": keys[1],
        "n_common": len(common),
        "agreement_rate": agree / len(common),
        "cohens_kappa": m["cohens_kappa"],
    }

def calibrate(args):
    """Compute ECE / Brier / confusion matrix / kappa / sensitivity vs ground truth,
    plus per-model breakdown and inter-model agreement."""
    gt = load_ground_truth(args.gt)
    lm = {}
    for line in open(args.calibrate, "r", encoding="utf-8"):
        r = json.loads(line); lm[r["record_id"]] = r

    preds: list[float] = []; ys: list[int] = []
    errors: list[dict] = []
    tp = fp = fn = tn = 0
    for rid in lm:
        if rid not in gt: continue
        p = lm[rid]["vote_share_include"]
        y = gt[rid]
        preds.append(p); ys.append(y)
        pred_binary = 1 if p >= 0.5 else 0
        if pred_binary == 1 and y == 1: tp += 1
        elif pred_binary == 1 and y == 0:
            fp += 1
            errors.append({"record_id": rid, "error_type": "FP",
                           "vote_share_include": p, "gt_label": "EXCLUDE",
                           "majority_code": lm[rid].get("screening_code"),
                           "explanation": lm[rid].get("runs", [{}])[0].get("explanation", "") if lm[rid].get("runs") else "",
                           "supporting_quote": lm[rid].get("runs", [{}])[0].get("supporting_quote", "") if lm[rid].get("runs") else ""})
        elif pred_binary == 0 and y == 1:
            fn += 1
            errors.append({"record_id": rid, "error_type": "FN",
                           "vote_share_include": p, "gt_label": "INCLUDE",
                           "majority_code": lm[rid].get("screening_code"),
                           "explanation": lm[rid].get("runs", [{}])[0].get("explanation", "") if lm[rid].get("runs") else "",
                           "supporting_quote": lm[rid].get("runs", [{}])[0].get("supporting_quote", "") if lm[rid].get("runs") else ""})
        else: tn += 1

    N = tp + fp + fn + tn
    sens = tp / (tp + fn) if tp + fn else 0.0
    spec = tn / (tn + fp) if tn + fp else 0.0
    prec = tp / (tp + fp) if tp + fp else 0.0
    f1 = 2 * prec * sens / (prec + sens) if (prec + sens) else 0.0
    Po = (tp + tn) / N
    pL = (tp + fp) / N; pG = (tp + fn) / N
    Pe = pL * pG + (1 - pL) * (1 - pG)
    kappa = (Po - Pe) / (1 - Pe) if (1 - Pe) else 0.0
    ece, brier, rows = ece_brier(preds, ys)

    # Threshold pass/fail
    sens_ok = sens >= 0.95
    kappa_ok = kappa >= 0.70
    ece_ok = ece <= 0.10

    print(f"\n=== k=5 sampled calibration on {args.calibrate} vs {args.gt} ===")
    print(f"N = {N}")
    print(f"\nConfusion matrix:")
    print(f"{'':>18}{'Pred INCLUDE':>14}{'Pred EXCLUDE':>14}")
    print(f"{'True INCLUDE':>18}{tp:>14}{fn:>14}")
    print(f"{'True EXCLUDE':>18}{fp:>14}{tn:>14}")
    print(f"\nSensitivity = {sens:.3f}   (>=0.95) {'PASS' if sens_ok else 'FAIL'}")
    print(f"Specificity = {spec:.3f}")
    print(f"Precision   = {prec:.3f}")
    print(f"F1          = {f1:.3f}")
    print(f"Cohen κ     = {kappa:.3f}   (>=0.70) {'PASS' if kappa_ok else 'FAIL'}")
    print(f"ECE         = {ece:.4f}   (<=0.10) {'PASS' if ece_ok else 'FAIL'}")
    print(f"Brier       = {brier:.4f}")
    print(f"\nReliability by vote-share decile:")
    print(f"{'bin':<12}{'n':>6}{'mean_pred':>12}{'empirical':>12}{'|gap|':>10}")
    for r in rows:
        mp = f"{r['mean_pred']:.3f}" if r['mean_pred'] is not None else "-"
        er = f"{r['empirical_rate']:.3f}" if r['empirical_rate'] is not None else "-"
        print(f"[{r['bin_lo']:.1f}-{r['bin_hi']:.1f}]  {r['n']:>6}{mp:>12}{er:>12}{r['abs_gap']:>10.3f}")

    # --- Per-model breakdown vs ground truth ---
    per_model_metrics: dict[str, dict] = {}
    model_vote_map: dict[str, dict[str, int]] = {}  # model -> {rid: 0/1}
    for rid, rec in lm.items():
        if rid not in gt: continue
        for pm in rec.get("per_model", []):
            mk = pm.get("model", "unknown")
            decision = 1 if pm.get("screening_decision") == "INCLUDE" else 0
            model_vote_map.setdefault(mk, {})[rid] = decision

    print(f"\n--- Per-model breakdown vs ground truth ---")
    for mk, votes in model_vote_map.items():
        ids = sorted(votes)
        ys_m = [gt[r] for r in ids]
        preds_m = [votes[r] for r in ids]
        mm = _confusion_metrics(preds_m, ys_m)
        # Per-model ECE on the model's own vote_share (for records with GT)
        pm_preds = []
        pm_ys = []
        for r in ids:
            rec = lm[r]
            # find this model's vote_share from per_model
            for pm in rec.get("per_model", []):
                if pm.get("model", "unknown") == mk:
                    pm_preds.append(pm.get("vote_share_include", votes[r]))
                    pm_ys.append(gt[r])
                    break
        ece_m, brier_m, _ = ece_brier(pm_preds, pm_ys)
        mm["ece"] = ece_m
        mm["brier"] = brier_m
        per_model_metrics[mk] = mm
        print(f"{mk}: sens={mm['sensitivity']:.3f} spec={mm['specificity']:.3f} "
              f"κ={mm['cohens_kappa']:.3f} ECE={ece_m:.4f} (n={mm['n']})")

    # --- Inter-model agreement (Cohen's κ between the two primary models) ---
    inter = _inter_model_kappa(lm, gt)
    if inter:
        print(f"\n--- Inter-model agreement ---")
        print(f"{inter['model_a']} vs {inter['model_b']}: "
              f"agreement={inter['agreement_rate']:.3f} κ={inter['cohens_kappa']:.3f} "
              f"(n={inter['n_common']})")

    # --- Critic adjudication summary ---
    critic_applied = sum(1 for r in lm.values() if r.get("critic", {}).get("applied"))
    critic_overrides = sum(1 for r in lm.values() if r.get("critic", {}).get("adjudication") == "override")
    if critic_applied:
        print(f"\n--- Critic adjudication ---")
        print(f"Applied to {critic_applied} record(s); {critic_overrides} override(s).")

    # Write reports
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)

    metrics_path = reports_dir / "metrics.json"
    metrics_path.write_text(json.dumps({
        "N": N, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "sensitivity": sens, "specificity": spec, "precision": prec, "f1": f1,
        "cohens_kappa": kappa, "ece": ece, "brier": brier,
        "thresholds": {"sensitivity_gte_095": sens_ok, "kappa_gte_070": kappa_ok, "ece_lte_010": ece_ok},
        "reliability_bins": rows,
        "per_model": per_model_metrics,
        "inter_model": inter,
        "critic": {"applied": critic_applied, "overrides": critic_overrides},
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {metrics_path}")

    # Confusion matrix PNG
    try:
        _plot_confusion_matrix(tp, fp, fn, tn, reports_dir / "confusion_matrix.png")
        print(f"Wrote {reports_dir / 'confusion_matrix.png'}")
    except Exception as e:
        print(f"(confusion-matrix plot skipped: {e})")

    # Reliability diagram PNG
    try:
        _plot_reliability(rows, ece, reports_dir / "reliability_diagram.png")
        print(f"Wrote {reports_dir / 'reliability_diagram.png'}")
    except Exception as e:
        print(f"(reliability-diagram plot skipped: {e})")

    # Errors JSONL (FN + FP for criteria refinement)
    if errors:
        errors_path = reports_dir / "errors.jsonl"
        with errors_path.open("w", encoding="utf-8") as ef:
            for e in errors:
                ef.write(json.dumps(e, ensure_ascii=False) + "\n")
        print(f"Wrote {errors_path} ({len(errors)} misclassified records: {fn} FN, {fp} FP)")

# ------------------------- CLI -------------------------

def main():
    p = argparse.ArgumentParser(description="k=5 MHAA screening runner (OpenRouter) + calibration.")
    p.add_argument("--prompt", type=str, help="Path to the v1.x prompt .md file")
    p.add_argument("--records", type=str, help="JSONL of records to screen")
    p.add_argument("--gt", type=str, help="Ground truth JSON (rid -> EPPI label)")
    p.add_argument("--out", type=str, help="Output JSONL of aggregated per-record results")
    p.add_argument("--k", type=int, default=5, help="Sampled runs per model")
    p.add_argument("--temperature", type=float, default=0.5)
    p.add_argument("--models", nargs="+", default=["anthropic/claude-sonnet-4"],
                   help="OpenRouter model slugs. Give two for cross-model consensus.")
    p.add_argument("--tie-break-model", type=str, default=None,
                   help="(Deprecated, aliased to --critic-model) Third model for tie-break.")
    p.add_argument("--critic-model", type=str, default=None,
                   help="OpenRouter model slug for the §2 critic/adjudicator, applied to flagged records.")
    p.add_argument("--uncertainty-band", nargs=2, type=float, default=[0.4, 0.6])
    p.add_argument("--workers", type=int, default=5)
    p.add_argument("--project", type=str, default="mhaa", choices=list(PROJECT_CONFIG.keys()),
                   help="Project adapter: selects user/critic message template, default "
                        "screening_level, and max_tokens. 'mhaa' for the MHAA pipeline, "
                        "'strongminds' for the ULCM adult-depression prompt set.")
    p.add_argument("--calibrate", type=str, default=None,
                   help="Instead of running, load this aggregated JSONL and compute ECE/kappa/sensitivity.")
    args = p.parse_args()

    # --tie-break-model is the legacy alias for --critic-model
    if args.tie_break_model and not args.critic_model:
        args.critic_model = args.tie_break_model

    if args.calibrate:
        if not args.gt: sys.exit("--gt required for --calibrate")
        calibrate(args); return

    if not (args.prompt and args.records and args.out):
        sys.exit("--prompt, --records, --out required for a run")
    run(args)

if __name__ == "__main__":
    main()
