param(
    [string]$ProjectRoot = "",
    [string]$PythonExe = "",
    [string]$ConfigPath = "",
    [string]$EpicsFile = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $ConfigPath = Join-Path $PSScriptRoot "config.ps1"
}

if ([string]::IsNullOrWhiteSpace($EpicsFile)) {
    $EpicsFile = Join-Path $PSScriptRoot "epic_keys.txt"
}

if (Test-Path $ConfigPath) {
    . $ConfigPath
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        $PythonExe = $venvPython
    } else {
        $PythonExe = "python"
    }
}

$runnerPath = Join-Path $PSScriptRoot "run_all_epics.py"
if (-not (Test-Path $runnerPath)) {
    throw "Runner script not found: $runnerPath"
}

if (-not (Test-Path $EpicsFile)) {
    throw "Epic list file not found: $EpicsFile"
}

$logsDir = Join-Path $ProjectRoot "task_scheduler_automation\logs"
New-Item -ItemType Directory -Path $logsDir -Force | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$env:RUN_LOG_FILE = Join-Path $logsDir "run_all_epics_$timestamp.log"
$consoleLog = Join-Path $logsDir "console_all_epics_$timestamp.log"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONWARNINGS = "ignore::DeprecationWarning,ignore::UserWarning"

Set-Location $ProjectRoot

Write-Host "Starting all-epics run at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "Epic list: $EpicsFile"
Write-Host "PythonExe: $PythonExe"
Write-Host "RUN_LOG_FILE: $env:RUN_LOG_FILE"

try {
    $oldErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $PythonExe $runnerPath --epics-file $EpicsFile *>&1 | Tee-Object -FilePath $consoleLog
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $oldErrorActionPreference
} catch {
    $errText = $_ | Out-String
    $errText | Tee-Object -FilePath $consoleLog -Append
    if ($null -ne $oldErrorActionPreference) {
        $ErrorActionPreference = $oldErrorActionPreference
    }
    $exitCode = 1
}

Write-Host "Finished all-epics run at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') with exit code $exitCode"

if ($exitCode -ne 0) {
    exit $exitCode
}
