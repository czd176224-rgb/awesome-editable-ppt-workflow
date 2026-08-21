param(
    [switch]$SkipPortableSmoke,
    [switch]$PublicSnapshotOnly,
    [switch]$SkipOfficeTests
)

$ErrorActionPreference = "Stop"
$RepoRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$PreviousDontWriteBytecode = $env:PYTHONDONTWRITEBYTECODE
$env:PYTHONDONTWRITEBYTECODE = "1"
function Get-Sha256([string]$Path) {
    $algorithm = [System.Security.Cryptography.SHA256]::Create(); $stream = [System.IO.File]::OpenRead($Path)
    try { return ([System.BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace("-", "").ToLowerInvariant() }
    finally { $stream.Dispose(); $algorithm.Dispose() }
}
Push-Location $RepoRoot
try {
    $IsGitWorkTree = Test-Path -LiteralPath (Join-Path $RepoRoot ".git")
    if (-not $PublicSnapshotOnly) {
        $wordTests = @(Get-ChildItem "plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests" -Filter "test_*.py" -File | Sort-Object Name)
        if ($SkipOfficeTests) {
            $wordTests = @($wordTests | Where-Object { $_.Name -ne "test_awesome_attachment_render.py" })
        }
        for ($offset = 0; $offset -lt $wordTests.Count; $offset += 12) {
            $last = [Math]::Min($offset + 11, $wordTests.Count - 1)
            $batch = @($wordTests[$offset..$last] | ForEach-Object { $_.FullName })
            & python -m pytest -p no:cacheprovider @batch -q
            if ($LASTEXITCODE -ne 0) { throw "Word V6 test split failed at offset $offset." }
        }
        foreach ($suite in @(
            "plugins/awesome-editable-ppt-workflow/skills/generate-slide-body-image/tests",
            "plugins/awesome-editable-ppt-workflow/skills/reconstruct-editable-slide/cli/tests",
            "tests"
        )) {
            & python -m pytest -p no:cacheprovider $suite -q
            if ($LASTEXITCODE -ne 0) { throw "Release test suite failed: $suite" }
        }

        & python scripts/check_python_syntax.py
        if ($LASTEXITCODE -ne 0) { throw "Python compilation failed." }
        & python plugins/awesome-editable-ppt-workflow/scripts/check_current_runtime.py --repo-root $RepoRoot
        if ($LASTEXITCODE -ne 0) { throw "Current-runtime policy scan failed." }
    }
    if ($IsGitWorkTree) {
        & git diff --check
        if ($LASTEXITCODE -ne 0) { throw "Git whitespace validation failed." }
    } else {
        Write-Output "Git whitespace validation skipped for manifest-verified exported snapshot."
    }

    $parseErrors = @()
    Get-ChildItem -Recurse -Filter *.ps1 -File | ForEach-Object {
        $tokens = $null; $errors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$errors)
        $parseErrors += $errors
    }
    if ($parseErrors.Count) { $parseErrors | Format-List; throw "PowerShell parsing failed." }
    Get-ChildItem -Recurse -Filter *.json -File | Where-Object { $_.FullName -notmatch '[\\/](\.git|dist|\.superpowers)[\\/]' } | ForEach-Object {
        Get-Content -Raw -Encoding UTF8 -LiteralPath $_.FullName | ConvertFrom-Json | Out-Null
    }

    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("editable-ppt-release-gate-" + [guid]::NewGuid().ToString("N"))
    try {
        New-Item -ItemType Directory -Path $tempRoot | Out-Null
        $package = Get-Content -Raw -Encoding UTF8 package-info.json | ConvertFrom-Json
        if ($package.releaseTag -ne ("v" + [string]$package.pluginVersion)) { throw "releaseTag/version mismatch." }
        if ($IsGitWorkTree) {
            $publicRoot = Join-Path $tempRoot "public"
            & .\scripts\export_public_release.ps1 -OutputPath $publicRoot
        } else {
            $publicRoot = $RepoRoot
            & python scripts/check_public_release.py . --write-report public-release-audit.json
        }
        if ($LASTEXITCODE -ne 0) { throw "Public snapshot validation failed." }

        $distA = Join-Path $tempRoot "dist-a"; $distB = Join-Path $tempRoot "dist-b"
        & (Join-Path $publicRoot "scripts/package_release.ps1") -SourceRoot $publicRoot -OutputDirectory $distA
        if ($LASTEXITCODE -ne 0) { throw "First production package build failed." }
        & (Join-Path $publicRoot "scripts/package_release.ps1") -SourceRoot $publicRoot -OutputDirectory $distB
        if ($LASTEXITCODE -ne 0) { throw "Second production package build failed." }
        $zipA = Get-ChildItem $distA -Filter *.zip | Select-Object -First 1
        $zipB = Get-ChildItem $distB -Filter *.zip | Select-Object -First 1
        if ((Get-Sha256 $zipA.FullName) -ne (Get-Sha256 $zipB.FullName)) {
            throw "Production ZIP is not reproducible."
        }

        if (-not $SkipPortableSmoke) {
            Write-Output "Portable clean-install smoke"
            $runtime = Join-Path $tempRoot "runtime"; $bin = Join-Path $tempRoot "bin"
            & (Join-Path $publicRoot "plugins/awesome-editable-ppt-workflow/scripts/install_runtime.ps1") -RuntimeRoot $runtime -BinDir $bin -PortableSmokeTest
            if ($LASTEXITCODE -ne 0) { throw "Portable runtime installation failed." }
            & (Join-Path $publicRoot "verify.ps1") -RuntimeRoot $runtime -PortableSmokeTest
            if ($LASTEXITCODE -ne 0) { throw "Portable runtime verification failed." }
        }
    } finally {
        if (Test-Path -LiteralPath $tempRoot) { [System.IO.Directory]::Delete($tempRoot, $true) }
    }
} finally {
    Pop-Location
    if ($null -eq $PreviousDontWriteBytecode) { Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue }
    else { $env:PYTHONDONTWRITEBYTECODE = $PreviousDontWriteBytecode }
}
