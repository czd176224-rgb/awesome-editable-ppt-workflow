# Quantitative Chart Grammar and Financial Analysis Discipline for v1.2.3

Date: 2026-08-29  
Status: approved design  
Base release: `v1.2.2` (`abc3932`)  
Target release: `v1.2.3`

## 1. Outcome

v1.2.3 will absorb two transferable capabilities without depending on either proprietary product or course material:

- think-cell contributes a quantitative chart grammar: choose a visual encoding from the verified relationship among real values, then deliver it as editable PowerPoint objects.
- Training The Street contributes financial analysis discipline: preserve subject, period, unit, actual/forecast status, assumptions, arithmetic relationships, source, and the boundary between fact and judgment.

Neither source becomes a factual authority. The Word document remains the content authority, the confirmed V6 materials remain the sealed factual input, and the accepted page image remains the visual authority. The upgrade adds no Agent, model call, external dependency, financial-modeling engine, Excel input, or think-cell object.

The published `v1.2.2` tag remains immutable. This design is implemented only in `v1.2.3`.

## 2. Product Boundary

The input contract remains exactly two user files:

1. one paginated Word document;
2. one SVG logo.

v1.2.3 does not accept or require Excel/CSV. It therefore does not promise live workbook links, automatic refresh after workbook edits, think-cell compatibility, or recurring-report automation. A changed Word source requires a fresh project or an authorized rerun under the existing V6 rules.

The feature is generic. Ordinary consulting pages keep the free composition introduced in v1.2.2. Quantitative and financial rules activate only when confirmed page materials contain sufficient real data.

## 3. Existing Architecture to Reuse

The implementation preserves the current path:

```text
Word + SVG
  -> V6 source and material extraction
  -> one global confirmation
  -> one page director
  -> one initial Image2 request
  -> one independent visual reviewer
  -> at most two existing review-directed corrections
  -> one existing editable reconstruction worker
  -> final assembly and Office validation
```

The upgrade reuses:

- `chart_facts` as the numeric source;
- the six-part consulting prompt as the design instruction surface;
- the accepted 1904x896 image as the visual authority;
- the existing independent reviewer and its existing error categories;
- the existing Office/PPT chart support for native standard charts;
- native PowerPoint shapes, text, connectors, and groups for special charts;
- the existing test suite and Office validation boundary.

No parallel chart pipeline is introduced.

## 4. Data Authority and Flow

`chart_to_facts()` already retains chart title, series, values, time, unit, trend, and relationship. v1.2.3 tightens eligibility checks but does not create a second factual model.

For a page with confirmed `chart_facts`:

1. Material extraction validates that every quantitative encoding has real values and corresponding labels.
2. Local deterministic code derives only permitted presentation annotations when all required inputs are complete and compatible.
3. The page director receives allowed chart semantics and verified annotations. It selects the analytical layout but never calculates a value.
4. Image2 renders the accepted visual composition under the existing prompt and review loop.
5. The confirmed `chart_facts` payload and permitted derivations are copied into the reconstruction page directory as a sealed sidecar, with source page and SHA-256 recorded in existing page request/result metadata. This is an optional extension of existing records, not a new standalone schema or schema version.
6. The reconstruction worker uses `source.png` for geometry, hierarchy, palette, spacing, and visual rhythm. It uses the sealed sidecar only for series, categories, values, periods, units, and permitted formula results.
7. Local validation compares reconstructed chart data and labels against the sealed sidecar before the page can complete.

The sidecar cannot authorize new wording, new assumptions, new periods, new subjects, or a different visual argument.

## 5. Chart Eligibility

A page qualifies for quantitative chart encoding only when its confirmed materials provide the required numeric dimensions.

Minimum rules:

- a category or series label must accompany each encoded value;
- time-based charts require explicit periods or dates;
- units must be present or unambiguously shared by the source values;
- percentage composition requires a complete source-backed denominator or explicit percentages;
- bubble size requires a real third non-negative numeric variable;
- waterfall requires a source-supported start, changes, and end, or a complete sequence from which those positions can be computed deterministically;
- Gantt requires source-backed start/end dates or start/duration values;
- Mekko-like encoding requires complete values for both width and internal composition;
- qualitative importance, priority, confidence, risk, or sequence must never become height, width, area, position, bubble size, or proportion unless the Word source provides the numeric encoding.

If the conditions are incomplete or ambiguous, retain the source chart type when it is recoverable; otherwise fall back to a native table. Never invent missing numbers to keep a chart.

## 6. Six Rendering Primitives

The director may understand 20-30 familiar business-chart names, but reconstruction maintains only six rendering primitives.

### 6.1 Column and bar

Covers clustered column/bar, stacked, 100% stacked, ranking, contribution, target-versus-actual, and simple variance views. Deliver as native PowerPoint Chart objects when supported.

### 6.2 Line and point

Covers line, area when the source supports magnitude over time, dot plot, dumbbell, slope, historical-versus-forecast, and range comparison. Prefer native charts; use native shapes for small point/connector forms that PowerPoint does not represent cleanly.

### 6.3 XY

Covers scatter, bubble, quadrant, and risk-return plots. Axes and bubble size must have explicit numeric authority. Deliver as native PowerPoint charts when supported.

### 6.4 Cumulative bridge

Covers waterfall, value bridge, profit bridge, and increase/decrease driver analysis. Use native editable shapes, text, and connectors when a stable native chart cannot reproduce the accepted composition.

### 6.5 Time interval

Covers Gantt, roadmap, milestone, and project schedule views. Use editable task labels, bars, milestone symbols, and time axes. Do not infer dates from ordering language.

### 6.6 Variable rectangle

Covers Mekko/Marimekko-like charts, market-size-and-share views, and portfolio composition matrices. Width and height must both be source-backed. Use editable rectangles and labels.

These are implementation families, not new user-facing template choices. The current one-time global confirmation remains unchanged.

## 7. Prompt Contract

Do not add a seventh or eighth prompt section. Add two conditional contracts inside the existing six-part prompt.

Quantitative chart grammar:

> When the source contains real quantitative data, choose a professional chart backbone that matches the verified data relationship. Restore source-backed labels, legends, periods, units, totals, and permitted annotations. Never turn qualitative information into quantitative visual encoding.

Financial analysis discipline:

> When a page contains financial, valuation, investment, or operating data, preserve subject, period, unit, basis, actual/forecast status, assumptions, and total-to-component relationships. Keep facts, assumptions, calculated results, analytical judgments, and recommendations distinct. Do not calculate or add a financial metric unless the sealed source inputs fully authorize a deterministic presentation calculation.

The grammar maps relationships conservatively:

- change drivers -> cumulative bridge;
- time trend -> line or column;
- comparison or ranking -> bar/column or point;
- two numeric variables -> scatter;
- a verified third variable -> bubble size;
- size plus internal share -> variable rectangle;
- task plus explicit time interval -> Gantt;
- target, actual, and variance -> bar/point plus target line or variance annotation.

When more than one encoding is valid, prefer the simpler primitive. When the relationship is unclear, keep the source chart or table.

## 8. Deterministic Presentation Calculations

The model must not calculate. Existing local Python code may produce only presentation annotations whose inputs and formula are complete and compatible:

- total and subtotal;
- absolute difference;
- percentage share with a complete denominator;
- percentage change with valid comparable periods and a non-zero base;
- arithmetic mean for compatible values;
- minimum and maximum;
- CAGR with explicit start value, end value, period count, compatible units, and a mathematically valid domain;
- cumulative waterfall positions;
- Gantt duration from valid dates.

Each derivation records its input values, formula identifier, displayed result, unit, period, and source page. A missing subject, unit, period, denominator, base, or required endpoint disables the derivation. The page then renders without that annotation.

The feature does not calculate IRR, DCF, valuation multiples, market size, forecasts, scenarios, or investment returns unless the exact displayed result is already present in the Word source. It does not make investment judgments.

## 9. Financial Analysis Discipline

For financial, valuation, investment, and operating pages, the director and reviewer preserve:

- entity or subject;
- period and date basis;
- currency, unit, and scale;
- actual, budget, forecast, target, or scenario status;
- source-stated assumptions;
- component, subtotal, and total relationships;
- data source;
- the distinction among fact, assumption, calculated result, analytical judgment, and action recommendation.

The preferred analytical reading paths are selected only when supported by Word content:

- result -> drivers -> conclusion;
- history -> forecast;
- baseline -> scenario -> sensitivity;
- revenue -> cost -> profit;
- enterprise value -> net debt -> equity value;
- input -> output -> return;
- current state -> drivers -> target result;
- assumptions -> calculation -> result.

These are organization rules, not new facts and not a TTS visual style.

## 10. Visual Composition

Composition remains content-driven:

- a core quantitative page gives the chart the dominant body area and places the source-backed takeaway above or beside it;
- an explanatory page may place the chart beside source-backed drivers, evidence, or conclusion;
- charts are not forced into cards;
- decorative imagery must not dominate a data page;
- confirmed fonts, primary/secondary/background colors, density, and typography remain authoritative;
- chart styling uses restrained gridlines, direct labels where legible, explicit units and periods, and semantic color only when the meaning is labeled or otherwise unambiguous.

The result may resemble professional consulting chart practice but must not copy think-cell branding, UI, templates, proprietary objects, or documentation.

## 11. Editable Reconstruction

Reconstruction has three ordered outcomes:

1. Native PowerPoint Chart for supported standard charts.
2. Grouped editable shapes for waterfall, Gantt, Mekko-like, and other special forms.
3. Native table when reliable quantitative reconstruction is not possible.

The worker must never use a raster screenshot of a whole chart merely to pass visual QA. Every visible number and label remains editable. Complex non-data visual texture may remain raster only under the existing accepted-image rules, never as a substitute for chart structure.

The accepted image remains the sole visual authority. The sealed chart sidecar may correct numeric transcription and populate editable chart data, but it must not redesign the accepted page.

## 12. Reviewer Contract

Use the existing independent reviewer and existing problem categories. Extend their definitions; do not add a reviewer, scoring layer, or chart-specific model call.

The reviewer rejects when:

- length, position, area, width, height, or bubble size contradicts the sealed data relationship;
- a label, unit, period, entity, actual/forecast status, or total relationship changes;
- a qualitative statement becomes quantitative encoding;
- a derived annotation contradicts the sealed local formula result;
- a chart loses the evidence-to-interpretation-to-conclusion path required by the page;
- a financial judgment or metric is added beyond the Word source;
- decorative spectacle, card fragmentation, or imagery displaces the analytical chart.

Post-reconstruction local validation proves exact values and object editability. The model reviewer is not the authority for arithmetic equality.

## 13. Failure and Degradation

- uncertain chart mapping -> retain source chart type or table;
- incomplete calculation inputs -> omit the annotation;
- Image2 encoding conflicts with source data -> existing reviewer rejects the candidate;
- reconstructed values differ from sealed data -> local validation fails and blocks page completion;
- unsupported native chart -> use grouped editable shapes;
- unreliable special-chart reconstruction -> use a native table;
- missing required data -> never call another model and never invent a substitute;
- ordinary non-quantitative page -> unchanged v1.2.2 behavior.

No failure path adds a director, reviewer, candidate-selection UI, model call, or unbounded retry.

## 14. Expected Implementation Surface

Keep the diff within the current workflow wherever practical:

- `workflow_v6_materials.py`: eligibility, conservative relationship mapping, and deterministic presentation annotations;
- `consulting_prompt.py`: conditional quantitative and financial clauses inside the existing six sections;
- `review.py`: extend existing factual, relational, and unusable-composition definitions;
- `workflow_v6_reconstruction.py`: seal and route existing chart facts to reconstruction;
- `page-worker.md`: permit the sealed chart sidecar as numeric authority only;
- existing reconstruction/Office helpers: create native charts or editable shape groups;
- existing tests: contract, refusal, reconstruction, validation, and regression coverage.

Do not introduce a chart framework, finance library, new runtime service, new formal schema, or new top-level workflow.

## 15. Verification and Release Gate

Six real paginated-Word representative pages are required, one per rendering primitive:

1. column/bar comparison;
2. line/point historical-versus-forecast;
3. XY scatter or bubble;
4. waterfall/value bridge;
5. Gantt/time interval;
6. Mekko-like size-and-share.

Add one purely qualitative control page to prove that qualitative language is not numerically encoded.

Required checks:

- exact source series, categories, values, periods, and units survive extraction;
- every permitted derivation equals the local formula result and records its inputs;
- invalid or incomplete inputs disable the corresponding chart/annotation;
- standard outputs contain actual PowerPoint chart objects;
- special outputs contain editable grouped shapes and no whole-chart screenshot fallback;
- output values equal the sealed chart sidecar item by item;
- source and output page counts and pagination semantics remain unchanged;
- ordinary consulting-page prompt and reconstruction behavior do not regress;
- the run retains one director, one initial Image2 call, one reviewer, and at most two existing corrections;
- Office validation and the existing targeted/full test suites pass.

Run the seven-page representative A/B gate before a full deck. Any wrong number, wrong unit/period/entity, qualitative-to-quantitative invention, non-editable chart fallback, additional model call, or ordinary-page regression blocks release.

## 16. Non-Goals

- think-cell installation, licensing, object format, API, export mode, or compatibility claim;
- Excel/CSV input or live external data links;
- automatic refresh after a user edits an external workbook;
- TTS course content, templates, cases, exercises, branding, or certification language;
- DCF, three-statement, LBO, M&A, valuation, forecasting, or scenario engines;
- dozens of independent chart implementations;
- new user-facing chart-selection UI;
- new Agent, model call, reviewer, error category, dependency, or formal schema.

## 17. External Reference Boundary

The design draws only on generic, transferable product and training principles:

- think-cell chart catalogue and data-driven chart behavior: <https://www.think-cell.com/en/product/think-cell-charts>
- think-cell Excel data-link behavior, deferred from this release: <https://www.think-cell.com/en/resources/manual/exceldatalinks>
- think-cell computed annotations: <https://www.think-cell.com/en/resources/manual/chartdecorations>
- Training The Street model planning and organization: <https://trainingthestreet.com/planning-and-structuring-a-model-how-to-make-your-model-more-organized/>
- Training The Street public curriculum boundary: <https://trainingthestreet.com/public-courses/>

Do not copy proprietary software behavior, UI, templates, source code, course slides, exercises, or branded materials, and do not imply endorsement or partnership.

## 18. Approved Decisions

- Freeze v1.2.2; target v1.2.3.
- Keep Word + SVG as the only user inputs.
- Automatically select a better chart only when the verified data relationship is unambiguous.
- Use native PowerPoint Chart objects for standard charts and editable shape groups for special charts.
- Permit only deterministic presentation calculations with complete source inputs.
- Show a light source/period/unit indication on the page and retain full provenance in existing project records.
- Preserve the accepted image as visual authority and route sealed `chart_facts` as numeric authority.
- Support 20-30 business chart names through six rendering primitives.
- Deliver all six primitives in v1.2.3.
- Use content-driven page composition rather than mandatory full-page or card-based layouts.
- Add no Agent, model call, external dependency, financial engine, or formal schema.
