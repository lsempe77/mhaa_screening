"""Investigate Elsevier and MDPI gaps."""
import csv
from collections import Counter

inv = list(csv.DictReader(open(r'projects/strongminds/full_text_retrieval/logs/inventory_merged.csv', encoding='utf-8-sig')))

def norm_doi(r):
    d = r.get('doi','').lower().strip()
    for p in ('https://dx.doi.org/','https://doi.org/','http://doi.org/'):
        d = d.replace(p, '')
    return d

# === Elsevier ===
elsevier = [r for r in inv if norm_doi(r).startswith('10.1016')]
has_pdf = [r for r in elsevier if r.get('pdf_path','') or r.get('attach_status','') in ('uploaded','exists')]
missing = [r for r in elsevier if not r.get('pdf_path','') and r.get('attach_status','') not in ('uploaded','exists')]
print(f'=== Elsevier ===')
print(f'Total: {len(elsevier)} | With PDF: {len(has_pdf)} | Missing: {len(missing)}')
src = Counter(r.get('pdf_source','') or '(empty)' for r in has_pdf)
for s, c in src.most_common(10):
    print(f'  {s:30s} {c}')
# Check what years the missing ones are
years = Counter(r.get('year','') for r in missing)
print(f'Missing by year (top 10):')
for y, c in years.most_common(10):
    print(f'  {y}: {c}')
# Check if missing have pre-prints (10.1016/j.xxx.preprint)
preprints = sum(1 for r in missing if 'preprint' in norm_doi(r))
print(f'Preprints in missing: {preprints}')
print(f'Sample missing:')
for r in missing[:5]:
    print(f'  {norm_doi(r)[:40]:42s} {r.get("title","")[:50]}')

# === MDPI ===
print(f'\n=== MDPI ===')
mdpi = [r for r in inv if norm_doi(r).startswith('10.3390')]
has_pdf_m = [r for r in mdpi if r.get('pdf_path','') or r.get('attach_status','') in ('uploaded','exists')]
missing_m = [r for r in mdpi if not r.get('pdf_path','') and r.get('attach_status','') not in ('uploaded','exists')]
print(f'Total: {len(mdpi)} | With PDF: {len(has_pdf_m)} | Missing: {len(missing_m)}')
src_m = Counter(r.get('pdf_source','') or '(empty)' for r in has_pdf_m)
for s, c in src_m.most_common(10):
    print(f'  {s:30s} {c}')
print(f'Sample missing MDPI:')
for r in missing_m[:5]:
    print(f'  {norm_doi(r)[:40]:42s} {r.get("title","")[:50]}')
