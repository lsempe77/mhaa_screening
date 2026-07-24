"""
Generate a clickable HTML worklist for the paywalled papers that are recoverable
only through Oxford's authenticated browser (LibKey 'Download PDF' via SOLO).

Excludes registry/aggregator stubs (Cochrane CENTRAL CN-, ISRCTN) that have no
full text. For each paper it shows the exact filename to 'Save As' into the pdfs/
folder, so scan_manual_pdfs.py + step3 can attach them automatically afterwards.

Run:
    python generate_worklist.py inventory_{timestamp}.csv
    then open worklist.html
"""

import html
import re
import sys

import pandas as pd

import config
from step2_fetch_missing_pdfs import target_filename

SOLO = "https://solo.bodleian.ox.ac.uk/openurl/44OXF_INST/44OXF_INST:SOLO"

PUBLISHER = {
    "10.1002": "Wiley", "10.1111": "Wiley", "10.1080": "Taylor & Francis",
    "10.1177": "SAGE", "10.1007": "Springer", "10.1097": "Wolters Kluwer",
    "10.1093": "Oxford UP", "10.3233": "IOS Press", "10.1089": "Mary Ann Liebert",
    "10.1159": "Karger", "10.2196": "JMIR", "10.1186": "BMC",
}


def is_stub(doi: str) -> bool:
    d = doi.upper()
    return "ISRCTN" in d or "/CENTRAL/CN-" in d or d.startswith("10.1002/CENTRAL")


def publisher_of(doi: str) -> str:
    m = re.match(r"^(10\.\d+)", doi)
    return PUBLISHER.get(m.group(1) if m else "", "Other")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python generate_worklist.py <inventory_csv>")
    csv_arg = sys.argv[1]
    csv_path = config.ROOT / csv_arg
    if not csv_path.exists():
        csv_path = config.LOG_DIR / csv_arg
    if not csv_path.exists():
        raise SystemExit(f"Inventory CSV not found: {csv_arg}")

    df = pd.read_csv(csv_path, dtype=str).fillna("")
    has_pdf = df["has_pdf"].str.lower().isin(["true", "1", "yes"])
    fetched = df["pdf_path"].str.strip() != ""
    miss = df[(~has_pdf) & (~fetched) & (df["doi"].str.strip() != "")].copy()
    miss = miss[~miss["doi"].apply(is_stub)]
    miss["pub"] = miss["doi"].apply(publisher_of)
    miss = miss.sort_values(["pub", "year"])
    n = len(miss)

    rows = []
    for i, (_, r) in enumerate(miss.iterrows(), start=1):
        doi = r["doi"].strip()
        fname = target_filename(r["zotero_key"], doi, r["title"])
        solo = f"{SOLO}?url_ver=Z39.88-2004&rft_id=info:doi/{doi}"
        rows.append(f"""
        <tr>
          <td class="num">{i}</td>
          <td><input type="checkbox"></td>
          <td class="pub">{html.escape(r['pub'])}</td>
          <td class="title">{html.escape(r['title'])}<div class="meta">{html.escape(r['authors'][:80])} &middot; {html.escape(r['year'])}</div></td>
          <td><a class="btn" href="{html.escape(solo)}" target="_blank" rel="noopener">Download&nbsp;PDF&nbsp;(SOLO)</a>
              <a class="lnk" href="https://doi.org/{html.escape(doi)}" target="_blank" rel="noopener">doi.org</a></td>
          <td class="fname">{html.escape(fname)}</td>
        </tr>""")

    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>GE-ftr — manual retrieval worklist</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:24px;color:#1a1a1a}}
 h1{{font-size:20px}} .lead{{background:#eef4ff;border:1px solid #cdd5f0;padding:12px 16px;border-radius:8px;max-width:1000px}}
 code{{background:#f2f2f2;padding:1px 5px;border-radius:4px}}
 table{{border-collapse:collapse;margin-top:16px;width:100%;max-width:1200px}}
 th,td{{border-bottom:1px solid #e5e5e5;padding:8px 10px;text-align:left;vertical-align:top;font-size:14px}}
 th{{background:#fafafa;position:sticky;top:0}}
 .num{{color:#888}} .pub{{white-space:nowrap;font-weight:600}}
 .title{{max-width:520px}} .meta{{color:#888;font-size:12px;margin-top:2px}}
 .fname{{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:#555;max-width:260px;word-break:break-all}}
 .btn{{display:inline-block;background:#2557d6;color:#fff;text-decoration:none;padding:5px 10px;border-radius:6px;font-size:13px}}
 .btn:hover{{background:#1c46ad}} .lnk{{margin-left:8px;font-size:12px;color:#2557d6}}
 tr:hover{{background:#fcfcff}}
</style></head><body>
<h1>Manual retrieval worklist — {n} papers</h1>
<div class="lead">
 <b>How to use</b> (you must be signed in to Oxford SSO in the browser):
 <ol>
  <li>Click <b>Download PDF (SOLO)</b>. On the SOLO page, click the blue <b>Download PDF</b> button (LibKey). Complete any &ldquo;Stay signed in?&rdquo; prompt <b>once</b> &mdash; later ones then flow automatically.</li>
  <li><b>Save As</b> the file using the exact name in the <b>Save as</b> column, into the folder
      <code>{html.escape(str(config.PDF_DIR))}</code></li>
  <li>Tick the checkbox to track progress. When done, tell the assistant &mdash; it will attach everything you saved to the right Zotero items.</li>
 </ol>
 Tip: rows are grouped by publisher, so the SSO chain is reused within a publisher.
</div>
<table>
 <thead><tr><th>#</th><th>&#10003;</th><th>Publisher</th><th>Paper</th><th>Get&nbsp;PDF</th><th>Save as (into pdfs/)</th></tr></thead>
 <tbody>{''.join(rows)}
 </tbody>
</table>
</body></html>"""

    out = config.ROOT / "worklist.html"
    out.write_text(page, encoding="utf-8")
    print(f"Wrote {out}  ({n} papers)")
    print("By publisher:")
    print(miss["pub"].value_counts().to_string())


if __name__ == "__main__":
    main()
