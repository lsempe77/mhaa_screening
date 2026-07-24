"""
config.py — Configuration for the StrongMinds ULCM full-text retrieval pipeline.

Adapted from the GE-ftr pipeline. The key difference: the starting point is the
RIS file of INCLUDEs (from TAS screening), not a Zotero collection export.

API key resolution order:
  1. Environment variable ZOTERO_API_KEY  (recommended)
  2. A local, git-ignored file `.zotero_key` in this folder
If neither is set the scripts will stop with a clear message.
"""

import os
from pathlib import Path


def _load_dotenv() -> None:
    """Load KEY=VALUE pairs from a local, git-ignored .env into the environment
    (without overriding variables already set in the shell)."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv()

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
# This file is at projects/strongminds/full_text_retrieval/scripts/config.py
# ROOT = projects/strongminds/full_text_retrieval/
ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "pdfs"
LOG_DIR = ROOT / "logs"
PDF_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

# The RIS file of INCLUDEs from TAS screening
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
