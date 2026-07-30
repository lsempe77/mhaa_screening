# run_rescreen_eit.ps1 — Corrective re-screen of the 17,033 EXCLUDE_INTERVENTION_TOPIC records
# with the router ON (v1.9 TA prompt). The full RIS run used --no-router, which applied the
# intervention screener to determinants (RQ1) / measurement (RQ18) records and wrongly excluded
# them (EXCLUDE_INTERVENTION_TOPIC). Router ON re-routes them to the correct screener.
# Resumable (appends to --out, skips done record_ids); restarts on crash after 30s.

$ErrorActionPreference = "Continue"
Set-Location "C:\Users\LucasSempe\OneDrive - International Initiative for Impact Evaluation\Desktop\mhaa_screening"

$prompt  = "projects\strongminds\prompts\ulcm-orchestrator-prompts-v1.9.md"
$records = "projects\strongminds\data\rescreen_eit_records.jsonl"
$out     = "projects\strongminds\data\output\rescreen_eit_results.jsonl"
$log     = "projects\strongminds\data\output\rescreen_eit.log"
$total   = 17033
$restarts = 0

function Get-DoneCount { if (Test-Path $out) { return (Get-Content $out | Measure-Object).Count } return 0 }
function Log($m) { Add-Content $log "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $m" }

Log "=== START: EIT corrective re-screen (router ON) ==="
while ($true) {
    $done = Get-DoneCount
    if ($done -ge $total) { Log "COMPLETE: $done / $total (restarts $restarts)"; break }
    Log "Starting ($done / $total done, restart #$restarts)"
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
    if ($done -ge $total) { Log "COMPLETE: $done / $total (restarts $restarts)"; break }
    $restarts++; Log "exited early; waiting 30s before restart..."; Start-Sleep 30
}
Log "=== DONE ==="
