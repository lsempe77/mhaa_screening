"""
config.py — Configuration for the full-text retrieval (FTR) pipeline.

Generic PDF retrieval pipeline (adapted from GE-ftr). Works for any project that
has an RIS file of INCLUDEs. The project data folder is set via FTR_PROJECT_DIR
env var (defaults to projects/strongminds/full_text_retrieval/).

API key resolution order:
  1. Environment variable ZOTERO_API_KEY  (recommended)
  2. A local, git-ignored file `.zotero_key` in the project folder
  3. The project folder's .env file
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Zotero target (the StrongMinds group — same as GE-ftr)
#   https://www.zotero.org/groups/6580398/strongminds/collections/6JEVFD99
# ---------------------------------------------------------------------------
LIBRARY_TYPE = "groups"          # "groups" or "users"
LIBRARY_ID = "6580398"           # StrongMinds group
COLLECTION_KEY = "6JEVFD99"      # target collection
INCLUDE_SUBCOLLECTIONS = True

ZOTERO_API_BASE = "https://api.zotero.org"

# ---------------------------------------------------------------------------
# Contact / API credentials
# ---------------------------------------------------------------------------
USER_EMAIL = "lsempe@3ieimpact.org"

# Optional: improves Semantic Scholar rate limits. Read from env only.
SEMANTIC_SCHOLAR_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# config.py is at pipeline/ftr/config.py
# The project data folder is configurable via FTR_PROJECT_DIR env var.
# Defaults to projects/strongminds/full_text_retrieval/ (the only FTR project so far).
_REPO_ROOT = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ.get("FTR_PROJECT_DIR", _REPO_ROOT / "projects/strongminds/full_text_retrieval"))
PDF_DIR = ROOT / "pdfs"
LOG_DIR = ROOT / "logs"
PDF_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Load .env from the project folder
_env_path = ROOT / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _, _val = _line.partition("=")
        os.environ.setdefault(_key.strip(), _val.strip().strip('"').strip("'"))

# The RIS file of INCLUDEs from TAS screening (project-specific)
RIS_FILE = ROOT.parent / "data" / "output" / "includes.ris"


def get_zotero_api_key() -> str:
    """Resolve the Zotero API key from env var or a local .zotero_key file."""
    key = os.environ.get("ZOTERO_API_KEY", "").strip()
    if key:
        return key

    key_file = ROOT / ".zotero_key"
    if key_file.exists():
        key = key_file.read_text(encoding="utf-8").strip()
        if key:
            return key

    raise SystemExit(
        "No Zotero API key found.\n"
        "Set it before running, e.g. (PowerShell):\n"
        '    $env:ZOTERO_API_KEY = "<your-key>"\n'
        "or create a file named .zotero_key in this folder containing only the key."
    )
