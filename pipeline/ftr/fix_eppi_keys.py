"""Fix the 6 records with EPPI IDs instead of Zotero keys by matching titles."""
import csv

merged = list(csv.DictReader(open(r'projects/strongminds/full_text_retrieval/logs/inventory_merged.csv', encoding='utf-8-sig')))
zot = list(csv.DictReader(open(r'projects/strongminds/full_text_retrieval/logs/inventory_20260724_202208.csv', encoding='utf-8-sig')))
fn = list(merged[0].keys())

# Build title -> zotero_key map from the Zotero export
zot_title_map = {}
for r in zot:
    t = r.get('title', '').lower().strip()[:60]
    if t:
        zot_title_map[t] = (r['zotero_key'], r.get('has_pdf', ''))

fixed = 0
for r in merged:
    key = r.get('zotero_key', '')
    if key.startswith('130') and len(key) == 9:
        # This is an EPPI ID, not a Zotero key — try title match
        t = r.get('title', '').lower().strip()[:60]
        if t in zot_title_map:
            real_key, zot_has = zot_title_map[t]
            r['zotero_key'] = real_key
            r['has_pdf'] = zot_has
            r['attach_status'] = ''
            r['attach_key'] = ''
            print(f'  Fixed: {key} -> {real_key} | {r.get("title","")[:50]}')
            fixed += 1
        else:
            print(f'  NOT FOUND: {key} | {r.get("title","")[:50]}')

with open(r'projects/strongminds/full_text_retrieval/logs/inventory_merged.csv', 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.DictWriter(f, fieldnames=fn)
    w.writeheader()
    w.writerows(merged)

print(f'\nFixed {fixed} records')
