param(
    [string]$RuntimeRoot,
    [switch]$PortableSmokeTest,
    [switch]$MetadataOnly
)

$ErrorActionPreference = "Stop"
$RepoRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$PluginRoot = Join-Path $RepoRoot "plugins\awesome-editable-ppt-workflow"
$WorkflowSkill = Join-Path $PluginRoot "skills\run-word-to-ppt-workflow"
$ManifestPath = Join-Path $PluginRoot ".codex-plugin\plugin.json"
$PackageInfoPath = Join-Path $RepoRoot "package-info.json"
$PolicyScanner = Join-Path $PluginRoot "scripts\check_current_runtime.py"
$RuntimeProcessEnvironmentScript = Join-Path $PluginRoot "scripts\runtime_process_environment.ps1"
. $RuntimeProcessEnvironmentScript
$ExpectedWorkflowContract = "awesome-word-ppt-workflow-v1"
$RunningOnWindows = [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT

if (-not $RuntimeRoot) {
    $RuntimeRoot = Join-Path $env:USERPROFILE ".codex\plugin-runtimes\awesome-editable-ppt-workflow-fixed-canvas-cm-v2"
}
$RuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
$WorkflowPython = Join-Path $RuntimeRoot "workflow\Scripts\python.exe"
$EditablePython = Join-Path $RuntimeRoot "editable-ppt\Scripts\python.exe"
$EditPptExe = Join-Path $RuntimeRoot "editable-ppt\Scripts\editppt.exe"
$PreflightManifest = Get-Content -Raw -Encoding UTF8 -LiteralPath $ManifestPath | ConvertFrom-Json
$WorkflowPackageName = "workflow-" + ([string]$PreflightManifest.version -replace '[^A-Za-z0-9._-]', '_')
$CurrentWorkflowRoot = Join-Path $RuntimeRoot $WorkflowPackageName
$WorkflowScripts = Join-Path $CurrentWorkflowRoot "scripts"

foreach ($required in @($ManifestPath, $PackageInfoPath, $PolicyScanner)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Verification prerequisite is missing: $required"
    }
}

$Manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath $ManifestPath | ConvertFrom-Json
$PackageInfo = Get-Content -Raw -Encoding UTF8 -LiteralPath $PackageInfoPath | ConvertFrom-Json
$Marketplace = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $RepoRoot ".agents\plugins\marketplace.json") | ConvertFrom-Json
if ($Manifest.name -ne "awesome-editable-ppt-workflow") {
    throw "Unexpected plugin name: $($Manifest.name)"
}
if ($PackageInfo.pluginVersion -ne $Manifest.version) {
    throw "package-info pluginVersion does not match plugin manifest version"
}
if ($PackageInfo.workflowContractVersion -ne $ExpectedWorkflowContract) {
    throw "package-info workflowContractVersion does not match the current workflow contract"
}
if ($PackageInfo.marketplacePreviewIdentity -ne $Marketplace.name) {
    throw "package-info marketplacePreviewIdentity does not match the local Marketplace name"
}
if ($Marketplace.interface.displayName -notmatch [regex]::Escape([string]$Manifest.version)) {
    throw "local Marketplace displayName does not contain the plugin version"
}
if ($PackageInfo.requiredUserFiles -ne 2) {
    throw "package-info must declare the paginated Word and required SVG logo"
}
if ($PackageInfo.requiredHumanConfirmationPhaseCount -ne 1) {
    throw "package-info must declare exactly one human confirmation phase"
}
if ($PackageInfo.confirmationInteraction -ne "single-global-confirmation") {
    throw "package-info must declare the single global confirmation interaction"
}
if ($PackageInfo.uiPreviewImagePolicy -ne "project-audit-only-never-image-input") {
    throw "UI preview audit image must be excluded from image generation"
}
if ($PackageInfo.imageSourcePixels -ne "service-original-dynamically-centered-to-17:8-then-uniformly-resized-to-1904x896-with-trace") {
    throw "package-info must declare truthful service dimensions and the dynamic 17:8 adaptation profile"
}
if ($PackageInfo.bodyImageSizes.speed -ne "1904x896" -or
    $PackageInfo.bodyImageSizes.balanced -ne "1904x896" -or
    $PackageInfo.bodyImageSizes.quality -ne "1904x896") {
    throw "package-info must keep all profiles on the exact 1904x896 V6 canvas"
}
if ($PackageInfo.bodyImageAspectPolicy -ne "dynamic-centered-17:8-crop-then-uniform-1904x896-no-stretch" -or $PackageInfo.everyPageCallsImage2 -ne $true) {
    throw "package-info must declare every-page Image2 and dynamic 17:8 adaptation without stretching"
}
if ($PackageInfo.geometryTolerancePercent -ne 0.1) {
    throw "package-info must declare the 0.1 percent geometry tolerance"
}
if ($PackageInfo.initialImageEndpoint -ne "adaptive-images/generate-or-edit") {
    throw "V6 initial page generation must use the adaptive generate-or-edit dispatcher"
}
if ($PackageInfo.localRepairEndpoint -ne "deterministic-mechanical-routing-with-model-fallback") {
    throw "V6 page repair must stay on the same provider and remain bounded by two review-directed corrections"
}
if ($PackageInfo.promptContractVersion -ne "consulting-page-director-v3-compact-page-plan" -or
    $PackageInfo.pageImagePolicy -ne "generate-without-refs-edit-with-confirmed-refs") {
    throw "package-info must declare the consulting director v3 compact page plan and confirmed-materials image policy"
}

if ($MetadataOnly) {
    Write-Output "verify-metadata-preflight=ok"
    exit 0
}

foreach ($required in @($WorkflowPython, $EditablePython, $EditPptExe)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Verification prerequisite is missing: $required"
    }
}
if (-not (Test-Path -LiteralPath $WorkflowScripts -PathType Container)) {
    throw "Installed current workflow scripts are missing: $WorkflowScripts"
}

& $WorkflowPython $PolicyScanner --repo-root $RepoRoot
if ($LASTEXITCODE -ne 0) {
    throw "Current-only runtime policy failed."
}

Invoke-WithClearedPythonPath {
    $PreviousEditPptPython = $env:EDITPPT_PYTHON
    try {
        $env:EDITPPT_PYTHON = $EditablePython
        & $WorkflowPython -c "import flask, jsonschema, PIL, pypdf, pypdfium2, docx, pptx; import confirm_ui.server, workflow_v6_contract, workflow_v6_source, workflow_v6_image, workflow_v6_reconstruction; print('workflow-runtime-imports=ok')"
        if ($LASTEXITCODE -ne 0) {
            throw "Workflow runtime import verification failed."
        }
        & $EditablePython -c "from pathlib import Path; import importlib.util, tempfile; from PIL import Image; from background_text_detector import capability_status, detect_background_text; assert importlib.util.find_spec('workflow_v6_contract') is None; d=tempfile.TemporaryDirectory(); p=Path(d.name)/'blank.png'; Image.new('RGB',(320,180),'white').save(p); s=capability_status(); assert s['available'],s; r=detect_background_text(p); assert r['background_text_detected'] is False,r; print('editable-runtime-detector=ok'); d.cleanup()"
        if ($LASTEXITCODE -ne 0) { throw "Editable-PPT detector runtime verification failed." }
        if ($RunningOnWindows -and -not $PortableSmokeTest) {
            & $EditablePython -c "import win32com.client; print('editppt-win32com=ok')"
            if ($LASTEXITCODE -ne 0) {
                throw "Editable-PPT Windows COM dependency verification failed."
            }
        }
    } finally {
        if ($null -eq $PreviousEditPptPython) { Remove-Item Env:EDITPPT_PYTHON -ErrorAction SilentlyContinue }
        else { $env:EDITPPT_PYTHON = $PreviousEditPptPython }
    }
}

if (-not $PortableSmokeTest) {
    & $EditPptExe --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Editable-PPT CLI verification failed." }
}

if ($PortableSmokeTest) {
    $ReportPath = Join-Path $RuntimeRoot "runtime_report.json"
    if (-not (Test-Path -LiteralPath $ReportPath -PathType Leaf)) {
        throw "Portable runtime report is missing: $ReportPath"
    }
    $Report = Get-Content -Raw -Encoding UTF8 -LiteralPath $ReportPath | ConvertFrom-Json
    if ($Report.portable_smoke_test -ne $true -or $Report.workflow_imports -ne "ok" -or $Report.editppt_cli -ne "v6-build-validate-ok" -or $Report.win32com_import -ne "skipped-portable") {
        throw "Portable runtime report did not record a successful clean-install smoke."
    }
} else {
    $PreviousImageSkill = $env:CODEX_GPT_IMAGE_SKILL
    try {
        $env:CODEX_GPT_IMAGE_SKILL = Join-Path $RuntimeRoot "generate-slide-body-image"
        Invoke-WithClearedPythonPath {
            & $WorkflowPython (Join-Path $WorkflowScripts "doctor.py") --check-powerpoint --smoke-test --require-high-quality
            if ($LASTEXITCODE -ne 0) {
                throw "High-quality workflow verification failed."
            }
        }
    } finally {
        if ($null -eq $PreviousImageSkill) { Remove-Item Env:CODEX_GPT_IMAGE_SKILL -ErrorAction SilentlyContinue }
        else { $env:CODEX_GPT_IMAGE_SKILL = $PreviousImageSkill }
    }
}

Write-Output "Verified $($Manifest.name) $($Manifest.version): awesome-word-ppt-workflow-v1, adaptive generate/edit Image2 bodies, light pre-reconstruction QA, editable reconstruction, fixed-layer mechanical assembly, optional Office validation, fixed-canvas-cm-v2."
