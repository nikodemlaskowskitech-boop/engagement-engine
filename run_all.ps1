# One command for the whole pipeline: get the latest code, run every step in
# order, and save all the output to data\last_run.txt (Claude reads it from
# there, so nothing needs pasting).
#
#   .\run_all.ps1                 pull + Slack clicks from AWS, cockpit decisions, flags, checks, journeys, reports, views
#   .\run_all.ps1 -Regenerate     ...starting with fresh fake data (wipes the action log)
#   .\run_all.ps1 -Actions        ...and post the new tasks to Slack at the end
#
# The Slack listener runs in AWS. Its clicks come down first (pull_clicks.py:
# skipped with a note if you're not signed in to AWS, never a failure). The
# laptop listener (python listener.py) is the fallback, in its own window.

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

# 2. The pipeline: one list in settings.toml [pipeline], the same one the AWS engine runs
# (ARCHITECTURE.md fix B). settings.py prints this laptop's steps, one per line; an
# optional step (its failure is a warning, not a stop) starts with "?".
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"   # the project's own Python
$flags = @()
if ($Regenerate) { $flags += "regenerate" }
if ($Actions) { $flags += "actions" }
$steps = @(); $optional = @()
foreach ($line in (& $python settings.py --steps laptop @flags)) {
    if ($line.StartsWith("?")) { $line = $line.Substring(1); $optional += $line }
    if ($line) { $steps += $line }
}
if ($LASTEXITCODE -ne 0 -or $steps.Count -eq 0) {
    Write-Host "Couldn't read the pipeline from settings.toml [pipeline]" -ForegroundColor Red
    "FAILED: reading the pipeline from settings.toml" | Out-File $log -Append -Encoding utf8
    exit 1
}

$ok = 0
foreach ($step in $steps) {
    "`n===== python $step =====" | Out-File $log -Append -Encoding utf8
    Write-Host "Running $step ..."
    $parts = $step -split ' '                  # a step may carry arguments
    & $python @parts 2>&1 | ForEach-Object { "$_" } | Where-Object { $_ -ne "System.Management.Automation.RemoteException" } |
        Out-File $log -Append -Encoding utf8
    if ($LASTEXITCODE -ne 0 -and $optional -contains $step) {
        Write-Host "$step had a problem (see data\last_run.txt); carrying on." -ForegroundColor Yellow
        "WARNING: $step" | Out-File $log -Append -Encoding utf8
    } elseif ($LASTEXITCODE -ne 0) {
        Write-Host "$step failed. The details are in data\last_run.txt" -ForegroundColor Red
        "FAILED: $step" | Out-File $log -Append -Encoding utf8
        exit 1
    }
    $ok++
}
"`nDONE: $ok of $($steps.Count) steps" | Out-File $log -Append -Encoding utf8
Write-Host "Done: $ok of $($steps.Count) steps. Output saved to data\last_run.txt" -ForegroundColor Green
