# run_ris_v19.ps1 — Run v1.9 orchestrator on the full RIS corpus with auto-restart.
# The orchestrator is resumable: it appends to --out and skips record_ids already present.
# This wrapper loops: if the orchestrator exits (crash, API error, timeout), it restarts
# after 30 seconds. When all 29251 records are done, it automatically:
#   Stage 2: runs the Gemini 2.5 Pro tie-breaker on all model disagreements
#   Stage 3: produces a human_review_3way.csv of unresolved 3-way splits

$ErrorActionPreference = "Continue"
Set-Location "C:\Users\LucasSempe\OneDrive - International Initiative for Impact Evaluation\Desktop\mhaa_screening"

$out = "projects\strongminds\data\output\results_ris_v19.jsonl"
$tbOut = "projects\strongminds\data\output\results_ris_v19_tiebreak.jsonl"
$reviewCsv = "projects\strongminds\data\output\human_review_3way.csv"
$log = "projects\strongminds\data\output\ris_run.log"
$total = 29251
$restarts = 0

# Ensure output dir exists
$outDir = Split-Path $out
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }

function Get-DoneCount {
    if (Test-Path $out) {
        return (Get-Content $out | Measure-Object).Count
    }
    return 0
}

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content $log "[$ts] $msg"
}

# ============================================================
# STAGE 1: Orchestrator (v1.9, k=1, temp 0, 2 models, no critic)
# ============================================================
Log "=== STAGE 1 START: Orchestrator screening ==="

while ($true) {
    $done = Get-DoneCount

    if ($done -ge $total) {
        Log "STAGE 1 COMPLETE: $done / $total records. Restarts: $restarts"
        break
    }

    $pct = [math]::Round($done / $total * 100, 1)
    Log "Starting orchestrator ($done / $total = $pct% done, restart #$restarts)"

    python pipeline/orchestrator.py `
        --prompt projects/strongminds/prompts/ulcm-orchestrator-prompts-v1.9.md `
        --records projects/strongminds/data/ris_records.jsonl `
        --out $out `
        --k 1 --temperature 0 `
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
    --records projects/strongminds/data/ris_records.jsonl `
    --prompt  projects/strongminds/prompts/ulcm-orchestrator-prompts-v1.9.md `
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

python -c "
import json, csv, sys
records = {}
for line in open('projects/strongminds/data/ris_records.jsonl', encoding='utf-8'):
    if line.strip():
        r = json.loads(line)
        records[str(r['record_id'])] = r

rows = []
for line in open('$tbOut', encoding='utf-8'):
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
            'abstract': rec.get('abstract', '')[:500],
            'votes': str(votes),
            'vote_share': r.get('vote_share_include', 0),
            'screening_code': r.get('screening_code', ''),
            'tiebreaker_explanation': next((run.get('explanation','') for run in r.get('runs',[]) if run.get('_role')=='tiebreaker'), '')[:300],
            'human_decision': '',
            'human_notes': ''
        })

with open('$reviewCsv', 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=['record_id','title','year','abstract','votes','vote_share','screening_code','tiebreaker_explanation','human_decision','human_notes'])
    w.writeheader()
    w.writerows(rows)

print(f'Wrote {len(rows)} records for human review to $reviewCsv')
" 2>&1 | ForEach-Object {
    Log "STAGE 3: $_"
}

Log "STAGE 3 COMPLETE: Human review CSV at $reviewCsv"
Log "=== ALL STAGES COMPLETE ==="
