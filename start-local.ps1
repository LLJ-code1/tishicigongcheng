param(
    [int]$StudioPort = 57913,
    [int]$AnimaDexPort = 5000,
    [string]$RuntimeRoot = "",
    [switch]$SkipLlm
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$runtimeRoot = if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
    $root
} else {
    [IO.Path]::GetFullPath($RuntimeRoot)
}
$python = Join-Path $runtimeRoot "AnimaDex\.venv\Scripts\python.exe"
$animaDexRoot = Join-Path $runtimeRoot "AnimaDex"
$studioUrl = "http://127.0.0.1:$StudioPort/"

if (-not (Test-Path -LiteralPath $python)) {
    throw "AnimaDex is not installed. Run install.bat in the AnimaDex directory first."
}

function Test-LocalPort([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Get-Sha256Hex([byte[]]$Bytes) {
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($sha256.ComputeHash($Bytes)).Replace("-", "")
    } finally {
        $sha256.Dispose()
    }
}

function Test-StudioSourceMatches {
    $client = New-Object Net.WebClient
    try {
        $served = $client.DownloadData("${studioUrl}app.js")
        $expected = [IO.File]::ReadAllBytes((Join-Path $root "prototype\app.js"))
        return (Get-Sha256Hex $served) -eq (Get-Sha256Hex $expected)
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

if ((Test-LocalPort $StudioPort) -and -not (Test-StudioSourceMatches)) {
    $listeners = @(Get-NetTCPConnection -LocalPort $StudioPort -State Listen -ErrorAction SilentlyContinue)
    $processIds = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($processIds.Count -ne 1) {
        throw "Port $StudioPort is occupied by an unexpected listener set. Stop it manually before starting Prompt Studio."
    }
    $listenerProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($processIds[0])"
    if (-not $listenerProcess -or
        $listenerProcess.Name -notin @("python.exe", "python") -or
        $listenerProcess.CommandLine -notmatch '(^|[\\/\s"])server\.py([\s"]|$)') {
        throw "Port $StudioPort is not serving this source tree and is not a recognized Prompt Studio process."
    }
    Stop-Process -Id $processIds[0] -Force
    $stopDeadline = (Get-Date).AddSeconds(10)
    while ((Test-LocalPort $StudioPort) -and (Get-Date) -lt $stopDeadline) {
        Start-Sleep -Milliseconds 200
    }
    if (Test-LocalPort $StudioPort) {
        throw "The stale Prompt Studio process did not release port $StudioPort."
    }
}

if (-not (Test-LocalPort $AnimaDexPort)) {
    $animaDexProcess = @{
        FilePath = $python
        ArgumentList = @("-m", "animadex", "serve")
        WorkingDirectory = $animaDexRoot
        WindowStyle = "Hidden"
    }
    Start-Process @animaDexProcess | Out-Null
}

if (-not (Test-LocalPort $StudioPort)) {
    $env:PROMPT_STUDIO_PORT = $StudioPort
    $env:ANIMADEX_URL = "http://127.0.0.1:$AnimaDexPort"
    if ([string]::IsNullOrWhiteSpace($env:PROMPT_STUDIO_DB)) {
        $env:PROMPT_STUDIO_DB = Join-Path $root "prototype\data\prompt_studio.db"
    }
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
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $runtimeRoot "scripts\start_local_llm.ps1")) `
        -WorkingDirectory $runtimeRoot `
        -WindowStyle Hidden | Out-Null
}

Start-Sleep -Milliseconds 800
Write-Output "Prompt Studio: $studioUrl"
Write-Output "AnimaDex:     http://127.0.0.1:$AnimaDexPort/"
Write-Output "Local LLM:    http://127.0.0.1:8080/v1"
