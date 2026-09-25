# Install qa-audit in a virtual environment (docs/qa-audit-guide.md §2). Windows PowerShell 5.1 or PowerShell 7.
#
#   .\scripts\install.ps1                  # chromium only
#   .\scripts\install.ps1 -AllBrowsers     # chromium, firefox and webkit
#
# Then: .\.venv\Scripts\qa-audit https://example.com   (or activate: .\.venv\Scripts\Activate.ps1)
param([switch]$AllBrowsers)
$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)
$python = if ($env:PYTHON) { $env:PYTHON } else { "python" }
& $python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)"
if ($LASTEXITCODE -ne 0) { throw "Python 3.12 or newer is required (set `$env:PYTHON)" }

if (-not (Test-Path .venv)) { & $python -m venv .venv }
$venvPython = Join-Path ".venv" "Scripts\python.exe"
& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet -e ".[visual]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
$browsers = if ($AllBrowsers) { @("chromium", "firefox", "webkit") } else { @("chromium") }
& $venvPython -m playwright install $browsers
if ($LASTEXITCODE -ne 0) { throw "browser install failed" }
& (Join-Path ".venv" "Scripts\qa-audit.exe") doctor
Write-Output ""
Write-Output "Installed. Run: .\.venv\Scripts\qa-audit https://example.com   (only sites you are authorized to test)"
