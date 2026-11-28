# The whole deploy, one command (28 Nov 2026), with every gate in it:
#   1. the latest code (git pull)
#   2. tests\infra_test.py: any FAIL stops here, before anything changes in AWS
#   3. cdk deploy (it asks before any new permission: answer y)
#   4. security_check.py: AWS itself answers what each function may do; any FAIL is reported
#   5. after_deploy.py: the engine runs now on the new code, and its result, time and memory
#      are printed (and saved for Claude in data\aws_outputs\run.json)
# Sign in first: aws login --profile cairnpath. Run from the repo root: .\deploy.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
& .venv\Scripts\Activate.ps1

function Step($title) { Write-Host "`n===== $title =====" -ForegroundColor Cyan }

Step "1/5 the latest code"
git pull
if ($LASTEXITCODE -ne 0) { Write-Host "git pull failed: nothing deployed." -ForegroundColor Red; exit 1 }

Step "2/5 the infrastructure test"
python tests\infra_test.py
if ($LASTEXITCODE -ne 0) { Write-Host "infra_test.py has a FAIL: nothing deployed. Paste the FAIL lines to Claude." -ForegroundColor Red; exit 1 }

Step "3/5 cdk deploy"
Push-Location infra
cdk deploy --profile cairnpath
$deployed = $LASTEXITCODE
Pop-Location
if ($deployed -ne 0) { Write-Host "cdk deploy failed or was declined." -ForegroundColor Red; exit 1 }

Step "4/5 the live security check"
python security_check.py
if ($LASTEXITCODE -ne 0) { Write-Host "security_check.py has a FAIL: tell Claude before anything else." -ForegroundColor Red; exit 1 }

Step "5/5 a run on the new code"
python after_deploy.py
if ($LASTEXITCODE -ne 0) { Write-Host "The run after the deploy failed: tell Claude (data\aws_outputs\run.json)." -ForegroundColor Red; exit 1 }
Write-Host "`nDeployed, checked and run." -ForegroundColor Green
