param(
    [switch]$Force,
    [string]$RuntimeRoot,
    [string]$BinDir,
    [string]$OfficeCliExe,
    [switch]$PortableSmokeTest
)

$ErrorActionPreference = "Stop"
$PluginRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)))
$WorkflowSkill = Join-Path $PluginRoot "skills\run-word-to-ppt-workflow"
$EditableSkill = Join-Path $PluginRoot "skills\reconstruct-editable-slide"
$ImageSkill = Join-Path $PluginRoot "skills\generate-slide-body-image"
$RunningOnWindows = [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
$UserCodexRoot = Join-Path $env:USERPROFILE ".codex"
$DefaultRuntimeRoot = Join-Path $UserCodexRoot "plugin-runtimes\awesome-editable-ppt-workflow-fixed-canvas-cm-v2"
$RuntimeSafetyScript = Join-Path $PSScriptRoot "runtime_root_safety.ps1"
. $RuntimeSafetyScript
$RuntimeProcessEnvironmentScript = Join-Path $PSScriptRoot "runtime_process_environment.ps1"
. $RuntimeProcessEnvironmentScript

if (-not $RuntimeRoot) {
    $RuntimeRoot = $DefaultRuntimeRoot
}
if (-not $BinDir) {
    $BinDir = Join-Path $UserCodexRoot "bin"
}
$RuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
$BinDir = [System.IO.Path]::GetFullPath($BinDir)

if ($PortableSmokeTest) {
    if (-not $PSBoundParameters.ContainsKey("RuntimeRoot") -or -not $PSBoundParameters.ContainsKey("BinDir")) {
        throw "PortableSmokeTest requires explicit temporary RuntimeRoot and BinDir paths."
    }
}

foreach ($required in @(
    (Join-Path $WorkflowSkill "SKILL.md"),
    (Join-Path $WorkflowSkill "requirements.txt"),
    (Join-Path $WorkflowSkill "scripts\confirm_ui\server.py"),
    (Join-Path $PluginRoot "scripts\runtime_office.py"),
    $RuntimeProcessEnvironmentScript,
    (Join-Path $EditableSkill "SKILL.md"),
    (Join-Path $PluginRoot "skills\generate-slide-body-image\SKILL.md"),
    (Join-Path $PluginRoot "skills\validate-ppt-output\SKILL.md")
)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Plugin file is missing: $required"
    }
}

$PythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $PythonCommand) {
    throw "Python 3.10+ is required. Install Python and enable python.exe on PATH."
}
$PythonExe = if ($PythonCommand.Source) { $PythonCommand.Source } else { $PythonCommand.Path }
$DetectedPython = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
if ($LASTEXITCODE -ne 0) {
    throw "Unable to execute Python."
}
$DetectedParts = $DetectedPython.Trim().Split('.')
if ([int]$DetectedParts[0] -lt 3 -or ([int]$DetectedParts[0] -eq 3 -and [int]$DetectedParts[1] -lt 10)) {
    throw "Python 3.10 or newer is required; detected $DetectedPython."
}

$RuntimeRoot = Initialize-EditablePptRuntimeRoot `
    -RuntimeRoot $RuntimeRoot `
    -DefaultRuntimeRoot $DefaultRuntimeRoot `
    -PluginRoot $PluginRoot `
    -Force:$Force
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

$PluginManifest = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $PluginRoot ".codex-plugin\plugin.json") | ConvertFrom-Json
$PluginVersion = [string]$PluginManifest.version
$InstallStatePath = Join-Path $RuntimeRoot "runtime_install_state.json"
$InstallState = [ordered]@{
    schemaVersion = 1
    plugin_version = $PluginVersion
    workflow_dependencies_ready = $false
    editable_cli_ready = $false
}
if (Test-Path -LiteralPath $InstallStatePath -PathType Leaf) {
    try {
        $PreviousState = Get-Content -Raw -Encoding UTF8 -LiteralPath $InstallStatePath | ConvertFrom-Json
        if ($PreviousState.schemaVersion -eq 1 -and $PreviousState.plugin_version -eq $PluginVersion) {
            $InstallState.workflow_dependencies_ready = $PreviousState.workflow_dependencies_ready -eq $true
            $InstallState.editable_cli_ready = $PreviousState.editable_cli_ready -eq $true
        }
    } catch {
        Write-Warning "Ignoring unreadable runtime install state: $InstallStatePath"
    }
}

function Save-RuntimeInstallState {
    $InstallState | ConvertTo-Json | Set-Content -LiteralPath $InstallStatePath -Encoding utf8
}

$WorkflowVenv = Join-Path $RuntimeRoot "workflow"
if (-not (Test-Path -LiteralPath (Join-Path $WorkflowVenv "Scripts\python.exe") -PathType Leaf)) {
    & $PythonExe -m venv $WorkflowVenv
    if ($LASTEXITCODE -ne 0) { throw "Unable to create the workflow virtual environment." }
}
$WorkflowPython = Join-Path $WorkflowVenv "Scripts\python.exe"
$WorkflowStageReady = $InstallState.workflow_dependencies_ready -eq $true
if ($WorkflowStageReady) {
    & $WorkflowPython -c "import flask, jsonschema, PIL, pypdf, pypdfium2, docx, pptx, editppt"
    $WorkflowStageReady = $LASTEXITCODE -eq 0
}
if (-not $WorkflowStageReady) {
    $InstallState.workflow_dependencies_ready = $false
    Save-RuntimeInstallState
    & $WorkflowPython -m pip install --disable-pip-version-check --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "Unable to update pip in the workflow runtime." }
    & $WorkflowPython -m pip install --disable-pip-version-check -r (Join-Path $WorkflowSkill "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Unable to install workflow prerequisites." }
    & $WorkflowPython -m pip install --disable-pip-version-check (Join-Path $EditableSkill "cli")
    if ($LASTEXITCODE -ne 0) { throw "Unable to install the workflow's editable-PPT package dependency." }
    & $WorkflowPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "Workflow prerequisite check failed." }
    $InstallState.workflow_dependencies_ready = $true
    Save-RuntimeInstallState
} else {
    Write-Output "Reusing workflow dependencies for plugin $PluginVersion."
}

$EditableVenv = Join-Path $RuntimeRoot "editable-ppt"
if (-not (Test-Path -LiteralPath (Join-Path $EditableVenv "Scripts\python.exe") -PathType Leaf)) {
    & $PythonExe -m venv $EditableVenv
    if ($LASTEXITCODE -ne 0) { throw "Unable to create the editable-PPT virtual environment." }
}
$EditablePython = Join-Path $EditableVenv "Scripts\python.exe"
$EditExe = Join-Path $EditableVenv "Scripts\editppt.exe"
$EditableStageReady = $InstallState.editable_cli_ready -eq $true -and (Test-Path -LiteralPath $EditExe -PathType Leaf)
if ($EditableStageReady) {
    & $EditablePython -c "import editppt"
    $EditableStageReady = $LASTEXITCODE -eq 0
}
if (-not $EditableStageReady) {
    $InstallState.editable_cli_ready = $false
    Save-RuntimeInstallState
    & $EditablePython -m pip install --disable-pip-version-check --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "Unable to update pip in the editable-PPT runtime." }
    & $EditablePython -m pip install --disable-pip-version-check --upgrade (Join-Path $EditableSkill "cli")
    if ($LASTEXITCODE -ne 0) { throw "Unable to install the editable-PPT CLI." }
    & $EditablePython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "Editable-PPT prerequisite check failed." }
    $InstallState.editable_cli_ready = $true
    Save-RuntimeInstallState
} else {
    Write-Output "Reusing editable-PPT CLI for plugin $PluginVersion."
}

# Install a self-contained copy of the current Word workflow beside the two
# virtual environments.  The editppt wheel imports this explicit runtime
# boundary through a .pth file; it never guesses a repository sibling path.
$WorkflowPackageName = "workflow-" + ($PluginVersion -replace '[^A-Za-z0-9._-]', '_')
$CurrentWorkflowRoot = Join-Path $RuntimeRoot $WorkflowPackageName
if (-not (Test-Path -LiteralPath $CurrentWorkflowRoot -PathType Container)) {
    $WorkflowStage = Join-Path $RuntimeRoot ".$WorkflowPackageName.$PID.tmp"
    if (Test-Path -LiteralPath $WorkflowStage) {
        Remove-Item -LiteralPath $WorkflowStage -Recurse -Force
    }
    New-Item -ItemType Directory -Path $WorkflowStage | Out-Null
    foreach ($name in @("scripts", "schemas", "template")) {
        Copy-Item -LiteralPath (Join-Path $WorkflowSkill $name) -Destination $WorkflowStage -Recurse
    }
    Copy-Item -LiteralPath (Join-Path $PluginRoot "scripts\runtime_office.py") -Destination (Join-Path $WorkflowStage "scripts\runtime_office.py")
    Move-Item -LiteralPath $WorkflowStage -Destination $CurrentWorkflowRoot
}
$CurrentWorkflowScripts = Join-Path $CurrentWorkflowRoot "scripts"
$DetectorModules = @("background_text_detector.py")
$DetectorPackageName = "background-text-detector-" + ($PluginVersion -replace '[^A-Za-z0-9._-]', '_')
$CurrentDetectorRuntime = Join-Path $RuntimeRoot $DetectorPackageName
if (-not (Test-Path -LiteralPath $CurrentDetectorRuntime -PathType Container)) {
    $DetectorStage = Join-Path $RuntimeRoot ".$DetectorPackageName.$PID.tmp"
    if (Test-Path -LiteralPath $DetectorStage) {
        Remove-Item -LiteralPath $DetectorStage -Recurse -Force
    }
    New-Item -ItemType Directory -Path $DetectorStage | Out-Null
    foreach ($DetectorModule in $DetectorModules) {
        $DetectorSource = Join-Path $CurrentWorkflowScripts $DetectorModule
        if (-not (Test-Path -LiteralPath $DetectorSource -PathType Leaf)) {
            throw "Detector runtime module is missing: $DetectorSource"
        }
        Copy-Item -LiteralPath $DetectorSource -Destination (Join-Path $DetectorStage $DetectorModule)
    }
    Move-Item -LiteralPath $DetectorStage -Destination $CurrentDetectorRuntime
}
$CurrentImageSkillRoot = Join-Path $RuntimeRoot "generate-slide-body-image"
if (-not (Test-Path -LiteralPath (Join-Path $CurrentImageSkillRoot "scripts\codex_gpt_image.py") -PathType Leaf)) {
    $ImageSkillStage = Join-Path $RuntimeRoot ".generate-slide-body-image.$PID.tmp"
    if (Test-Path -LiteralPath $ImageSkillStage) {
        Remove-Item -LiteralPath $ImageSkillStage -Recurse -Force
    }
    Copy-Item -LiteralPath $ImageSkill -Destination $ImageSkillStage -Recurse
    if (Test-Path -LiteralPath $CurrentImageSkillRoot) {
        Remove-Item -LiteralPath $CurrentImageSkillRoot -Recurse -Force
    }
    Move-Item -LiteralPath $ImageSkillStage -Destination $CurrentImageSkillRoot
}
$WorkflowSitePackages = (& $WorkflowPython -c "import site; print(site.getsitepackages()[0])").Trim()
if ($LASTEXITCODE -ne 0 -or -not $WorkflowSitePackages) {
    throw "Unable to locate workflow site-packages for workflow injection."
}
$WorkflowPth = Join-Path $WorkflowSitePackages "word_to_editable_ppt_current_workflow.pth"
$WorkflowPthStage = "$WorkflowPth.$PID.tmp"
Set-Content -LiteralPath $WorkflowPthStage -Value $CurrentWorkflowScripts -Encoding ascii
Move-Item -LiteralPath $WorkflowPthStage -Destination $WorkflowPth -Force
$EditableSitePackages = (& $EditablePython -c "import site; print(site.getsitepackages()[0])").Trim()
if ($LASTEXITCODE -ne 0 -or -not $EditableSitePackages) {
    throw "Unable to locate editable-PPT site-packages for detector injection."
}
$LegacyEditableWorkflowPth = Join-Path $EditableSitePackages "word_to_editable_ppt_current_workflow.pth"
Remove-Item -LiteralPath $LegacyEditableWorkflowPth -Force -ErrorAction SilentlyContinue
$EditableDetectorPth = Join-Path $EditableSitePackages "awesome_background_text_detector_runtime.pth"
$EditableDetectorPthStage = "$EditableDetectorPth.$PID.tmp"
Set-Content -LiteralPath $EditableDetectorPthStage -Value $CurrentDetectorRuntime -Encoding ascii
Move-Item -LiteralPath $EditableDetectorPthStage -Destination $EditableDetectorPth -Force
Invoke-WithClearedPythonPath {
    $PreviousEditPptPython = $env:EDITPPT_PYTHON
    try {
        $env:EDITPPT_PYTHON = $EditablePython
        & $WorkflowPython -c "import editppt, workflow_v6_contract, workflow_v6_source, workflow_v6_image, workflow_v6_reconstruction; print('awesome-v6-workflow-boundary=ok')"
        if ($LASTEXITCODE -ne 0) { throw "Editable-PPT current workflow injection failed." }
        & $EditablePython -c "from pathlib import Path; import importlib.util, tempfile; from PIL import Image; from background_text_detector import capability_status, detect_background_text; assert importlib.util.find_spec('workflow_v6_contract') is None; d=tempfile.TemporaryDirectory(); p=Path(d.name)/'blank.png'; Image.new('RGB',(320,180),'white').save(p); s=capability_status(); assert s['available'],s; r=detect_background_text(p); assert r['background_text_detected'] is False,r; print('background-text-detector-smoke=ok'); d.cleanup()"
        if ($LASTEXITCODE -ne 0) { throw "Installed background text detector smoke failed." }
    } finally {
        if ($null -eq $PreviousEditPptPython) { Remove-Item Env:EDITPPT_PYTHON -ErrorAction SilentlyContinue }
        else { $env:EDITPPT_PYTHON = $PreviousEditPptPython }
    }
}
Remove-StaleEditablePptWorkflowPackages `
    -RuntimeRoot $RuntimeRoot `
    -CurrentWorkflowRoot $CurrentWorkflowRoot
Remove-StaleEditablePptDetectorPackages `
    -RuntimeRoot $RuntimeRoot `
    -CurrentDetectorRoot $CurrentDetectorRuntime
if ($RunningOnWindows -and -not $PortableSmokeTest) {
    & $EditablePython -c "import win32com.client; print('editppt-win32com=ok')"
    if ($LASTEXITCODE -ne 0) { throw "Editable-PPT Windows COM dependency probe failed." }
}

$EditWrapper = @"
@echo off
"$EditExe" %*
"@
$EditWrapperPath = Join-Path $BinDir "editppt.CMD"
$EditWrapperStage = "$EditWrapperPath.$PID.tmp"
Set-Content -LiteralPath $EditWrapperStage -Value $EditWrapper -Encoding ascii
Move-Item -LiteralPath $EditWrapperStage -Destination $EditWrapperPath -Force

$ResolvedOfficeExe = $null
$OfficeCliOptional = "not-detected"
if (-not $PortableSmokeTest) {
    if ($OfficeCliExe) {
        $ResolvedOfficeExe = [System.IO.Path]::GetFullPath($OfficeCliExe)
    } else {
        $ResolvedOfficeExe = Join-Path $env:LOCALAPPDATA "OfficeCLI\officecli.exe"
    }
    if (Test-Path -LiteralPath $ResolvedOfficeExe -PathType Leaf) {
        $OfficeCliOptional = "preinstalled"
        $OfficeWrapper = @"
@echo off
"$ResolvedOfficeExe" %*
"@
        $OfficeWrapperPath = Join-Path $BinDir "officecli.CMD"
        $OfficeWrapperStage = "$OfficeWrapperPath.$PID.tmp"
        Set-Content -LiteralPath $OfficeWrapperStage -Value $OfficeWrapper -Encoding ascii
        Move-Item -LiteralPath $OfficeWrapperStage -Destination $OfficeWrapperPath -Force
    } else {
        $ResolvedOfficeExe = $null
        Write-Output "OfficeCLI is not bundled and was not detected; optional OfficeCLI features remain disabled."
    }

    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $PathParts = @($UserPath -split ";" | Where-Object { $_ -and $_ -ne $BinDir })
    [Environment]::SetEnvironmentVariable("Path", (($BinDir + $PathParts) -join ";"), "User")
}

$env:Path = "$BinDir;$env:Path"
$env:CODEX_GPT_IMAGE_SKILL = $CurrentImageSkillRoot
$env:EDITPPT_EXE = $EditExe
if ($ResolvedOfficeExe) {
    $env:OFFICECLI_EXE = $ResolvedOfficeExe
}

$ReportPath = Join-Path $RuntimeRoot "runtime_report.json"
if ($PortableSmokeTest) {
    Invoke-WithClearedPythonPath {
        & $WorkflowPython -c "import flask, jsonschema, PIL, pypdf, pypdfium2, docx, pptx; import confirm_ui.server, workflow_v6_contract, workflow_v6_source, workflow_v6_image, workflow_v6_reconstruction; print('workflow-import-smoke=ok')"
        if ($LASTEXITCODE -ne 0) { throw "Workflow import smoke test failed." }
        & $WorkflowPython (Join-Path $PSScriptRoot "portable_e2e_smoke.py") --editppt $EditExe --output (Join-Path $RuntimeRoot "portable-e2e")
        if ($LASTEXITCODE -ne 0) { throw "Editable-PPT V6 object build/validate portable E2E failed." }
    }
    [ordered]@{
        portable_smoke_test = $true
        workflow_imports = "ok"
        editppt_cli = "v6-build-validate-ok"
        win32com_import = "skipped-portable"
        officecli_optional = $OfficeCliOptional
        workflow_python = $WorkflowPython
        editppt = $EditExe
    } | ConvertTo-Json | Set-Content -LiteralPath $ReportPath -Encoding utf8
    Write-Output "Portable clean-install smoke passed."
    Write-Output "Runtime report: $ReportPath"
    return
}

Invoke-WithClearedPythonPath {
    & $WorkflowPython (Join-Path $WorkflowSkill "scripts\doctor.py") --check-powerpoint --smoke-test --json $ReportPath
    if ($LASTEXITCODE -ne 0) {
        throw "Plugin runtime diagnostics failed. Review $ReportPath."
    }
}
$Report = Get-Content -Raw -Encoding UTF8 -LiteralPath $ReportPath | ConvertFrom-Json
if (-not $Report.workflow_ready) {
    throw "Plugin runtime is incomplete. Review $ReportPath and confirm Codex sign-in and editppt readiness."
}
if (-not $Report.render_backend_available) {
    Write-Warning "No local presentation renderer was detected. Installation remains usable; PowerPoint or LibreOffice will be used lazily when available."
}
if (-not $Report.high_quality_ready) {
    Write-Warning "The runtime works, but a CJK font or render backend is not ready. Review $ReportPath."
}

Write-Output "Editable PPT fixed-canvas-cm-v2 runtime is ready."
Write-Output "Runtime report: $ReportPath"
