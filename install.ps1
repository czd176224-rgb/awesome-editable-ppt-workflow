param(
    [switch]$ForceRuntime,
    [string]$RuntimeRoot,
    [string]$BinDir,
    [switch]$Repair,
    [switch]$Update,
    [string]$ReceiptPath
)

$ErrorActionPreference = "Stop"
$RepoRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$PluginRoot = Join-Path $RepoRoot "plugins\awesome-editable-ppt-workflow"
$PluginName = "awesome-editable-ppt-workflow"
$Manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $PluginRoot ".codex-plugin\plugin.json") | ConvertFrom-Json
$PackageInfo = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $RepoRoot "package-info.json") | ConvertFrom-Json
$MarketplaceSource = [string]$PackageInfo.repository
$MarketplaceName = [string]$PackageInfo.marketplace
$ReleaseTag = [string]$PackageInfo.releaseTag

if (-not $MarketplaceSource -or -not $MarketplaceName -or $ReleaseTag -ne ("v" + [string]$PackageInfo.pluginVersion)) {
    throw "package-info.json must declare repository, marketplace, and an exact matching releaseTag."
}
if ($Manifest.version -ne $PackageInfo.pluginVersion -or $PackageInfo.workflowContractVersion -ne "awesome-word-ppt-workflow-v1") {
    throw "Local plugin metadata is inconsistent with the current awesome-word-ppt-workflow-v1 contract."
}
if (-not $IsWindows -and $PSVersionTable.PSEdition -eq "Core") {
    throw "This installer currently supports Windows only."
}

$ReceiptBase = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" }
if (-not $ReceiptPath) { $ReceiptPath = Join-Path $ReceiptBase "plugin-install-receipts\awesome-editable-ppt-workflow.json" }
$ReceiptPath = [System.IO.Path]::GetFullPath($ReceiptPath)
$PreviousReceipt = $null
if (Test-Path -LiteralPath $ReceiptPath -PathType Leaf) {
    $PreviousReceipt = Get-Content -Raw -Encoding UTF8 -LiteralPath $ReceiptPath | ConvertFrom-Json
    if ($PreviousReceipt.schemaVersion -ne "editable-ppt-install-receipt-v1" -or
        $PreviousReceipt.plugin -ne $PluginName -or -not $PreviousReceipt.releaseTag -or
        -not $PreviousReceipt.pluginVersion -or -not $PreviousReceipt.repository -or
        -not $PreviousReceipt.marketplace) {
        throw "The existing Editable PPT install receipt is invalid; refusing to modify registration."
    }
}
$TransactionPath = "$ReceiptPath.transaction.json"
if (Test-Path -LiteralPath $TransactionPath -PathType Leaf) {
    throw "RECOVERY-REQUIRED: an unfinished install transaction exists at $TransactionPath. Resolve it before retrying."
}

function Write-InstallTransaction([string]$Status, [string]$Detail) {
    $Directory = Split-Path -Parent $TransactionPath
    New-Item -ItemType Directory -Force -Path $Directory | Out-Null
    $Temporary = "$TransactionPath.tmp-$([guid]::NewGuid().ToString('N'))"
    try {
        [ordered]@{
            schemaVersion = "editable-ppt-install-transaction-v1"
            status = $Status
            detail = $Detail
            previous = $PreviousReceipt
            desired = [ordered]@{
                plugin = $PluginName
                marketplace = $MarketplaceName
                repository = $MarketplaceSource
                releaseTag = $ReleaseTag
                pluginVersion = [string]$PackageInfo.pluginVersion
            }
            updatedAtUtc = [DateTime]::UtcNow.ToString("o")
        } | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $Temporary -Encoding utf8
        Move-Item -LiteralPath $Temporary -Destination $TransactionPath -Force
    } finally {
        if (Test-Path -LiteralPath $Temporary) { Remove-Item -LiteralPath $Temporary -Force }
    }
}

if ($Repair -and $Update) { throw "-Repair and -Update are mutually exclusive." }
if ($Repair) {
    if (-not $PreviousReceipt) { throw "Repair requires an existing verified install receipt." }
    if ($PreviousReceipt.releaseTag -ne $ReleaseTag -or
        $PreviousReceipt.pluginVersion -ne $PackageInfo.pluginVersion -or
        $PreviousReceipt.repository -ne $MarketplaceSource -or
        $PreviousReceipt.marketplace -ne $MarketplaceName) {
        throw "Repair is allowed only for the exact release recorded in the verified receipt."
    }
} elseif ($Update) {
    if (-not $PreviousReceipt) { throw "Update requires an existing verified install receipt; run install.ps1 first." }
    try {
        $InstalledVersion = [version]([string]$PreviousReceipt.pluginVersion)
        $TargetVersion = [version]([string]$PackageInfo.pluginVersion)
    } catch { throw "Install receipt and target plugin versions must be semantic numeric versions." }
    if ($TargetVersion -le $InstalledVersion) {
        throw "Update requires a strictly higher version than $($PreviousReceipt.pluginVersion); same-version updates and downgrades are forbidden."
    }
} elseif ($PreviousReceipt) {
    if ($PreviousReceipt.releaseTag -eq $ReleaseTag -and $PreviousReceipt.pluginVersion -eq $PackageInfo.pluginVersion) {
        throw "Editable PPT Workflow $ReleaseTag is already installed. Use install.ps1 -Repair for an explicit same-tag repair."
    }
    throw "A managed installation already exists. Use update.ps1 with a strictly higher immutable tag."
}

$Codex = $null
$CodexRoots = @()
if ($env:CODEX_HOME) { $CodexRoots += $env:CODEX_HOME }
$CodexRoots += @(
    (Join-Path $env:USERPROFILE ".codex"),
    (Join-Path $env:LOCALAPPDATA "Codex"),
    (Join-Path $env:LOCALAPPDATA "OpenAI\Codex")
)
foreach ($CodexRoot in ($CodexRoots | Select-Object -Unique)) {
    $ReleaseRoot = Join-Path $CodexRoot "packages\standalone\releases"
    if (Test-Path -LiteralPath $ReleaseRoot -PathType Container) {
        $Codex = Get-ChildItem -LiteralPath $ReleaseRoot -Filter codex.exe -File -Recurse |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($Codex) { break }
    }
}
if (-not $Codex) { $Codex = Get-Command codex -ErrorAction SilentlyContinue }
if (-not $Codex) { throw "Codex CLI is required on PATH. Install or open Codex Desktop, enable its CLI, and retry." }
$CodexPath = if ($Codex.Source) { $Codex.Source } else { $Codex.FullName }

$Existing = (& $CodexPath plugin marketplace list 2>&1 | Out-String)
if (-not $PreviousReceipt -and $Existing -match "(?m)^\s*$([regex]::Escape($MarketplaceName))\s*$") {
    throw "An unmanaged Marketplace registration named '$MarketplaceName' already exists; refusing to replace it without a verified receipt."
}

function Restore-PreviousRegistration {
    & $CodexPath plugin remove "$PluginName@$MarketplaceName" 2>$null
    & $CodexPath plugin marketplace remove $MarketplaceName 2>$null
    if ($PreviousReceipt) {
        & $CodexPath plugin marketplace add ([string]$PreviousReceipt.repository) --ref ([string]$PreviousReceipt.releaseTag)
        if ($LASTEXITCODE -ne 0) { throw "Rollback could not restore the previous Marketplace ref." }
        & $CodexPath plugin add "$PluginName@$([string]$PreviousReceipt.marketplace)"
        if ($LASTEXITCODE -ne 0) { throw "Rollback could not restore the previous plugin registration." }
    }
}

if ($PreviousReceipt) { Write-InstallTransaction "switching" "Preparing exact-ref registration transition." }

try {
    if ($PreviousReceipt) {
        & $CodexPath plugin remove "$PluginName@$([string]$PreviousReceipt.marketplace)" 2>$null
        if ($LASTEXITCODE -ne 0) { throw "Unable to remove the previous plugin registration." }
        & $CodexPath plugin marketplace remove ([string]$PreviousReceipt.marketplace)
        if ($LASTEXITCODE -ne 0) { throw "Unable to remove the previous Marketplace registration." }
    }
    & $CodexPath plugin marketplace add $MarketplaceSource --ref $ReleaseTag
    if ($LASTEXITCODE -ne 0) { throw "Unable to register Marketplace '$MarketplaceName' at immutable ref '$ReleaseTag'." }
    & $CodexPath plugin add "$PluginName@$MarketplaceName"
    if ($LASTEXITCODE -ne 0) { throw "Codex could not install $PluginName from $MarketplaceName." }

    $RuntimeInstaller = Join-Path $PluginRoot "scripts\install_runtime.ps1"
    if (-not (Test-Path -LiteralPath $RuntimeInstaller -PathType Leaf)) { throw "Runtime installer is missing: $RuntimeInstaller" }
    $RuntimeArguments = @{}
    if ($ForceRuntime) { $RuntimeArguments.Force = $true }
    if ($RuntimeRoot) { $RuntimeArguments.RuntimeRoot = $RuntimeRoot }
    if ($BinDir) { $RuntimeArguments.BinDir = $BinDir }
    & $RuntimeInstaller @RuntimeArguments
    if ($LASTEXITCODE -ne 0) { throw "Plugin runtime installation failed." }

    $VerifyArguments = @{}
    if ($RuntimeRoot) { $VerifyArguments.RuntimeRoot = $RuntimeRoot }
    & (Join-Path $RepoRoot "verify.ps1") @VerifyArguments
    if ($LASTEXITCODE -ne 0) { throw "Plugin verification failed." }
} catch {
    $Failure = $_
    try { Restore-PreviousRegistration } catch {
        $RollbackFailure = $_
        try { Write-InstallTransaction "recovery-required" "$($Failure.Exception.Message) Rollback failed: $($RollbackFailure.Exception.Message)" } catch {}
        throw "$($Failure.Exception.Message) RECOVERY-REQUIRED: rollback also failed: $($RollbackFailure.Exception.Message). See $TransactionPath"
    }
    if (Test-Path -LiteralPath $TransactionPath) { Remove-Item -LiteralPath $TransactionPath -Force }
    throw $Failure
}

$ReceiptDirectory = Split-Path -Parent $ReceiptPath
$ReceiptTemporary = "$ReceiptPath.tmp-$([guid]::NewGuid().ToString('N'))"
try {
    New-Item -ItemType Directory -Force -Path $ReceiptDirectory | Out-Null
    [ordered]@{
        schemaVersion = "editable-ppt-install-receipt-v1"
        plugin = $PluginName
        marketplace = $MarketplaceName
        repository = $MarketplaceSource
        releaseTag = $ReleaseTag
        pluginVersion = [string]$PackageInfo.pluginVersion
        workflowContractVersion = [string]$PackageInfo.workflowContractVersion
        installedAtUtc = [DateTime]::UtcNow.ToString("o")
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ReceiptTemporary -Encoding utf8
    Move-Item -LiteralPath $ReceiptTemporary -Destination $ReceiptPath -Force
} catch {
    $Failure = $_
    try { Restore-PreviousRegistration } catch {
        $RollbackFailure = $_
        try { Write-InstallTransaction "recovery-required" "$($Failure.Exception.Message) Receipt commit and rollback failed: $($RollbackFailure.Exception.Message)" } catch {}
        throw "$($Failure.Exception.Message) RECOVERY-REQUIRED: receipt commit failed and rollback also failed: $($RollbackFailure.Exception.Message). See $TransactionPath"
    }
    if (Test-Path -LiteralPath $TransactionPath) { Remove-Item -LiteralPath $TransactionPath -Force }
    throw "Installation registration was rolled back because the receipt could not be committed: $($Failure.Exception.Message)"
} finally {
    if (Test-Path -LiteralPath $ReceiptTemporary) { Remove-Item -LiteralPath $ReceiptTemporary -Force }
}
if (Test-Path -LiteralPath $TransactionPath) { Remove-Item -LiteralPath $TransactionPath -Force }

Write-Output "Installation complete. Start a new Codex task, then provide one paginated Word document to @$PluginName."
