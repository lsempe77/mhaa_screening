"""Fix orphan PDFs by matching EPPI ID from original inventory to merged inventory via DOI."""
import csv
from pathlib import Path

pdf_dir = Path(r'projects/strongminds/full_text_retrieval/pdfs')
merged_path = r'projects/strongminds/full_text_retrieval/logs/inventory_merged.csv'
orig_path = r'projects\strongminds/full_text_retrieval/logs/inventory_20260724_092404.csv'

# Build EPPI_ID -> DOI map from original inventory
orig = list(csv.DictReader(open(orig_path, encoding='utf-8-sig')))
eppi_to_doi = {}
for r in orig:
    if r['zotero_key'] and r['doi']:
        eppi_to_doi[r['zotero_key']] = r['doi'].lower().replace('https://doi.org/','').replace('http://doi.org/','')

# Build DOI -> merged row index
merged = list(csv.DictReader(open(merged_path, encoding='utf-8-sig')))
fieldnames = list(merged[0].keys())
doi_to_row = {}
for i, r in enumerate(merged):
    doi = r.get('doi', '').lower().replace('https://doi.org/','').replace('http://doi.org/','')
    if doi:
        doi_to_row[doi] = i

# Scan PDFs on disk, find orphans (not in merged inventory's pdf_path)
pdfs_on_disk = {f.name: f for f in pdf_dir.glob('*.pdf')}
pdf_paths_in_inv = set()
for r in merged:
    if r.get('pdf_path', ''):
        pdf_paths_in_inv.add(Path(r['pdf_path']).name)

orphans = {name: f for name, f in pdfs_on_disk.items() if name not in pdf_paths_in_inv}
print(f'Orphan PDFs on disk: {len(orphans)}')

updated = 0
for name, f in orphans.items():
    eppi = name.split('_')[0]
    doi = eppi_to_doi.get(eppi)
    if doi and doi in doi_to_row:
        idx = doi_to_row[doi]
        merged[idx]['pdf_path'] = f'pdfs/{name}'
        merged[idx]['pdf_source'] = merged[idx].get('pdf_source', '') or 'orphan_matched'
        updated += 1
    else:
        print(f'  could not match: {name} (eppi={eppi})')

print(f'Updated {updated} rows')

with open(merged_path, 'w', newline='', encoding='utf-8-sig') as fout:
    w = csv.DictWriter(fout, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(merged)

# Final stats
has_pdf_path = sum(1 for r in merged if r.get('pdf_path', ''))
has_zotero_pdf = sum(1 for r in merged if r.get('has_pdf', '').lower() in ('true', '1', 'yes'))
need_attach = sum(1 for r in merged if r.get('pdf_path', '') and r.get('has_pdf', '').lower() not in ('true', '1', 'yes'))
total_with_pdf = sum(1 for r in merged if r.get('pdf_path', '') or r.get('has_pdf', '').lower() in ('true', '1', 'yes'))
print(f'With PDF path (local): {has_pdf_path}')
print(f'Zotero already has PDF: {has_zotero_pdf}')
print(f'Need Zotero attach: {need_attach}')
print(f'Total with PDF (local or Zotero): {total_with_pdf}')
print(f'Still missing PDF: {len(merged) - total_with_pdf}')
