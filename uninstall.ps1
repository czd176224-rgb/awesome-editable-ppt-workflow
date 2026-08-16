param(
    [switch]$RemoveRuntime,
    [switch]$RemoveMarketplace,
    [string]$RuntimeRoot,
    [string]$ReceiptPath
)

$ErrorActionPreference = "Stop"
$RepoRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$PluginRoot = Join-Path $RepoRoot "plugins\awesome-editable-ppt-workflow"
$PackageInfo = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $RepoRoot "package-info.json") | ConvertFrom-Json
$MarketplaceName = [string]$PackageInfo.marketplace
$PluginName = [string]$PackageInfo.plugin
$ReceiptBase = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" }
if (-not $ReceiptPath) { $ReceiptPath = Join-Path $ReceiptBase "plugin-install-receipts\awesome-editable-ppt-workflow.json" }
$ReceiptPath = [System.IO.Path]::GetFullPath($ReceiptPath)
$ManagedReceipt = $null
if (Test-Path -LiteralPath $ReceiptPath -PathType Leaf) {
    try { $ManagedReceipt = Get-Content -Raw -Encoding UTF8 -LiteralPath $ReceiptPath | ConvertFrom-Json }
    catch { throw "The install receipt is unreadable; refusing to delete or modify it: $ReceiptPath" }
    if ($ManagedReceipt.schemaVersion -ne "editable-ppt-install-receipt-v1" -or
        $ManagedReceipt.plugin -ne $PluginName -or
        $ManagedReceipt.marketplace -ne $MarketplaceName -or
        -not $ManagedReceipt.repository -or -not $ManagedReceipt.releaseTag -or
        -not $ManagedReceipt.pluginVersion) {
        throw "The install receipt does not match this plugin/Marketplace and will not be deleted: $ReceiptPath"
    }
}

$Codex = $null
$CodexRoots = @(
    (Join-Path $env:USERPROFILE ".codex"),
    (Join-Path $env:LOCALAPPDATA "Codex"),
    (Join-Path $env:LOCALAPPDATA "OpenAI\Codex")
)
foreach ($CodexRoot in $CodexRoots) {
    $ReleaseRoot = Join-Path $CodexRoot "packages\standalone\releases"
    if (Test-Path -LiteralPath $ReleaseRoot -PathType Container) {
        $Codex = Get-ChildItem -LiteralPath $ReleaseRoot -Filter codex.exe -File -Recurse |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($Codex) { break }
    }
}
if (-not $Codex) { $Codex = Get-Command codex -ErrorAction SilentlyContinue }
if (-not $Codex) { throw "Codex CLI was not found." }
$CodexPath = if ($Codex.Source) { $Codex.Source } else { $Codex.FullName }

& $CodexPath plugin remove "$PluginName@$MarketplaceName"
if ($LASTEXITCODE -ne 0) { throw "Unable to remove $PluginName from $MarketplaceName." }

if ($RemoveMarketplace) {
    & $CodexPath plugin marketplace remove $MarketplaceName
    if ($LASTEXITCODE -ne 0) { throw "Unable to remove Marketplace $MarketplaceName." }
}

if ($RemoveRuntime) {
    if (-not $RuntimeRoot) {
        $RuntimeRoot = Join-Path $env:USERPROFILE ".codex\plugin-runtimes\awesome-editable-ppt-workflow-fixed-canvas-cm-v2"
    }
    $RuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
    . (Join-Path $PluginRoot "scripts\runtime_root_safety.ps1")
    $DefaultRuntimeRoot = Join-Path $env:USERPROFILE ".codex\plugin-runtimes\awesome-editable-ppt-workflow-fixed-canvas-cm-v2"
    Assert-RuntimeRootLocation -RuntimeRoot $RuntimeRoot -DefaultRuntimeRoot $DefaultRuntimeRoot -PluginRoot $PluginRoot
    if (Test-Path -LiteralPath $RuntimeRoot -PathType Container) {
        if (-not (Test-RuntimeOwnershipSentinel $RuntimeRoot)) {
            throw "Refusing to remove a runtime without the awesome-editable-ppt-workflow ownership sentinel: $RuntimeRoot"
        }
        [System.IO.Directory]::Delete($RuntimeRoot, $true)
        Write-Output "Removed isolated runtime: $RuntimeRoot"
    }
}

if ($ManagedReceipt) {
    if ($RemoveMarketplace) {
        $ReceiptTombstone = "$ReceiptPath.removed-$([guid]::NewGuid().ToString('N'))"
        Move-Item -LiteralPath $ReceiptPath -Destination $ReceiptTombstone
        try { Remove-Item -LiteralPath $ReceiptTombstone -Force }
        catch { Write-Warning "The verified receipt was atomically detached but its tombstone could not be removed: $ReceiptTombstone" }
        Write-Output "Removed verified install receipt: $ReceiptPath"
    } else {
        Write-Output "Verified install receipt preserved because Marketplace removal was not requested."
    }
}

Write-Output "Plugin removal complete. User-created Word and PPT project folders were not searched or modified."
