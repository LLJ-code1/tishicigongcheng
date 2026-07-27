param(
    [int]$StudioPort = 57913,
    [int]$AnimaDexPort = 5000,
    [string]$RuntimeRoot = "",
    [switch]$SkipBrowser
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$starter = Join-Path $root "start-local.ps1"
$studioUrl = "http://127.0.0.1:$StudioPort/"

if (-not (Test-Path -LiteralPath $starter)) {
    throw "Prompt Studio starter was not found: $starter"
}

& $starter `
    -StudioPort $StudioPort `
    -AnimaDexPort $AnimaDexPort `
    -RuntimeRoot $RuntimeRoot

$deadline = (Get-Date).AddMinutes(3)
$ready = $false
do {
    Start-Sleep -Seconds 2
    try {
        $status = Invoke-RestMethod `
            -Uri "http://127.0.0.1:$StudioPort/api/local-llm/status" `
            -TimeoutSec 3
        $ready = [bool]$status.item.running
    } catch {
        $ready = $false
    }
} while (-not $ready -and (Get-Date) -lt $deadline)

if (-not $ready) {
    throw "Prompt Studio or the local LLM did not become ready in time."
}

if (-not $SkipBrowser) {
    Start-Process $studioUrl | Out-Null
}

Write-Output "Prompt Studio: $studioUrl"
