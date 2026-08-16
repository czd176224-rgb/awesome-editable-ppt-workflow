param(
    [switch]$ForceRuntime,
    [string]$RuntimeRoot,
    [string]$BinDir,
    [string]$ReceiptPath
)

$ErrorActionPreference = "Stop"
$Arguments = @{ Update = $true }
if ($ForceRuntime) { $Arguments.ForceRuntime = $true }
if ($RuntimeRoot) { $Arguments.RuntimeRoot = $RuntimeRoot }
if ($BinDir) { $Arguments.BinDir = $BinDir }
if ($ReceiptPath) { $Arguments.ReceiptPath = $ReceiptPath }
& (Join-Path $PSScriptRoot "install.ps1") @Arguments
if ($LASTEXITCODE -ne 0) { throw "Awesome Editable PPT Workflow update failed." }
Write-Output "Update complete. Restart Codex and start a new task to load the updated plugin."
