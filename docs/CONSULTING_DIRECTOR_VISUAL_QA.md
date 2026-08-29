# Consulting Director Visual QA

This document separates public structural regression from private visual acceptance. Public tests prove the consulting-director contract without publishing customer material. Private tests evaluate actual rendered page bodies locally and must never add source documents, prompts, candidate images, screenshots, or absolute paths to Git.

## Public synthetic regression

Run:

```powershell
$env:PYTHONPATH='plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/scripts'
python -m pytest plugins/awesome-editable-ppt-workflow/skills/run-word-to-ppt-workflow/tests/test_consulting_director_regressions.py -q
```

The four synthetic cases cover a three-lane portfolio, five-stage capital loop, four-capability transformation chain, and four-row investment matrix. Every case must preserve the sealed six-section order, confirmed background/primary/secondary colors, explanatory lead, analytical backbone, explicit takeaway, fixed-layer exclusions, and absence of legacy director fields.

## Private four-page visual regression

Use a clean copy of the private project and the locally built plugin. The source project is read-only for the run. Evaluate pages 4, 19, 33, and 36 independently; do not reuse an old candidate or legacy director authority.

For each page, confirm all of the following:

- The entire 17:8 body reads as one continuous reporting composition, excluding the fixed title, upper-right logo, footer, and page number.
- The page states one business proposition, uses a visible analytical backbone, includes enough explanatory copy to understand the page, connects evidence to interpretation and conclusion, and ends with an explicit takeaway.
- Confirmed semantic colors retain their assigned meaning. The secondary color remains a sparse accent rather than a large fill, repeated card color, or decorative path.
- Supporting imagery functions as evidence or context. It does not become a decorative hero scene.
- The page contains no disconnected module grid, glossy 3D machinery, miniature factory or park, toy-model scene, neon/cyberpunk treatment, glowing track, or other AI-heavy spectacle unsuitable for a formal report.
- Text is readable at presentation scale, source-faithful, and free of generated fixed-layer content.

Record only a local pass/fail note with this non-sensitive shape:

```json
{
  "schema_version": "awesome-private-consulting-visual-smoke-v1",
  "plugin_version": "1.2.2",
  "pages": [
    {
      "page_number": 4,
      "result": "pass",
      "checks": {
        "coherent_argument": "pass",
        "explanatory_copy": "pass",
        "semantic_color": "pass",
        "report_grade_style": "pass",
        "fixed_layer_exclusion": "pass"
      }
    }
  ]
}
```

Store the private note outside the repository. Before committing, run `git status --short` and verify that no private source, prompt, image, screenshot, or result file appears.
