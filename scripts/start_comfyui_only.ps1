param(
    [int]$Port = 8191,
    [switch]$SkipBrowser
)

$ErrorActionPreference = "Stop"
$pythonw = "H:\conmfui\ComfyUI-aki(1)\ComfyUI-aki-v3\ComfyUI-aki-v3\python\pythonw.exe"
$starter = "H:\comyfui\start_blackbeast_comfyui.py"
$workingDirectory = "H:\comyfui"
$url = "http://127.0.0.1:$Port"
$healthUrl = "$url/system_stats"

function Test-ComfyUi {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 3
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

if (-not (Test-ComfyUi)) {
    if (-not (Test-Path -LiteralPath $pythonw)) {
        throw "ComfyUI Python runtime was not found: $pythonw"
    }
    if (-not (Test-Path -LiteralPath $starter)) {
        throw "ComfyUI starter was not found: $starter"
    }
    Start-Process `
        -FilePath $pythonw `
        -ArgumentList @($starter) `
        -WorkingDirectory $workingDirectory `
        -WindowStyle Hidden | Out-Null

    $deadline = (Get-Date).AddMinutes(4)
    do {
        Start-Sleep -Seconds 2
        if (Test-ComfyUi) {
            break
        }
    } while ((Get-Date) -lt $deadline)
}

if (-not (Test-ComfyUi)) {
    throw "ComfyUI startup timed out. Check H:\comyfui\logs."
}

if (-not $SkipBrowser) {
    Start-Process $url | Out-Null
}

Write-Output "ComfyUI: $url"
