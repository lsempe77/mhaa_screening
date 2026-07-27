import csv
inv = list(csv.DictReader(open(r'projects/strongminds/full_text_retrieval/logs/inventory_merged.csv', encoding='utf-8-sig')))
for r in inv:
    if r.get('pdf_path','') and r.get('attach_status','') not in ('uploaded','exists'):
        key = r.get('zotero_key','')
        status = r.get('attach_status','')
        title = r.get('title','')[:50]
        print(f'  {key:20s} status={status:30s} {title}')
