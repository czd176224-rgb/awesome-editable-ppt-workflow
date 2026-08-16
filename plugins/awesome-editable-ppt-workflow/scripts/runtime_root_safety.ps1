function Get-NormalizedRuntimePath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return [System.IO.Path]::GetFullPath($Path).TrimEnd([char[]]"\/")
}

function Test-RuntimePathEqual {
    param(
        [Parameter(Mandatory = $true)][string]$Left,
        [Parameter(Mandatory = $true)][string]$Right
    )

    return [string]::Equals(
        (Get-NormalizedRuntimePath $Left),
        (Get-NormalizedRuntimePath $Right),
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Test-RuntimePathWithin {
    param(
        [Parameter(Mandatory = $true)][string]$Child,
        [Parameter(Mandatory = $true)][string]$Parent
    )

    $NormalizedChild = Get-NormalizedRuntimePath $Child
    $NormalizedParent = Get-NormalizedRuntimePath $Parent
    if (Test-RuntimePathEqual $NormalizedChild $NormalizedParent) {
        return $false
    }
    return $NormalizedChild.StartsWith(
        $NormalizedParent + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Get-RuntimeOwnershipSentinelPath {
    param([Parameter(Mandatory = $true)][string]$RuntimeRoot)

    return Join-Path (Get-NormalizedRuntimePath $RuntimeRoot) ".awesome-editable-ppt-workflow-runtime-owner.json"
}

function Test-RuntimeOwnershipSentinel {
    param([Parameter(Mandatory = $true)][string]$RuntimeRoot)

    $Sentinel = Get-RuntimeOwnershipSentinelPath $RuntimeRoot
    if (-not (Test-Path -LiteralPath $Sentinel -PathType Leaf)) {
        return $false
    }
    try {
        $Ownership = Get-Content -Raw -Encoding UTF8 -LiteralPath $Sentinel | ConvertFrom-Json
        return (
            $Ownership.schemaVersion -eq 1 -and
            $Ownership.owner -eq "awesome-editable-ppt-workflow" -and
            (Test-RuntimePathEqual ([string]$Ownership.runtimeRoot) $RuntimeRoot)
        )
    } catch {
        return $false
    }
}

function Write-RuntimeOwnershipSentinel {
    param([Parameter(Mandatory = $true)][string]$RuntimeRoot)

    $NormalizedRoot = Get-NormalizedRuntimePath $RuntimeRoot
    [ordered]@{
        schemaVersion = 1
        owner = "awesome-editable-ppt-workflow"
        runtimeRoot = $NormalizedRoot
    } | ConvertTo-Json | Set-Content -LiteralPath (Get-RuntimeOwnershipSentinelPath $NormalizedRoot) -Encoding utf8
}

function Assert-NoRuntimeRootReparsePoint {
    param([Parameter(Mandatory = $true)][string]$RuntimeRoot)

    $Current = Get-NormalizedRuntimePath $RuntimeRoot
    while ($Current) {
        if (Test-Path -LiteralPath $Current) {
            $Item = Get-Item -LiteralPath $Current -Force
            if (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Refusing runtime root with a symbolic-link, junction, or reparse-point ancestor: $Current"
            }
        }
        $Parent = Split-Path -Parent $Current
        if (-not $Parent -or (Test-RuntimePathEqual $Parent $Current)) {
            break
        }
        $Current = $Parent
    }
}

function Get-ProtectedRuntimeRoots {
    param([Parameter(Mandatory = $true)][string]$PluginRoot)

    $RepoRoot = Split-Path -Parent (Split-Path -Parent (Get-NormalizedRuntimePath $PluginRoot))
    $Desktop = [Environment]::GetFolderPath("Desktop")
    return @($RepoRoot, $PluginRoot, $Desktop) | Where-Object { $_ }
}

function Assert-RuntimeRootLocation {
    param(
        [Parameter(Mandatory = $true)][string]$RuntimeRoot,
        [Parameter(Mandatory = $true)][string]$DefaultRuntimeRoot,
        [Parameter(Mandatory = $true)][string]$PluginRoot
    )

    $NormalizedRoot = Get-NormalizedRuntimePath $RuntimeRoot
    $UserCodexRoot = Join-Path $env:USERPROFILE ".codex"
    $DefaultParent = Split-Path -Parent (Get-NormalizedRuntimePath $DefaultRuntimeRoot)
    $SystemTemp = Get-NormalizedRuntimePath ([System.IO.Path]::GetTempPath())
    $BroadTargets = @(
        [System.IO.Path]::GetPathRoot($NormalizedRoot),
        $env:USERPROFILE,
        $UserCodexRoot,
        $DefaultParent,
        $SystemTemp
    ) | Where-Object { $_ }
    if ($BroadTargets | Where-Object { Test-RuntimePathEqual $_ $NormalizedRoot }) {
        throw "Refusing broad runtime root: $NormalizedRoot"
    }
    foreach ($Protected in (Get-ProtectedRuntimeRoots $PluginRoot)) {
        if ((Test-RuntimePathEqual $NormalizedRoot $Protected) -or (Test-RuntimePathWithin $NormalizedRoot $Protected)) {
            throw "Refusing runtime root inside a protected source or desktop directory: $NormalizedRoot"
        }
    }
    Assert-NoRuntimeRootReparsePoint $NormalizedRoot
}

function Initialize-EditablePptRuntimeRoot {
    param(
        [Parameter(Mandatory = $true)][string]$RuntimeRoot,
        [Parameter(Mandatory = $true)][string]$DefaultRuntimeRoot,
        [Parameter(Mandatory = $true)][string]$PluginRoot,
        [switch]$Force
    )

    $NormalizedRoot = Get-NormalizedRuntimePath $RuntimeRoot
    $NormalizedDefault = Get-NormalizedRuntimePath $DefaultRuntimeRoot
    Assert-RuntimeRootLocation $NormalizedRoot $NormalizedDefault $PluginRoot

    $Exists = Test-Path -LiteralPath $NormalizedRoot -PathType Container
    $IsOwned = $Exists -and (Test-RuntimeOwnershipSentinel $NormalizedRoot)
    $IsEmpty = $Exists -and @(Get-ChildItem -LiteralPath $NormalizedRoot -Force).Count -eq 0

    if ($Exists -and -not $IsOwned -and -not $IsEmpty) {
        throw "Refusing existing non-empty runtime root without a matching ownership sentinel: $NormalizedRoot"
    }
    if ($Force -and $Exists) {
        if ($IsOwned) {
            [System.IO.Directory]::Delete($NormalizedRoot, $true)
        } else {
            [System.IO.Directory]::Delete($NormalizedRoot, $false)
        }
        $Exists = $false
    }
    if (-not $Exists) {
        New-Item -ItemType Directory -Path $NormalizedRoot | Out-Null
    }
    Write-RuntimeOwnershipSentinel $NormalizedRoot
    return $NormalizedRoot
}

function Remove-StaleEditablePptWorkflowPackages {
    param(
        [Parameter(Mandatory = $true)][string]$RuntimeRoot,
        [Parameter(Mandatory = $true)][string]$CurrentWorkflowRoot
    )

    $NormalizedRoot = Get-NormalizedRuntimePath $RuntimeRoot
    $NormalizedCurrent = Get-NormalizedRuntimePath $CurrentWorkflowRoot
    if (-not (Test-RuntimeOwnershipSentinel $NormalizedRoot)) {
        throw "Refusing workflow cleanup without a matching runtime ownership sentinel: $NormalizedRoot"
    }
    if (-not (Test-RuntimePathWithin $NormalizedCurrent $NormalizedRoot)) {
        throw "Current workflow package is outside the owned runtime root: $NormalizedCurrent"
    }
    if (-not (Test-Path -LiteralPath $NormalizedCurrent -PathType Container)) {
        throw "Current workflow package is unavailable: $NormalizedCurrent"
    }
    foreach ($Directory in Get-ChildItem -LiteralPath $NormalizedRoot -Directory -Filter "workflow-*" -Force) {
        $Candidate = Get-NormalizedRuntimePath $Directory.FullName
        if (Test-RuntimePathEqual $Candidate $NormalizedCurrent) {
            continue
        }
        if (-not (Test-RuntimePathWithin $Candidate $NormalizedRoot)) {
            throw "Refusing workflow cleanup outside the owned runtime root: $Candidate"
        }
        if (($Directory.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing to remove a stale workflow package through a reparse point: $Candidate"
        }
        [System.IO.Directory]::Delete($Candidate, $true)
    }
}

function Remove-StaleEditablePptDetectorPackages {
    param(
        [Parameter(Mandatory = $true)][string]$RuntimeRoot,
        [Parameter(Mandatory = $true)][string]$CurrentDetectorRoot
    )

    $NormalizedRoot = Get-NormalizedRuntimePath $RuntimeRoot
    $NormalizedCurrent = Get-NormalizedRuntimePath $CurrentDetectorRoot
    if (-not (Test-RuntimeOwnershipSentinel $NormalizedRoot)) {
        throw "Refusing detector cleanup without a matching runtime ownership sentinel: $NormalizedRoot"
    }
    if (-not (Test-RuntimePathWithin $NormalizedCurrent $NormalizedRoot)) {
        throw "Current detector package is outside the owned runtime root: $NormalizedCurrent"
    }
    if (-not (Test-Path -LiteralPath $NormalizedCurrent -PathType Container)) {
        throw "Current detector package is unavailable: $NormalizedCurrent"
    }
    foreach ($Directory in Get-ChildItem -LiteralPath $NormalizedRoot -Directory -Filter "background-text-detector-*" -Force) {
        $Candidate = Get-NormalizedRuntimePath $Directory.FullName
        if (Test-RuntimePathEqual $Candidate $NormalizedCurrent) {
            continue
        }
        if (-not (Test-RuntimePathWithin $Candidate $NormalizedRoot)) {
            throw "Refusing detector cleanup outside the owned runtime root: $Candidate"
        }
        if (($Directory.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing to remove a stale detector package through a reparse point: $Candidate"
        }
        [System.IO.Directory]::Delete($Candidate, $true)
    }
}
