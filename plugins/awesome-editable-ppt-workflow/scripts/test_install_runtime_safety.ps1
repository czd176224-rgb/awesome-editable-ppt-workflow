$ErrorActionPreference = "Stop"
$SafetyScript = Join-Path $PSScriptRoot "runtime_root_safety.ps1"
. $SafetyScript

$PluginRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$DefaultRuntimeRoot = Join-Path $env:USERPROFILE ".codex\plugin-runtimes\awesome-editable-ppt-workflow-fixed-canvas-cm-v2"
$TestRoot = Join-Path $env:USERPROFILE (".codex\runtime-safety-smoke-" + [guid]::NewGuid().ToString("N"))
$UnownedRoot = "$TestRoot-unowned"
$ProtectedRoot = Join-Path $PluginRoot ("runtime-safety-smoke-" + [guid]::NewGuid().ToString("N"))
$SafetyTempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("editable-ppt-runtime-safety-" + [guid]::NewGuid().ToString("N"))
$FakeDefaultRoot = Join-Path $SafetyTempRoot "default-runtime"
$TempUnownedRoot = Join-Path $SafetyTempRoot "unowned-temp-runtime"
$ReparseTarget = Join-Path $SafetyTempRoot "junction-target"
$ReparseParent = Join-Path $SafetyTempRoot "junction-parent"
$ReparseRuntime = Join-Path $ReparseParent "runtime"

try {
    $Initialized = Initialize-EditablePptRuntimeRoot `
        -RuntimeRoot $TestRoot `
        -DefaultRuntimeRoot $DefaultRuntimeRoot `
        -PluginRoot $PluginRoot
    if (-not (Test-RuntimeOwnershipSentinel $Initialized)) {
        throw "A first install did not create a valid ownership sentinel."
    }
    Set-Content -LiteralPath (Join-Path $Initialized "replace-me.txt") -Value "owned"
    Initialize-EditablePptRuntimeRoot `
        -RuntimeRoot $Initialized `
        -DefaultRuntimeRoot $DefaultRuntimeRoot `
        -PluginRoot $PluginRoot `
        -Force | Out-Null
    if (Test-Path -LiteralPath (Join-Path $Initialized "replace-me.txt")) {
        throw "Force did not replace the owned runtime root."
    }
    $CurrentWorkflow = Join-Path $Initialized "workflow-current"
    $OldWorkflow = Join-Path $Initialized "workflow-old"
    $CurrentDetector = Join-Path $Initialized "background-text-detector-current"
    $OldDetector = Join-Path $Initialized "background-text-detector-old"
    $UnrelatedRuntimeData = Join-Path $Initialized "preserve-runtime-data"
    New-Item -ItemType Directory -Path $CurrentWorkflow, $OldWorkflow, $CurrentDetector, $OldDetector, $UnrelatedRuntimeData | Out-Null
    Set-Content -LiteralPath (Join-Path $OldWorkflow "old.txt") -Value "old"
    Set-Content -LiteralPath (Join-Path $OldDetector "old.txt") -Value "old"
    Remove-StaleEditablePptWorkflowPackages `
        -RuntimeRoot $Initialized `
        -CurrentWorkflowRoot $CurrentWorkflow
    if (-not (Test-Path -LiteralPath $CurrentWorkflow -PathType Container)) {
        throw "Current workflow package was removed during stale-version cleanup."
    }
    if (Test-Path -LiteralPath $OldWorkflow) {
        throw "Stale workflow package was not removed."
    }
    if (-not (Test-Path -LiteralPath $UnrelatedRuntimeData -PathType Container)) {
        throw "Unrelated runtime data was removed during stale-version cleanup."
    }
    Remove-StaleEditablePptDetectorPackages `
        -RuntimeRoot $Initialized `
        -CurrentDetectorRoot $CurrentDetector
    if (-not (Test-Path -LiteralPath $CurrentDetector -PathType Container)) {
        throw "Current detector package was removed during stale-version cleanup."
    }
    if (Test-Path -LiteralPath $OldDetector) {
        throw "Stale detector package was not removed."
    }

    New-Item -ItemType Directory -Path $UnownedRoot | Out-Null
    Set-Content -LiteralPath (Join-Path $UnownedRoot "preserve.txt") -Value "user-data"
    Set-Content -LiteralPath (Get-RuntimeOwnershipSentinelPath $UnownedRoot) -Value '{"schemaVersion":1,"owner":"wrong","runtimeRoot":"wrong"}'
    $RejectedUnowned = $false
    try {
        Initialize-EditablePptRuntimeRoot `
            -RuntimeRoot $UnownedRoot `
            -DefaultRuntimeRoot $DefaultRuntimeRoot `
            -PluginRoot $PluginRoot `
            -Force | Out-Null
    } catch {
        $RejectedUnowned = $true
    }
    if (-not $RejectedUnowned -or -not (Test-Path -LiteralPath (Join-Path $UnownedRoot "preserve.txt"))) {
        throw "An unowned custom directory was not safely preserved."
    }

    New-Item -ItemType Directory -Path $ProtectedRoot | Out-Null
    Set-Content -LiteralPath (Join-Path $ProtectedRoot "preserve.txt") -Value "source-data"
    $RejectedProtected = $false
    try {
        Initialize-EditablePptRuntimeRoot `
            -RuntimeRoot $ProtectedRoot `
            -DefaultRuntimeRoot $DefaultRuntimeRoot `
            -PluginRoot $PluginRoot `
            -Force | Out-Null
    } catch {
        $RejectedProtected = $true
    }
    if (-not $RejectedProtected -or -not (Test-Path -LiteralPath (Join-Path $ProtectedRoot "preserve.txt"))) {
        throw "A protected source directory was not safely preserved."
    }

    New-Item -ItemType Directory -Path $FakeDefaultRoot -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $FakeDefaultRoot "preserve.txt") -Value "default-user-data"
    $RejectedDefault = $false
    try {
        Initialize-EditablePptRuntimeRoot `
            -RuntimeRoot $FakeDefaultRoot `
            -DefaultRuntimeRoot $FakeDefaultRoot `
            -PluginRoot $PluginRoot `
            -Force | Out-Null
    } catch {
        $RejectedDefault = $true
    }
    if (-not $RejectedDefault -or -not (Test-Path -LiteralPath (Join-Path $FakeDefaultRoot "preserve.txt"))) {
        throw "A non-empty default runtime without a sentinel was not preserved."
    }

    New-Item -ItemType Directory -Path $TempUnownedRoot -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $TempUnownedRoot "preserve.txt") -Value "temp-user-data"
    $RejectedTemp = $false
    try {
        Initialize-EditablePptRuntimeRoot `
            -RuntimeRoot $TempUnownedRoot `
            -DefaultRuntimeRoot $DefaultRuntimeRoot `
            -PluginRoot $PluginRoot `
            -Force | Out-Null
    } catch {
        $RejectedTemp = $true
    }
    if (-not $RejectedTemp -or -not (Test-Path -LiteralPath (Join-Path $TempUnownedRoot "preserve.txt"))) {
        throw "A non-empty temporary runtime without a sentinel was not preserved."
    }

    New-Item -ItemType Directory -Path $ReparseTarget -Force | Out-Null
    New-Item -ItemType Junction -Path $ReparseParent -Target $ReparseTarget | Out-Null
    $RejectedReparseAncestor = $false
    try {
        Initialize-EditablePptRuntimeRoot `
            -RuntimeRoot $ReparseRuntime `
            -DefaultRuntimeRoot $DefaultRuntimeRoot `
            -PluginRoot $PluginRoot | Out-Null
    } catch {
        $RejectedReparseAncestor = $true
    }
    if (-not $RejectedReparseAncestor -or (Test-Path -LiteralPath $ReparseRuntime)) {
        throw "A runtime root beneath a junction ancestor was not rejected."
    }

    Write-Output "runtime-root-safety-smoke=ok"
} finally {
    if (Test-Path -LiteralPath $ReparseParent) {
        Remove-Item -LiteralPath $ReparseParent -Force
    }
    foreach ($Path in @($TestRoot, $UnownedRoot, $ProtectedRoot)) {
        if (Test-Path -LiteralPath $Path) {
            [System.IO.Directory]::Delete([System.IO.Path]::GetFullPath($Path), $true)
        }
    }
    if (Test-Path -LiteralPath $SafetyTempRoot) {
        [System.IO.Directory]::Delete([System.IO.Path]::GetFullPath($SafetyTempRoot), $true)
    }
}
