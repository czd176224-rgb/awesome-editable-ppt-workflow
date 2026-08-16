param(
    [Parameter(Mandatory = $true)][string]$OutputPath
)

$ErrorActionPreference = "Stop"
$RepoRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)
$ManifestPath = Join-Path $RepoRoot "public-release-files.json"
$RepoPrefix = $RepoRoot.TrimEnd([char[]]"\/") + [System.IO.Path]::DirectorySeparatorChar

function Get-Sha256([string]$Path) {
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($Path)
    try { return ([System.BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace("-", "").ToLowerInvariant() }
    finally { $stream.Dispose(); $algorithm.Dispose() }
}
function Get-ContentTreeSha256($Files) {
    [string[]]$keys = @($Files.Keys | ForEach-Object { [string]$_ })
    [Array]::Sort($keys, [System.StringComparer]::Ordinal)
    $lines = @($keys | ForEach-Object { "$_ $($Files[$_])" })
    $bytes = [System.Text.Encoding]::UTF8.GetBytes(($lines -join "`n") + "`n")
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try { return ([System.BitConverter]::ToString($algorithm.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant() }
    finally { $algorithm.Dispose() }
}
function Write-Utf8NoBom([string]$Path, [string]$Content) {
    [System.IO.File]::WriteAllText($Path, $Content, [System.Text.UTF8Encoding]::new($false))
}

if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    throw "Public release allowlist is missing: $ManifestPath"
}
if (Test-Path -LiteralPath $OutputPath) {
    if (-not (Test-Path -LiteralPath $OutputPath -PathType Container)) {
        throw "OutputPath exists and is not a directory: $OutputPath"
    }
    if (@(Get-ChildItem -LiteralPath $OutputPath -Force).Count -ne 0) {
        throw "OutputPath must be absent or empty: $OutputPath"
    }
} else {
    New-Item -ItemType Directory -Path $OutputPath | Out-Null
}

$Allowlist = Get-Content -Raw -Encoding UTF8 -LiteralPath $ManifestPath | ConvertFrom-Json
$Tracked = @(& git -C $RepoRoot ls-files --cached)
if ($LASTEXITCODE -ne 0) { throw "git ls-files failed; public export requires a Git index." }
& git -C $RepoRoot diff --quiet -- @($Allowlist.files)
if ($LASTEXITCODE -ne 0) { throw "Public export requires tracked allowlisted files to match the reviewed commit." }
& git -C $RepoRoot diff --cached --quiet -- @($Allowlist.files)
if ($LASTEXITCODE -ne 0) { throw "Public export requires no staged allowlisted changes outside the reviewed commit." }
$TrackedSet = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
foreach ($item in $Tracked) { [void]$TrackedSet.Add(([string]$item).Replace("\", "/")) }
foreach ($relative in $Allowlist.files) {
    $source = Join-Path $RepoRoot ([string]$relative)
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Allowlisted source is missing: $relative"
    }
    if (Test-Path -LiteralPath $source -PathType Container) {
        $prefix = ([string]$relative).Replace("\", "/").TrimEnd("/") + "/"
        $Tracked | Where-Object { ([string]$_).Replace("\", "/").StartsWith($prefix, [System.StringComparison]::Ordinal) } | ForEach-Object {
            $childRelative = ([string]$_).Replace("/", [System.IO.Path]::DirectorySeparatorChar)
            $childFullName = Join-Path $RepoRoot $childRelative
            $destination = Join-Path $OutputPath $childRelative
            $parent = Split-Path -Parent $destination
            if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
            Copy-Item -LiteralPath $childFullName -Destination $destination -Force
        }
    } else {
        $normalized = ([string]$relative).Replace("\", "/")
        if (-not $TrackedSet.Contains($normalized)) { throw "Allowlisted source is not tracked by Git: $relative" }
        $destination = Join-Path $OutputPath ([string]$relative)
        $parent = Split-Path -Parent $destination
        if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
        Copy-Item -LiteralPath $source -Destination $destination -Force
    }
}

$PackagePath = Join-Path $OutputPath "package-info.json"
$Package = Get-Content -Raw -Encoding UTF8 -LiteralPath $PackagePath | ConvertFrom-Json
if (
    $Package.marketplace -ne "editable-ppt-public" -or
    $Package.marketplacePreviewIdentity -ne "editable-ppt-public" -or
    $Package.repository -ne "czd176224-rgb/awesome-editable-ppt-workflow" -or
    $Package.releaseStatus -ne "published-public-marketplace" -or
    $Package.repositoryVisibility -ne "public"
) {
    throw "Public source metadata is not already sealed for the public marketplace."
}

$MarketplacePath = Join-Path $OutputPath ".agents\plugins\marketplace.json"
$Marketplace = Get-Content -Raw -Encoding UTF8 -LiteralPath $MarketplacePath | ConvertFrom-Json
if (
    $Marketplace.name -ne "editable-ppt-public" -or
    $Marketplace.interface.displayName -ne "Awesome Editable PPT Workflow $($Package.pluginVersion)"
) {
    throw "Public marketplace metadata is not already sealed for this plugin version."
}

$SourceFiles = [ordered]@{}
Get-ChildItem -LiteralPath $OutputPath -File -Recurse -Force | Sort-Object FullName | ForEach-Object {
    $relative = $_.FullName.Substring(($OutputPath.TrimEnd([char[]]"\/") + [System.IO.Path]::DirectorySeparatorChar).Length).Replace("\", "/")
    if ($relative -in @("public-source-manifest.json", "public-release-audit.json")) { return }
    $SourceFiles[$relative] = Get-Sha256 $_.FullName
}
$SourceManifestJson = [ordered]@{
    schemaVersion = "public-source-manifest-v1"
    authority = "tracked-public-source"
    releaseTag = [string]$Package.releaseTag
    pluginVersion = [string]$Package.pluginVersion
    workflowContractVersion = [string]$Package.workflowContractVersion
    promptContractVersion = [string]$Package.promptContractVersion
    pageImagePolicy = [string]$Package.pageImagePolicy
    indexTreeSha256 = Get-ContentTreeSha256 $SourceFiles
    files = $SourceFiles
} | ConvertTo-Json -Depth 10
$SourceManifestJson = $SourceManifestJson -replace "`r`n", "`n"
Write-Utf8NoBom (Join-Path $OutputPath "public-source-manifest.json") ($SourceManifestJson + "`n")

$ReportPath = Join-Path $OutputPath "public-release-audit.json"
& python (Join-Path $OutputPath "scripts\check_public_release.py") $OutputPath --write-report $ReportPath
if ($LASTEXITCODE -ne 0) {
    throw "Public release validation failed. See $ReportPath"
}
Write-Output "Public release snapshot created: $OutputPath"
