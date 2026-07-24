"""
Step 3: Attach the fetched PDFs back to their Zotero items.

For every reference the pipeline fetched a PDF for (pdf_path set, and the item
did not already have a PDF in Zotero), this uploads the file as a child
attachment of the correct item, matched by `zotero_key`.

Uses the Zotero file-upload protocol:
  1. create an `attachment` (imported_file) child item under the parent
  2. request upload authorization (md5/filename/filesize/mtime)
  3. upload the bytes to Zotero storage
  4. register the completed upload

Idempotent: records `attach_status` (+ `attach_key`) in the inventory and skips
items already attached. Only NEW fetches are uploaded; the 192 references that
already had a PDF in Zotero are never touched.

Run (dry test on one item first, then all):
    python step3_attach_to_zotero.py inventory_{timestamp}.csv --limit 1
    python step3_attach_to_zotero.py inventory_{timestamp}.csv
"""

import hashlib
import os
import sys
import time
from datetime import datetime

import pandas as pd
import requests

import config

API = config.ZOTERO_API_BASE
LIB = f"{config.LIBRARY_TYPE}/{config.LIBRARY_ID}"
CHECKPOINT_EVERY = 5


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}")
    sys.stdout.flush()


def _headers(key, extra=None):
    h = {"Zotero-API-Key": key, "User-Agent": "GE-ftr attach"}
    if extra:
        h.update(extra)
    return h


def create_attachment_item(key, parent_key, filename) -> str:
    tmpl = requests.get(
        f"{API}/items/new",
        params={"itemType": "attachment", "linkMode": "imported_file"},
        headers=_headers(key), timeout=20,
    ).json()
    tmpl["parentItem"] = parent_key
    tmpl["title"] = "Full Text PDF"
    tmpl["filename"] = filename
    tmpl["contentType"] = "application/pdf"

    r = requests.post(
        f"{API}/{LIB}/items",
        headers=_headers(key, {"Content-Type": "application/json"}),
        json=[tmpl], timeout=30,
    )
    r.raise_for_status()
    resp = r.json()
    succ = resp.get("successful", {})
    if "0" in succ:
        return succ["0"]["key"]
    raise RuntimeError(f"create attachment failed: {resp.get('failed')}")


def upload_file(key, attach_key, filepath) -> str:
    with open(filepath, "rb") as f:
        data = f.read()
    md5 = hashlib.md5(data).hexdigest()
    filename = os.path.basename(filepath)
    mtime = int(os.path.getmtime(filepath) * 1000)

    # 1) authorization
    r = requests.post(
        f"{API}/{LIB}/items/{attach_key}/file",
        headers=_headers(key, {"Content-Type": "application/x-www-form-urlencoded", "If-None-Match": "*"}),
        data={"md5": md5, "filename": filename, "filesize": len(data), "mtime": mtime,
              "contentType": "application/pdf"},
        timeout=30,
    )
    r.raise_for_status()
    auth = r.json()
    if auth.get("exists"):
        return "exists"

    # 2) upload bytes to storage
    up = requests.post(
        auth["url"],
        data=auth["prefix"].encode("utf-8") + data + auth["suffix"].encode("utf-8"),
        headers={"Content-Type": auth["contentType"]},
        timeout=180,
    )
    up.raise_for_status()

    # 3) register the completed upload
    reg = requests.post(
        f"{API}/{LIB}/items/{attach_key}/file",
        headers=_headers(key, {"Content-Type": "application/x-www-form-urlencoded", "If-None-Match": "*"}),
        data={"upload": auth["uploadKey"]},
        timeout=30,
    )
    reg.raise_for_status()
    return "uploaded"


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python step3_attach_to_zotero.py <inventory_csv> [--limit N]")

    csv_arg = sys.argv[1]
    csv_path = config.ROOT / csv_arg
    if not csv_path.exists():
        csv_path = config.LOG_DIR / csv_arg
    if not csv_path.exists():
        raise SystemExit(f"Inventory CSV not found: {csv_arg}")

    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    key = config.get_zotero_api_key()

    df = pd.read_csv(csv_path, dtype=str).fillna("")
    for col in ("attach_status", "attach_key"):
        if col not in df.columns:
            df[col] = ""

    has_pdf = df["has_pdf"].str.lower().isin(["true", "1", "yes"])
    fetched = df["pdf_path"].str.strip() != ""
    done = df["attach_status"].isin(["uploaded", "exists"])
    todo = df[fetched & (~has_pdf) & (~done)].copy()
    if limit:
        todo = todo.head(limit)
    log(f"PDFs to attach to Zotero: {len(todo)}")

    ok = 0
    for n, (idx, row) in enumerate(todo.iterrows(), start=1):
        zkey = row["zotero_key"]
        rel = row["pdf_path"]
        fpath = config.ROOT / rel
        if not fpath.exists():
            df.at[idx, "attach_status"] = "missing_file"
            log(f"[{n}/{len(todo)}] {zkey}: local file missing ({rel})")
            continue
        try:
            attach_key = row["attach_key"] or create_attachment_item(key, zkey, fpath.name)
            df.at[idx, "attach_key"] = attach_key
            status = upload_file(key, attach_key, fpath)
            df.at[idx, "attach_status"] = status
            ok += 1
            log(f"[{n}/{len(todo)}] {zkey}: {status} (attach {attach_key})")
        except Exception as e:
            df.at[idx, "attach_status"] = f"error: {type(e).__name__}"
            log(f"[{n}/{len(todo)}] {zkey}: ERROR {type(e).__name__}: {str(e)[:120]}")

        if n % CHECKPOINT_EVERY == 0:
            df.to_csv(csv_path, index=False, encoding="utf-8-sig")
            log(f"  ...checkpoint saved ({ok} attached so far)")
        time.sleep(0.5)

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    log("-" * 60)
    log(f"Attached: {ok}/{len(todo)}")
    log(f"Updated inventory: {csv_path}")


if __name__ == "__main__":
    main()
