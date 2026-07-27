"""Check local PDFs not yet in Zotero."""
import csv

inv = list(csv.DictReader(open(r'projects/strongminds/full_text_retrieval/logs/inventory_merged.csv', encoding='utf-8-sig')))
local_only = [r for r in inv if r.get('pdf_path', '') and r.get('attach_status', '') not in ('uploaded', 'exists')]
print(f'Local PDF not in Zotero: {len(local_only)}')
for r in local_only[:30]:
    print(f'  {r["zotero_key"]} | status={r.get("attach_status", "")} | {r.get("pdf_source", "")} | {r["title"][:70]}')
if len(local_only) > 30:
    print(f'  ... and {len(local_only) - 30} more')
