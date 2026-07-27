import csv
from collections import Counter

inv = list(csv.DictReader(open(r'projects/strongminds/full_text_retrieval/logs/inventory_merged.csv', encoding='utf-8-sig')))
def norm(doi):
    d = (doi or '').lower().strip()
    for p in ('https://dx.doi.org/','https://doi.org/','http://doi.org/'):
        d = d.replace(p, '')
    return d

# MDPI
mdpi_miss = [r for r in inv if norm(r.get('doi','')).startswith('10.3390') and not r.get('pdf_path','') and r.get('attach_status','') not in ('uploaded','exists')]
print(f'=== MDPI missing: {len(mdpi_miss)} ===')
mdpi_src = Counter(r.get('pdf_source','') or '(empty)' for r in mdpi_miss)
for s, c in mdpi_src.most_common(5):
    print(f'  pdf_source={s:30s} {c}')
for r in mdpi_miss[:5]:
    print(f'  {norm(r["doi"])[:40]:42s} {r.get("title","")[:50]}')

print()

# Elsevier
els_miss = [r for r in inv if norm(r.get('doi','')).startswith('10.1016') and not r.get('pdf_path','') and r.get('attach_status','') not in ('uploaded','exists')]
print(f'=== Elsevier missing: {len(els_miss)} ===')
els_src = Counter(r.get('pdf_source','') or '(empty)' for r in els_miss)
for s, c in els_src.most_common(5):
    print(f'  pdf_source={s:30s} {c}')
els_years = Counter(r.get('year','') for r in els_miss)
print(f'  By year (top 5):')
for y, c in els_years.most_common(5):
    print(f'    {y}: {c}')
for r in els_miss[:5]:
    print(f'  {norm(r["doi"])[:40]:42s} {r.get("year","")} {r.get("title","")[:40]}')
