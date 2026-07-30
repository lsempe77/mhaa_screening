# run_fts_recovered.ps1 — FTS screening of the 1,066 RECOVERED determinants/measurement PDFs
# (the ones recovered by the RIS determinants correction). Same config as run_fts.ps1:
# router ON (v1.9-fts), k=1 temp 0, Claude+GLM panel, Gemini tie-break, human-review CSV.
# Resumable + auto-restart. Output merges with the original FTS run downstream.

$ErrorActionPreference = "Continue"
Set-Location "C:\Users\LucasSempe\OneDrive - International Initiative for Impact Evaluation\Desktop\mhaa_screening"

$prompt    = "projects\strongminds\prompts\ulcm-orchestrator-prompts-v1.9-fts.md"
$records   = "projects\strongminds\data\fts_recovered\records_fts_1066.jsonl"
$out       = "projects\strongminds\data\output\results_fts_recovered_1066.jsonl"
$tbOut     = "projects\strongminds\data\output\results_fts_recovered_tiebreak.jsonl"
$reviewCsv = "projects\strongminds\data\output\fts_recovered_human_review_3way.csv"
$log       = "projects\strongminds\data\output\fts_recovered_run.log"
$total     = 1066
$restarts  = 0

function Get-DoneCount { if (Test-Path $out) { return (Get-Content $out | Measure-Object).Count } return 0 }
function Log($m) { Add-Content $log "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $m" }

# ---- STAGE 1: orchestrator screening (router ON) ----
Log "=== STAGE 1 START: recovered FTS screening (router ON) ==="
while ($true) {
    $done = Get-DoneCount
    if ($done -ge $total) { Log "STAGE 1 COMPLETE: $done / $total (restarts $restarts)"; break }
    Log "Starting orchestrator ($done / $total done, restart #$restarts)"
    python pipeline/orchestrator.py `
        --prompt $prompt `
        --records $records `
        --out $out `
        --k 1 --temperature 0 `
        --router-model openai/gpt-4o-mini `
        --models anthropic/claude-sonnet-4 z-ai/glm-5.2 `
        --uncertainty-band 0.4 0.6 `
        --workers 8 2>&1 | ForEach-Object {
        if ($_ -match '\[(\d+)\]') { Add-Content $log "[$(Get-Date -Format 'HH:mm:ss')] $_" }
    }
    $done = Get-DoneCount
    if ($done -ge $total) { Log "STAGE 1 COMPLETE: $done / $total (restarts $restarts)"; break }
    $restarts++; Log "exited early; waiting 30s..."; Start-Sleep 30
}

# ---- STAGE 2: Gemini 2.5 Pro tie-breaker ----
Log "=== STAGE 2 START: Gemini tie-breaker ==="
python projects/strongminds/scripts/tiebreak_ris.py `
    --results $out `
    --records $records `
    --prompt  $prompt `
    --model   google/gemini-2.5-pro `
    --out     $tbOut `
    --workers 8 --resume 2>&1 | ForEach-Object { Add-Content $log "[$(Get-Date -Format 'HH:mm:ss')] [TIEBREAK] $_" }
Log "STAGE 2 COMPLETE: $tbOut"

# ---- STAGE 3: human-review CSV of unresolved 3-way splits ----
Log "=== STAGE 3 START: human-review CSV ==="
$env:FTS_RECORDS = $records; $env:FTS_TBOUT = $tbOut; $env:FTS_REVIEW = $reviewCsv
python -c @"
import json, csv, os
records = {}
for line in open(os.environ['FTS_RECORDS'], encoding='utf-8'):
    if line.strip():
        r = json.loads(line); records[str(r['record_id'])] = r
rows = []
for line in open(os.environ['FTS_TBOUT'], encoding='utf-8'):
    if not line.strip(): continue
    r = json.loads(line)
    if r.get('_tiebreaker_applied') and r.get('needs_second_opinion'):
        rid = str(r['record_id']); rec = records.get(rid, {})
        rows.append({'record_id': rid, 'title': rec.get('title','')[:200], 'year': rec.get('year',''),
            'pdf_file': rec.get('pdf_file',''), 'n_pages': rec.get('n_pages',''),
            'votes': str(r.get('_votes', [])), 'vote_share': r.get('vote_share_include', 0),
            'screening_code': r.get('screening_code',''),
            'tiebreaker_explanation': next((run.get('explanation','') for run in r.get('runs',[]) if run.get('_role')=='tiebreaker'), '')[:300],
            'human_decision': '', 'human_notes': ''})
with open(os.environ['FTS_REVIEW'], 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=['record_id','title','year','pdf_file','n_pages','votes','vote_share','screening_code','tiebreaker_explanation','human_decision','human_notes'])
    w.writeheader(); w.writerows(rows)
print(f'Wrote {len(rows)} records for human review to {os.environ[\"FTS_REVIEW\"]}')
"@ 2>&1 | ForEach-Object { Log "STAGE 3: $_" }
Log "=== ALL STAGES COMPLETE ==="
