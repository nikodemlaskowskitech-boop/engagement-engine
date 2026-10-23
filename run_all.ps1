# One command for the whole pipeline: get the latest code, run every step in
# order, and save all the output to data\last_run.txt (Claude reads it from
# there, so nothing needs pasting).
#
#   .\run_all.ps1                 pull + flags, checks, journeys, reports, views
#   .\run_all.ps1 -Regenerate     ...starting with fresh fake data (wipes the action log)
#   .\run_all.ps1 -Actions        ...and post the new tasks to Slack at the end
#
# The Slack listener isn't included: it keeps running, so it gets its own
# window (python listener.py).

param([switch]$Regenerate, [switch]$Actions, [switch]$Pulled)

Set-Location $PSScriptRoot                       # always run from the repo root
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8    # read Python's output as UTF-8 (table borders)
$log = Join-Path $PSScriptRoot "data\last_run.txt"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
if (-not $Pulled) { "Run started $(Get-Date -Format 'yyyy-MM-dd HH:mm')" | Out-File $log -Encoding utf8 }

# 1. The latest merged code
# (git writes progress to stderr; "$_" keeps just the text, not PowerShell's error wrapper)
if (-not $Pulled) {
    $before = (Get-FileHash $PSCommandPath).Hash
    git checkout -q main 2>&1 | ForEach-Object { "$_" } | Out-File $log -Append -Encoding utf8
    git pull 2>&1 | ForEach-Object { "$_" } | Out-File $log -Append -Encoding utf8
    # If the pull changed this script, PowerShell is still running the old copy:
    # start the new one instead (without pulling again) and stop here
    if ((Get-FileHash $PSCommandPath).Hash -ne $before) {
        "run_all.ps1 itself was updated: restarting with the new version" | Out-File $log -Append -Encoding utf8
        & $PSCommandPath -Regenerate:$Regenerate -Actions:$Actions -Pulled
        exit $LASTEXITCODE
    }
}
"Code at: $(git log --oneline -1)" | Out-File $log -Append -Encoding utf8

# 2. The pipeline, in the order the scripts depend on each other
$steps = @()
if ($Regenerate) { $steps += "generate_events.py" }
$steps += "flags.py", "check_flags.py", "backtest.py", "journeys.py", "team_report.py",
          "onboarding_report.py", "export_views.py"
if ($Actions) { $steps += "actions.py" }

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"   # the project's own Python
$ok = 0
foreach ($step in $steps) {
    "`n===== python $step =====" | Out-File $log -Append -Encoding utf8
    Write-Host "Running $step ..."
    & $python $step 2>&1 | ForEach-Object { "$_" } | Where-Object { $_ -ne "System.Management.Automation.RemoteException" } |
        Out-File $log -Append -Encoding utf8
    if ($LASTEXITCODE -ne 0) {
        Write-Host "$step failed. The details are in data\last_run.txt" -ForegroundColor Red
        "FAILED: $step" | Out-File $log -Append -Encoding utf8
        exit 1
    }
    $ok++
}
"`nDONE: $ok of $($steps.Count) steps" | Out-File $log -Append -Encoding utf8
Write-Host "Done: $ok of $($steps.Count) steps. Output saved to data\last_run.txt" -ForegroundColor Green
