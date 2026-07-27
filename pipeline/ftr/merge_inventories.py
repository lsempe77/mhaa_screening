"""Merge Zotero export (real item keys) with step2 inventory (PDF paths)."""
import pandas as pd

step2 = pd.read_csv(r'projects\strongminds\full_text_retrieval\logs\inventory_20260724_092404.csv', dtype=str).fillna('')
zot = pd.read_csv(r'projects\strongminds\full_text_retrieval\logs\inventory_20260724_202208.csv', dtype=str).fillna('')

print(f'Step2 inventory: {len(step2)} rows, {(step2["pdf_path"] != "").sum()} with PDF path')
print(f'Zotero inventory: {len(zot)} rows, {zot["has_pdf"].astype(str).str.lower().isin(["true"]).sum()} already have PDF in Zotero')

step2['doi_norm'] = step2['doi'].str.lower().str.replace('https://doi.org/', '', regex=False).str.replace('http://doi.org/', '', regex=False)
zot['doi_norm'] = zot['doi'].str.lower().str.replace('https://doi.org/', '', regex=False).str.replace('http://doi.org/', '', regex=False)

# Build a 1:1 lookup: doi_norm -> (zotero_key, has_pdf) — only non-empty DOIs
zot_doi_map = {}
for _, zrow in zot[zot['doi_norm'] != ''].iterrows():
    zot_doi_map[zrow['doi_norm']] = (zrow['zotero_key'], zrow['has_pdf'])

# Also build title map for records without DOI
zot_title_map = {}
for _, zrow in zot.iterrows():
    t = zrow['title'].lower().strip()[:80]
    if t:
        zot_title_map[t] = (zrow['zotero_key'], zrow['has_pdf'])

# Apply: for each step2 row, find the Zotero key
real_keys = []
real_has_pdf = []
doi_matched = 0
title_matched = 0
unmatched = 0
for _, row in step2.iterrows():
    doi = row['doi_norm']
    found = None
    if doi and doi in zot_doi_map:
        found = zot_doi_map[doi]
        doi_matched += 1
    if not found:
        t = row['title'].lower().strip()[:80]
        if t and t in zot_title_map:
            found = zot_title_map[t]
            title_matched += 1
    if found:
        real_keys.append(found[0])
        real_has_pdf.append(found[1])
    else:
        real_keys.append('')
        real_has_pdf.append('')
        unmatched += 1

step2['zotero_key'] = [k if k else orig for k, orig in zip(real_keys, step2['zotero_key'])]
step2['has_pdf'] = [h if h else orig for h, orig in zip(real_has_pdf, step2['has_pdf'])]
step2 = step2.drop(columns=['doi_norm'])

out = r'projects\strongminds\full_text_retrieval\logs\inventory_merged.csv'
step2.to_csv(out, index=False, encoding='utf-8-sig')
print(f'\nMatched by DOI: {doi_matched}')
print(f'Matched by title: {title_matched}')
print(f'Unmatched: {unmatched}')
print(f'\nMerged inventory: {out}')
print(f'Total: {len(step2)}')
print(f'With real Zotero key: {(step2["zotero_key"] != "").sum()}')
print(f'With PDF path (local): {(step2["pdf_path"] != "").sum()}')
print(f'Zotero already has PDF: {step2["has_pdf"].astype(str).str.lower().isin(["true"]).sum()}')
need_attach = ((step2["pdf_path"] != "") & (~step2["has_pdf"].astype(str).str.lower().isin(["true"]))).sum()
print(f'Need Zotero attach (have local PDF, Zotero has none): {need_attach}')
