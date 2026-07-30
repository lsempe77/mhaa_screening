# run_fts.ps1 — Run the v1.9-fts orchestrator on the 2721 full-text records with auto-restart.
# Mirrors run_ris_v19.ps1 but: (a) screens FULL TEXT (records_fts_2721.jsonl), (b) router is ON
# (the v1.9-fts prompt routes each record to the intervention / no_intervention screener).
#
# The orchestrator is resumable: it appends to --out and skips record_ids already present, so
# this wrapper simply restarts it (after 30s) whenever it exits early on a crash/API error.
# When all 2721 records are done it runs:
#   Stage 2: Gemini 2.5 Pro tie-breaker on all model disagreements
#   Stage 3: a human_review CSV of unresolved 3-way splits

$ErrorActionPreference = "Continue"
Set-Location "C:\Users\LucasSempe\OneDrive - International Initiative for Impact Evaluation\Desktop\mhaa_screening"

$prompt    = "projects\strongminds\prompts\ulcm-orchestrator-prompts-v1.9-fts.md"
$records   = "projects\strongminds\data\fts\records_fts_2721.jsonl"
$out       = "projects\strongminds\data\output\results_fts_v19_2721.jsonl"
$tbOut     = "projects\strongminds\data\output\results_fts_v19_tiebreak.jsonl"
$reviewCsv = "projects\strongminds\data\output\fts_human_review_3way.csv"
$log       = "projects\strongminds\data\output\fts_run.log"
$total     = 2721
$restarts  = 0

$outDir = Split-Path $out
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }

function Get-DoneCount {
    if (Test-Path $out) { return (Get-Content $out | Measure-Object).Count }
    return 0
}

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content $log "[$ts] $msg"
}

# ============================================================
# STAGE 1: Orchestrator (v1.9-fts, k=1, temp 0, 2 models, router ON, no critic)
# ============================================================
Log "=== STAGE 1 START: FTS orchestrator screening (router ON) ==="

while ($true) {
    $done = Get-DoneCount

    if ($done -ge $total) {
        Log "STAGE 1 COMPLETE: $done / $total records. Restarts: $restarts"
        break
    }

    $pct = [math]::Round($done / $total * 100, 1)
    Log "Starting orchestrator ($done / $total = $pct% done, restart #$restarts)"

    python pipeline/orchestrator.py `
        --prompt $prompt `
        --records $records `
        --out $out `
        --k 1 --temperature 0 `
        --router-model openai/gpt-4o-mini `
        --models anthropic/claude-sonnet-4 z-ai/glm-5.2 `
        --uncertainty-band 0.4 0.6 `
        --workers 8 2>&1 | ForEach-Object {
        $line = $_
        if ($line -match '\[(\d+)\]') {
            $ts2 = Get-Date -Format "HH:mm:ss"
            Add-Content $log "[$ts2] $line"
        }
    }

    $exitCode = $LASTEXITCODE
    $done = Get-DoneCount
    $pct = [math]::Round($done / $total * 100, 1)
    Log "Orchestrator exited (code=$exitCode, $done / $total = $pct% done)"

    if ($done -ge $total) {
        Log "STAGE 1 COMPLETE: $done / $total records. Restarts: $restarts"
        break
    }

    $restarts++
    Log "Waiting 30s before restart..."
    Start-Sleep 30
}

# ============================================================
# STAGE 2: Gemini 2.5 Pro tie-breaker on disagreements
# ============================================================
Log "=== STAGE 2 START: Gemini 2.5 Pro tie-breaker ==="

python projects/strongminds/scripts/tiebreak_ris.py `
    --results $out `
    --records $records `
    --prompt  $prompt `
    --model   google/gemini-2.5-pro `
    --out     $tbOut `
    --workers 8 --resume 2>&1 | ForEach-Object {
    $line = $_
    $ts2 = Get-Date -Format "HH:mm:ss"
    Add-Content $log "[$ts2] [TIEBREAK] $line"
}

Log "STAGE 2 COMPLETE: Tie-breaker finished. Output: $tbOut"

# ============================================================
# STAGE 3: Produce human-review CSV of unresolved 3-way splits
# ============================================================
Log "=== STAGE 3 START: Producing human-review CSV ==="

$env:FTS_RECORDS = $records
$env:FTS_TBOUT   = $tbOut
$env:FTS_REVIEW  = $reviewCsv

python -c @"
import json, csv, os
records = {}
for line in open(os.environ['FTS_RECORDS'], encoding='utf-8'):
    if line.strip():
        r = json.loads(line)
        records[str(r['record_id'])] = r

rows = []
for line in open(os.environ['FTS_TBOUT'], encoding='utf-8'):
    if not line.strip(): continue
    r = json.loads(line)
    if r.get('_tiebreaker_applied') and r.get('needs_second_opinion'):
        rid = str(r['record_id'])
        rec = records.get(rid, {})
        votes = r.get('_votes', [])
        rows.append({
            'record_id': rid,
            'title': rec.get('title', '')[:200],
            'year': rec.get('year', ''),
            'pdf_file': rec.get('pdf_file', ''),
            'n_pages': rec.get('n_pages', ''),
            'votes': str(votes),
            'vote_share': r.get('vote_share_include', 0),
            'screening_code': r.get('screening_code', ''),
            'tiebreaker_explanation': next((run.get('explanation','') for run in r.get('runs',[]) if run.get('_role')=='tiebreaker'), '')[:300],
            'human_decision': '',
            'human_notes': ''
        })

with open(os.environ['FTS_REVIEW'], 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=['record_id','title','year','pdf_file','n_pages','votes','vote_share','screening_code','tiebreaker_explanation','human_decision','human_notes'])
    w.writeheader()
    w.writerows(rows)

print(f'Wrote {len(rows)} records for human review to {os.environ[\"FTS_REVIEW\"]}')
"@ 2>&1 | ForEach-Object {
    Log "STAGE 3: $_"
}

Log "STAGE 3 COMPLETE: Human review CSV at $reviewCsv"
Log "=== ALL STAGES COMPLETE ==="
