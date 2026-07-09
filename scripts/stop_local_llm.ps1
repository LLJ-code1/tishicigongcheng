$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$python = Join-Path $root "AnimaDex\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Project Python runtime was not found: $python"
}
& $python (Join-Path $root "prototype\local_llm.py") stop
exit $LASTEXITCODE
