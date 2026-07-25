"""
config.py — Configuration for the data-extraction pipeline.

Extraction runs *after* full-text screening: it takes the included studies
(full PDF text) and pulls the structured fields defined by a framework YAML
(see framework.py). Like pipeline/ftr/config.py, the project data folder is
selectable so the same engine serves girl_effect now and strongminds later.

Env vars:
  EXTRACT_PROJECT_DIR   project extraction folder (default: girl_effect)
  OPENROUTER_API_KEY    (loaded from repo-root .env by the runner)
"""
from __future__ import annotations

import os
from pathlib import Path

# extraction/ lives at pipeline/extraction/ ; repo root is two levels up.
REPO_ROOT = Path(__file__).resolve().parents[2]

# Per-project framework registry. Add a StrongMinds framework here later.
PROJECT_FRAMEWORKS = {
    "girl_effect": REPO_ROOT / "pipeline/extraction/frameworks/mhaa_v1.yaml",
    # "strongminds": REPO_ROOT / "pipeline/extraction/frameworks/ulcm_v1.yaml",
}

# Default project + its extraction working directory.
DEFAULT_PROJECT = "girl_effect"
PROJECT_DIRS = {
    "girl_effect": REPO_ROOT / "projects/girl_effect/full_text",
    "strongminds": REPO_ROOT / "projects/strongminds/full_text",
}


def project_dir(project: str) -> Path:
    d = Path(os.environ.get("EXTRACT_PROJECT_DIR", PROJECT_DIRS[project]))
    return d


def extraction_dir(project: str) -> Path:
    """Where extraction outputs land: <project>/full_text/extraction/."""
    d = project_dir(project) / "extraction"
    d.mkdir(parents=True, exist_ok=True)
    (d / "output").mkdir(exist_ok=True)
    (d / "reports").mkdir(exist_ok=True)
    return d


def framework_path(project: str) -> Path:
    return PROJECT_FRAMEWORKS[project]


# --------------------------- model defaults ---------------------------
# Two independent extractors + a reconciler, mirroring the screening
# pipeline's dual-model + critic design. Claude leads (strongest at faithful
# long-context extraction + verbatim quoting); GLM-5.2 is the second reviewer.
DEFAULT_EXTRACTORS = ["anthropic/claude-sonnet-4", "z-ai/glm-5.2"]
DEFAULT_RECONCILER = "anthropic/claude-sonnet-4"

# Extraction responses are large (a whole field group with quotes per field),
# so max_tokens is well above the screening default.
EXTRACT_MAX_TOKENS = 8000
RECONCILE_MAX_TOKENS = 8000

# Full PDF text char cap handed to the model (matches ingest_fts MAX_CHARS).
MAX_SOURCE_CHARS = 400_000


def load_repo_env() -> None:
    """Load OPENROUTER_API_KEY + headers from the repo-root .env (if present)."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
