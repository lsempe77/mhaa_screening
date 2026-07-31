# run_dex_full.ps1 — Full DEX extraction over the 2,670-record corpus.
# Resumable (run_dex skips completed non-error record_ids) + auto-restart on crash/API error.
# k=1 + tolerant quote-check + audit stamping; parallelised via --workers.

$ErrorActionPreference = "Continue"
Set-Location "C:\Users\LucasSempe\OneDrive - International Initiative for Impact Evaluation\Desktop\mhaa_screening"

$prompt  = "projects\strongminds\prompts\ulcm-extraction-prompt-v1.7.md"
$records = "projects\strongminds\data\extraction\records_extract_final_2670.jsonl"
$out     = "projects\strongminds\data\extraction\dex_full_2670.jsonl"
$log     = "projects\strongminds\data\extraction\dex_full.log"
$total   = 2670
$restarts = 0

function Get-DoneCount {
    if (-not (Test-Path $out)) { return 0 }
    $c = python -c "import json; ids=set(); [ids.add(str(json.loads(l)['record_id'])) for l in open(r'$out',encoding='utf-8') if l.strip() and not json.loads(l).get('_error')]; print(len(ids))"
    return [int]$c
}
function Log($m) { Add-Content $log "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $m" }

Log "=== START: DEX full extraction (Sonnet, k=1, workers 10) ==="
while ($true) {
    $done = Get-DoneCount
    if ($done -ge $total) { Log "COMPLETE: $done / $total (restarts $restarts)"; break }
    Log "starting run_dex ($done / $total done, restart #$restarts)"
    python pipeline/extraction/run_dex.py `
        --prompt $prompt `
        --records $records `
        --out $out `
        --extractor anthropic/claude-sonnet-4 `
        --k 1 --workers 10 2>&1 | ForEach-Object { Add-Content $log "[$(Get-Date -Format 'HH:mm:ss')] $_" }
    $done = Get-DoneCount
    if ($done -ge $total) { Log "COMPLETE: $done / $total (restarts $restarts)"; break }
    $restarts++
    if ($restarts -gt 40) { Log "STOP: too many restarts ($restarts) at $done / $total"; break }
    Log "exited early ($done / $total); waiting 30s before restart..."; Start-Sleep 30
}
Log "=== DONE ==="
