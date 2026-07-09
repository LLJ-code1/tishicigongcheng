param(
    [int]$StudioPort = 57913,
    [int]$AnimaDexPort = 5000,
    [switch]$SkipLlm
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root "AnimaDex\.venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "AnimaDex is not installed. Run install.bat in the AnimaDex directory first."
}

function Test-LocalPort([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

if (-not (Test-LocalPort $AnimaDexPort)) {
    $animaDexProcess = @{
        FilePath = $python
        ArgumentList = @("-m", "animadex", "serve")
        WorkingDirectory = (Join-Path $root "AnimaDex")
        WindowStyle = "Hidden"
    }
    Start-Process @animaDexProcess | Out-Null
}

if (-not (Test-LocalPort $StudioPort)) {
    $env:PROMPT_STUDIO_PORT = $StudioPort
    $env:ANIMADEX_URL = "http://127.0.0.1:$AnimaDexPort"
    $studioProcess = @{
        FilePath = $python
        ArgumentList = @("server.py")
        WorkingDirectory = (Join-Path $root "prototype")
        WindowStyle = "Hidden"
    }
    Start-Process @studioProcess | Out-Null
}

$llmExecutable = "H:\AI\apps\llama.cpp\b9442-cuda12.4\llama-server.exe"
$llmModel = "H:\AI\models\llm\Huihui-Qwen3-14B-abliterated-v2.Q4_K_M.gguf"
if (-not $SkipLlm -and
    (Test-Path -LiteralPath $llmExecutable) -and
    (Test-Path -LiteralPath $llmModel) -and
    -not (Test-LocalPort 8080)) {
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $root "scripts\start_local_llm.ps1")) `
        -WorkingDirectory $root `
        -WindowStyle Hidden | Out-Null
}

Start-Sleep -Milliseconds 800
Write-Output "Prompt Studio: http://127.0.0.1:$StudioPort/"
Write-Output "AnimaDex:     http://127.0.0.1:$AnimaDexPort/"
Write-Output "Local LLM:    http://127.0.0.1:8080/v1"
