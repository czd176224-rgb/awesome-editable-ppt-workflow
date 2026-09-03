param(
    [string]$OutputRoot = 'D:\AI项目管理\01-当前项目\黄石\task12-real-ab-evidence-20260903',
    [string]$BaselineProject = 'D:\AI项目管理\01-当前项目\黄石\task12-v122-baseline-clean-20260903-145835\project',
    [string]$CandidateAProject = 'D:\AI项目管理\01-当前项目\黄石\task12-v123-candidate-a-clean-20260903-152543\project',
    [string]$CandidateBProject = 'D:\AI项目管理\01-当前项目\黄石\task12-v123-candidate-b-clean-20260903-152543\project'
)

$ErrorActionPreference = 'Stop'
$pages = @(5, 10, 14, 20, 21, 31, 40, 41)
$wordPath = 'C:\Users\24927\Desktop\黄石市产业创新与母基金专业化管理合作建议_PPT生成专用Word副本_V3.docx'
$pngLogoPath = 'C:\Users\24927\Desktop\尚融logo.png'
$baselineCommit = 'abc3932cd20ba14e6b831278289d52a86d9bd130'
$candidateCommit = '32ff0cba6b3a5f65d9bb319d0aede20bfafee1f2'

function Read-Json([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
}

function Get-Sha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Get-CanonicalDigest($Value) {
    $json = $Value | ConvertTo-Json -Compress -Depth 20
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    $digest = [Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
    return [Convert]::ToHexString($digest).ToLowerInvariant()
}

function Get-PptxObjectCounts([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return [ordered]@{ present = $false; connectors = 0; native_charts = 0; text_labels = 0 }
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($Path)
    try {
        $connectors = 0
        $charts = 0
        $labels = 0
        foreach ($entry in $archive.Entries | Where-Object FullName -like 'ppt/slides/slide*.xml') {
            $reader = [IO.StreamReader]::new($entry.Open())
            try { $xml = $reader.ReadToEnd() } finally { $reader.Dispose() }
            $connectors += ([regex]::Matches($xml, '<p:cxnSp(?:\s|>)')).Count
            $charts += ([regex]::Matches($xml, '<c:chart(?:\s|>)')).Count
            $labels += ([regex]::Matches($xml, '<a:t(?:\s|>)')).Count
        }
        return [ordered]@{ present = $true; connectors = $connectors; native_charts = $charts; text_labels = $labels }
    }
    finally { $archive.Dispose() }
}

function Html([AllowNull()][string]$Value) {
    if ($null -eq $Value) { return '' }
    return [Net.WebUtility]::HtmlEncode($Value)
}

function Collect-Page([string]$RunName, [string]$Project, [int]$PageNumber) {
    $pad = $PageNumber.ToString('000')
    $workflow = Read-Json (Join-Path $Project 'workflow_v6.json')
    $pageState = $workflow.pages | Where-Object page_number -eq $PageNumber
    $materialPath = Join-Path $Project "02_v6\page_materials\page_$pad.json"
    $material = Read-Json $materialPath
    $directorPath = Join-Path $Project "02_v6\experiments\live-page-$pad\director_v2.json"
    $director = Read-Json $directorPath
    $imageReceiptPath = Join-Path $Project "04_v6\images\page_$pad.json"
    $imageReceipt = Read-Json $imageReceiptPath
    $failedOutcomePath = Join-Path $Project "04_v6\experiments\live-page-$pad\failed_outcome.json"
    $failedOutcome = Read-Json $failedOutcomePath
    $attemptOnePath = Join-Path $Project "04_v6\experiments\live-page-$pad\attempt_1.json"
    $attemptOne = Read-Json $attemptOnePath
    $evidencePath = Join-Path $Project "04_v6\experiments\live-page-$pad\evidence.jsonl"
    $reconstructionRoot = Join-Path $Project "05_v6\reconstruction_runs\page_$pad\pages\page_001"
    $pptxPath = Join-Path $reconstructionRoot 'page.pptx'
    $previewPath = Join-Path $reconstructionRoot 'preview.png'
    $validationPath = Join-Path $reconstructionRoot 'validation.json'
    $manifestPath = Join-Path $reconstructionRoot 'manifest.json'
    $validation = Read-Json $validationPath
    $manifest = Read-Json $manifestPath

    $attemptCount = 0
    if ($imageReceipt -and $imageReceipt.candidate) { $attemptCount = [int]$imageReceipt.candidate.attempt }
    elseif ($failedOutcome) { $attemptCount = @($failedOutcome.attempts).Count }
    elseif ($pageState) { $attemptCount = [int]$pageState.qa_attempts }
    $accepted = [bool]($imageReceipt -and $imageReceipt.status -eq 'accepted')
    $reconstructionPassed = [bool]($validation -and $validation.passed)
    $factCoverage = [bool]($accepted -and $imageReceipt.accepted_review.decision -eq 'accept' -and @($imageReceipt.accepted_review.problems).Count -eq 0)

    $assetName = "$RunName-page-$pad.png"
    $assetRelative = $null
    $sourceImagePath = $null
    $finalCandidate = if ($imageReceipt -and $imageReceipt.candidate) { $imageReceipt.candidate } elseif ($failedOutcome) { @($failedOutcome.attempts)[-1] } else { $null }
    if (Test-Path -LiteralPath $previewPath) { $sourceImagePath = $previewPath }
    elseif ($finalCandidate -and $finalCandidate.path) { $sourceImagePath = Join-Path $Project $finalCandidate.path }
    if ($sourceImagePath -and (Test-Path -LiteralPath $sourceImagePath)) {
        Copy-Item -LiteralPath $sourceImagePath -Destination (Join-Path $OutputRoot "assets\$assetName") -Force
        $assetRelative = "assets/$assetName"
    }

    $shapeLines = 0
    $manifestLabels = 0
    if ($manifest) {
        $shapeLines = @($manifest.shapes | Where-Object type -eq 'line').Count
        $manifestLabels = @($manifest.text_boxes).Count
    }
    $pptxCounts = Get-PptxObjectCounts $pptxPath

    return [ordered]@{
        page_number = $PageNumber
        title = $pageState.title
        document_content = [ordered]@{
            word_original = $material.word_original
            effective_body = $material.effective_body
            material_path = $materialPath
            material_sha256 = Get-Sha256 $materialPath
        }
        instructions = if ($director) {
            [ordered]@{
                director_path = $directorPath
                director_sha256 = Get-Sha256 $directorPath
                director_thread_id = $director.thread_id
                director_turn_id = $director.turn_id
                page_purpose = $director.value.page_plan.page_purpose
                primary_relationship = $director.value.page_plan.primary_relationship
                core_exhibit = $director.value.page_plan.core_exhibit
                reading_path = $director.value.page_plan.reading_path
                local_visuals = $director.value.page_plan.local_visuals
            }
        } else {
            [ordered]@{
                director_path = $null
                note = 'v1.2.2 baseline does not emit the v1.2.3 structured page-director artifact.'
                primary_relationship = $null
                core_exhibit = $null
            }
        }
        generation = [ordered]@{
            final_status = if ($pageState) { $pageState.state } else { 'missing' }
            accepted_image = $accepted
            first_pass_acceptance = [bool]($accepted -and $attemptCount -eq 1)
            attempt_count = $attemptCount
            correction_count = [Math]::Max(0, $attemptCount - 1)
            independent_review_reported_complete_fact_coverage = $factCoverage
            accepted_review_path = if ($imageReceipt) { $imageReceipt.accepted_review.authority_path } else { $null }
            accepted_review_problems = if ($imageReceipt) { @($imageReceipt.accepted_review.problems) } else { @() }
            failure_problems = if ($failedOutcome) { @($failedOutcome.failure_problems) } else { @() }
            failed_outcome_path = $failedOutcomePath
            compiled_initial_prompt_length_chars = if ($attemptOne) { $attemptOne.actual_prompt.Length } else { 0 }
            initial_prompt_path = $attemptOnePath
            initial_prompt_sha256 = Get-Sha256 $attemptOnePath
            prompt_receipt_path = Join-Path $Project "02_v6\page_image_prompts\page_$pad.receipt.json"
            image_receipt_path = $imageReceiptPath
            image_receipt_sha256 = Get-Sha256 $imageReceiptPath
            evidence_path = $evidencePath
            candidate_path = if ($finalCandidate -and $finalCandidate.path) { Join-Path $Project $finalCandidate.path } else { $null }
            candidate_sha256 = if ($finalCandidate) { $finalCandidate.sha256 } else { $null }
            request_identity = if ($attemptOne) { $attemptOne.request_identity } else { $null }
        }
        reconstruction = [ordered]@{
            passed = $reconstructionPassed
            pptx_path = $pptxPath
            pptx_sha256 = Get-Sha256 $pptxPath
            preview_path = $previewPath
            validation_path = $validationPath
            manifest_path = $manifestPath
            runtime_validation = if ($validation) { $validation.runtime_validation } else { $null }
            editable_connector_validation = [ordered]@{
                manifest_line_shapes = $shapeLines
                pptx_connector_shapes = $pptxCounts.connectors
            }
            editable_chart_validation = [ordered]@{
                pptx_native_chart_shapes = $pptxCounts.native_charts
                note = 'Zero native charts is allowed when the accepted analytical exhibit is reconstructed as editable native shapes and labels.'
            }
            editable_label_validation = [ordered]@{
                manifest_text_boxes = $manifestLabels
                pptx_text_labels = $pptxCounts.text_labels
            }
        }
        comparison_asset = $assetRelative
    }
}

New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $OutputRoot 'assets') -Force | Out-Null

$runSpecs = @(
    [ordered]@{ name = 'baseline'; label = 'v1.2.2 official baseline'; project = $BaselineProject; commit = $baselineCommit },
    [ordered]@{ name = 'candidate-a'; label = 'v1.2.3 candidate A'; project = $CandidateAProject; commit = $candidateCommit },
    [ordered]@{ name = 'candidate-b'; label = 'v1.2.3 candidate B'; project = $CandidateBProject; commit = $candidateCommit }
)

$runs = foreach ($spec in $runSpecs) {
    $workflow = Read-Json (Join-Path $spec.project 'workflow_v6.json')
    $visualContract = $workflow.style_confirmation.contract
    $deckPath = Join-Path $OutputRoot "decks\$($spec.name).pptx"
    [ordered]@{
        run_id = Split-Path -Leaf (Split-Path -Parent $spec.project)
        name = $spec.name
        label = $spec.label
        project_root = $spec.project
        runtime_source_commit = $spec.commit
        plugin_id = $workflow.plugin_id
        plugin_version = $workflow.plugin_version
        word_project_sha256 = $workflow.word_source.sha256
        svg_logo_project_sha256 = $workflow.logo_source.sha256
        taskbook_digest = $workflow.director_confirmation.taskbook_digest
        visual_contract_digest = Get-CanonicalDigest $visualContract
        visual_contract = $visualContract
        confirmed_taskbook = $workflow.director_confirmation.taskbook
        confirmed_ui_digest = $workflow.confirmed_ui_digest
        selected_page_deck = [ordered]@{
            path = $deckPath
            sha256 = Get-Sha256 $deckPath
            manifest_path = [IO.Path]::ChangeExtension($deckPath, '.json')
        }
        pages = @($pages | ForEach-Object { Collect-Page $spec.name $spec.project $_ })
        outcome = $null
    }
}

foreach ($run in $runs) {
    $run.outcome = [ordered]@{
        requested_pages = $pages.Count
        accepted_images = @($run.pages | Where-Object { $_.generation.accepted_image }).Count
        reconstructed_pages = @($run.pages | Where-Object { $_.reconstruction.passed }).Count
        first_pass_acceptances = @($run.pages | Where-Object { $_.generation.first_pass_acceptance }).Count
        failed_or_incomplete_pages = @($run.pages | Where-Object { -not $_.reconstruction.passed }).Count
    }
}

$candidateIndependence = foreach ($pageNumber in $pages) {
    $a = $runs[1].pages | Where-Object page_number -eq $pageNumber
    $b = $runs[2].pages | Where-Object page_number -eq $pageNumber
    [ordered]@{
        page_number = $pageNumber
        director_artifacts_differ = [bool]($a.instructions.director_sha256 -and $b.instructions.director_sha256 -and $a.instructions.director_sha256 -ne $b.instructions.director_sha256)
        initial_image_artifacts_differ = [bool]($a.generation.candidate_sha256 -and $b.generation.candidate_sha256 -and $a.generation.candidate_sha256 -ne $b.generation.candidate_sha256)
        candidate_a_director_sha256 = $a.instructions.director_sha256
        candidate_b_director_sha256 = $b.instructions.director_sha256
        candidate_a_image_sha256 = $a.generation.candidate_sha256
        candidate_b_image_sha256 = $b.generation.candidate_sha256
    }
}

$result = [ordered]@{
    schema_version = 'huangshi-task12-real-ab-v1'
    generated_at = (Get-Date).ToString('o')
    selected_pages = $pages
    inputs = [ordered]@{
        word_path = $wordPath
        word_sha256 = Get-Sha256 $wordPath
        user_png_logo_path = $pngLogoPath
        user_png_logo_sha256 = Get-Sha256 $pngLogoPath
        production_svg_wrapper_sha256 = $runs[0].svg_logo_project_sha256
        svg_wrapper_note = 'The production V6 route requires SVG. The wrapper embeds the exact user PNG bytes; the embedded PNG hash was separately verified equal to user_png_logo_sha256.'
    }
    identity_invariants = [ordered]@{
        same_word_hash = (@($runs.word_project_sha256 | Select-Object -Unique).Count -eq 1)
        same_svg_logo_hash = (@($runs.svg_logo_project_sha256 | Select-Object -Unique).Count -eq 1)
        same_taskbook_digest = (@($runs.taskbook_digest | Select-Object -Unique).Count -eq 1)
        same_visual_contract_digest = (@($runs.visual_contract_digest | Select-Object -Unique).Count -eq 1)
        confirmation_method = 'browser automation; no user UI confirmation requested'
    }
    candidate_independence = @($candidateIndependence)
    manual_visual_audit_path = Join-Path $OutputRoot 'task-12-manual-visual-audit.json'
    manual_visual_audit = Read-Json (Join-Path $OutputRoot 'task-12-manual-visual-audit.json')
    runs = @($runs)
}

$jsonPath = Join-Path $OutputRoot 'task-12-ab-results.json'
$result | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $jsonPath -Encoding utf8

$cards = foreach ($pageNumber in $pages) {
    $baseline = $runs[0].pages | Where-Object page_number -eq $pageNumber
    $candidateA = $runs[1].pages | Where-Object page_number -eq $pageNumber
    $candidateB = $runs[2].pages | Where-Object page_number -eq $pageNumber
    $word = Html $baseline.document_content.effective_body
    $word = $word -replace "`r?`n", '<br>'
    $cells = foreach ($page in @($baseline, $candidateA, $candidateB)) {
        $image = if ($page.comparison_asset) { '<img src="{0}" alt="page {1}">' -f $page.comparison_asset, $pageNumber } else { '<div class="missing">No accepted/reconstructed artifact</div>' }
        $status = Html "$($page.generation.final_status); attempts=$($page.generation.attempt_count); reconstruction=$($page.reconstruction.passed)"
        '<td><div class="meta">{0}</div>{1}</td>' -f $status, $image
    }
    $relationship = if ($candidateA.instructions.primary_relationship) { Html $candidateA.instructions.primary_relationship.description } else { 'not available' }
    $core = if ($candidateA.instructions.core_exhibit) { Html $candidateA.instructions.core_exhibit.description } else { 'not available' }
    $notes = "A relationship: $relationship<br><br>A core exhibit: $core<br><br>A/B reconstruction: $($candidateA.reconstruction.passed)/$($candidateB.reconstruction.passed)"
    '<section><h2>Page {0} — {1}</h2><table><thead><tr><th>Word facts</th><th>v1.2.2 image</th><th>v1.2.3 batch 1</th><th>v1.2.3 batch 2</th><th>result notes</th></tr></thead><tbody><tr><td class="facts">{2}</td>{3}<td class="notes">{4}</td></tr></tbody></table></section>' -f $pageNumber, (Html $baseline.title), $word, ($cells -join ''), $notes
}

$html = @"
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>Task 12 Huangshi real A/B</title>
<style>body{margin:0;background:#eef2f1;color:#15201e;font:14px/1.5 "Microsoft YaHei",sans-serif}main{max-width:1900px;margin:auto;padding:24px}h1{margin:0 0 8px}p{color:#52615e}section{background:white;border:1px solid #ccd8d5;border-radius:12px;margin:20px 0;padding:16px;box-shadow:0 4px 18px #173f3512}h2{margin:0 0 12px;font-size:20px}table{border-collapse:collapse;width:100%;table-layout:fixed}th,td{border:1px solid #d4ddda;vertical-align:top;padding:8px}th{background:#e7efed}th:first-child,td:first-child{width:18%}th:last-child,td:last-child{width:16%}img{width:100%;height:auto;display:block}.facts{font-size:12px}.notes{font-size:12px}.meta{font-size:11px;color:#53625f;margin-bottom:6px}.missing{min-height:120px;display:grid;place-items:center;color:#a22626;background:#fafafa}</style>
</head><body><main><h1>Huangshi Task 12 — real plugin A/B comparison</h1><p>Same Word bytes, logo bytes, taskbook, and visual contract. Document content is kept in the first column; generated instructions are reported only in result notes and the machine-readable manifest.</p>$($cards -join "`n")</main></body></html>
"@
$htmlPath = Join-Path $OutputRoot 'task-12-ab-comparison.html'
$html | Set-Content -LiteralPath $htmlPath -Encoding utf8

[ordered]@{ result_manifest = $jsonPath; comparison_html = $htmlPath; runs = $runs | ForEach-Object { $_.project_root } } | ConvertTo-Json -Depth 5
