# Complete filesystem snapshot: venvs contain absolute paths, so restore to the
# same runtime path. Successful snapshots are retained for explicit rollback.
function Assert-RuntimeTreeWithoutLinks([string]$Path) {
    Assert-NoRuntimeRootReparsePoint $Path
    if (Test-Path -LiteralPath $Path -PathType Container) {
        foreach ($item in Get-ChildItem -LiteralPath $Path -Recurse -Force) {
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Refusing runtime snapshot containing a reparse point: $($item.FullName)"
            }
        }
    }
}

function Backup-EditablePptRuntime([string]$RuntimeRoot, [string]$BinDir, [string]$PluginRoot, $PreviousReceipt = $null) {
    $default = Join-Path $env:USERPROFILE '.codex\plugin-runtimes\awesome-editable-ppt-workflow-fixed-canvas-cm-v2'
    $runtime = Get-NormalizedRuntimePath $RuntimeRoot
    $bin = Get-NormalizedRuntimePath $BinDir
    Assert-RuntimeRootLocation $runtime $default $PluginRoot
    Assert-RuntimeTreeWithoutLinks $runtime
    Assert-NoRuntimeRootReparsePoint $bin
    if (Test-Path -LiteralPath $runtime) {
        if (-not (Test-Path -LiteralPath $runtime -PathType Container)) { throw 'Runtime path must be a directory.' }
        if (@(Get-ChildItem -LiteralPath $runtime -Force).Count -and -not (Test-RuntimeOwnershipSentinel $runtime)) {
            throw 'Refusing to snapshot an unowned runtime.'
        }
    }
    $backup = "$runtime.rollback-$([guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $backup | Out-Null
    $state = [ordered]@{
        schemaVersion = 1; runtimeRoot = $runtime; binDir = $bin; backupRoot = $backup
        runtimeExisted = (Test-Path -LiteralPath $runtime); wrappers = @{}
        previousReceipt = $PreviousReceipt
        userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    }
    if ($state.runtimeExisted) { Copy-Item -LiteralPath $runtime -Destination (Join-Path $backup 'runtime') -Recurse -Force }
    foreach ($name in @('editppt.CMD', 'officecli.CMD')) {
        $wrapper = Join-Path $bin $name
        Assert-NoRuntimeRootReparsePoint $wrapper
        $state.wrappers[$name] = Test-Path -LiteralPath $wrapper -PathType Leaf
        if ($state.wrappers[$name]) { Copy-Item -LiteralPath $wrapper -Destination (Join-Path $backup $name) }
    }
    $state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $backup 'snapshot.json') -Encoding utf8
    return $state
}

function Restore-EditablePptRuntime($Snapshot) {
    $runtime = Get-NormalizedRuntimePath $Snapshot.runtimeRoot
    $backup = Get-NormalizedRuntimePath $Snapshot.backupRoot
    if ((Split-Path -Parent $backup) -ne (Split-Path -Parent $runtime) -or
        -not $backup.StartsWith("$runtime.rollback-", [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Runtime rollback path is outside the expected sibling backup.'
    }
    Assert-RuntimeTreeWithoutLinks $backup
    Assert-RuntimeTreeWithoutLinks $runtime
    $saved = Get-Content -Raw -LiteralPath (Join-Path $backup 'snapshot.json') | ConvertFrom-Json
    if ($saved.schemaVersion -ne 1 -or $saved.runtimeRoot -ne $runtime -or $saved.binDir -ne $Snapshot.binDir -or
        $saved.backupRoot -ne $backup -or $saved.runtimeExisted -ne $Snapshot.runtimeExisted) {
        throw 'Runtime rollback snapshot identity is invalid.'
    }
    if ($Snapshot.runtimeExisted -and -not (Test-Path -LiteralPath (Join-Path $backup 'runtime') -PathType Container)) {
        throw 'Runtime rollback backup is incomplete.'
    }
    Assert-NoRuntimeRootReparsePoint $Snapshot.binDir
    foreach ($name in @('editppt.CMD', 'officecli.CMD')) {
        Assert-NoRuntimeRootReparsePoint (Join-Path $Snapshot.binDir $name)
        if ($Snapshot.wrappers[$name] -and -not (Test-Path -LiteralPath (Join-Path $backup $name) -PathType Leaf)) {
            throw 'Runtime rollback wrapper backup is incomplete.'
        }
    }
    if (Test-Path -LiteralPath $runtime) {
        if (@(Get-ChildItem -LiteralPath $runtime -Force).Count -and -not (Test-RuntimeOwnershipSentinel $runtime)) {
            throw 'Refusing to replace an unowned runtime during rollback.'
        }
        Remove-Item -LiteralPath $runtime -Recurse -Force
    }
    if ($Snapshot.runtimeExisted) { Copy-Item -LiteralPath (Join-Path $backup 'runtime') -Destination $runtime -Recurse -Force }
    Assert-NoRuntimeRootReparsePoint $Snapshot.binDir
    foreach ($name in @('editppt.CMD', 'officecli.CMD')) {
        $wrapper = Join-Path $Snapshot.binDir $name
        Assert-NoRuntimeRootReparsePoint $wrapper
        if ($Snapshot.wrappers[$name]) {
            New-Item -ItemType Directory -Force -Path $Snapshot.binDir | Out-Null
            Copy-Item -LiteralPath (Join-Path $backup $name) -Destination $wrapper -Force
        } elseif (Test-Path -LiteralPath $wrapper -PathType Leaf) {
            Remove-Item -LiteralPath $wrapper -Force
        }
    }
    if ([Environment]::GetEnvironmentVariable('Path', 'User') -ne $Snapshot.userPath) {
        [Environment]::SetEnvironmentVariable('Path', $Snapshot.userPath, 'User')
    }
}
