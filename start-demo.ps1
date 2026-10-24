$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    & '.\.venv\Scripts\python.exe' -m pip install -e '.[dev]'
    & '.\.venv\Scripts\python.exe' -m playwright install chromium
}
Start-Process 'http://127.0.0.1:8765'
& '.\.venv\Scripts\python.exe' -m regulatory_agent demo
