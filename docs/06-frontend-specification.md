# 06 — Command-line interaction specification

## Frontend decision

The MVP has no graphical or web frontend. Its user-facing surfaces are:

- C++ and Python command-line help/results.
- Structured JSON outputs.
- Markdown and static HTML research reports.

This is a **confirmed non-goal** from the selected project scope. A browser UI would repeat skills already demonstrated elsewhere and would divert work from systems correctness and research quality.

## Surface inventory

| Surface ID | Surface | Entry command/file | Primary responsibility |
| --- | --- | --- | --- |
| UI-001 | Global help | itchlab --help | Discover C++ subcommands and common options |
| UI-002 | Inspect result | itchlab inspect | Qualify source/framing and show bounded statistics |
| UI-003 | Replay progress/result | itchlab replay | Explain preflight, progress, completion and output identity |
| UI-004 | Validation result | itchlab validate | List passed/failed checks and corrective actions |
| UI-005 | Benchmark result | itchlab benchmark | Present environment, samples, throughput and digest |
| UI-006 | Research help | itchlab-research --help | Discover conversion/research subcommands |
| UI-007 | Dataset/model result | convert, build-dataset, train | Summarise immutable output and data/metrics |
| UI-008 | Simulation result | simulate | Summarise scenarios, fills, inventory and diagnostics |
| UI-009 | Report | report.md/report.html | Human review of methods, results and limitations |

There are no URL routes.

## Command hierarchy

    itchlab
    ├── inspect
    ├── replay
    ├── validate
    └── benchmark

    itchlab-research
    ├── convert
    ├── build-dataset
    ├── train
    ├── simulate
    └── report

Each leaf command owns its parsed options and delegates validated domain objects to services. Domain components never read argv or print directly.

## State ownership

| State | Owner | Lifetime |
| --- | --- | --- |
| Parsed raw options | Command adapter | Until validation |
| Validated config | Immutable config domain object | Whole command |
| Progress counters | Command coordinator/replay engine | Running command |
| Cancellation flag | Process-level CancellationToken | Running command |
| Book/replay state | C++ ReplayEngine/OrderBook | One source run |
| Dataset lazy plan | Python dataset service | One transformation |
| Model/simulation state | Python service | One immutable run |
| Completion metadata | Manifest builder | Persisted |

## Help behaviour

- Global help lists subcommands in workflow order.
- Subcommand help contains purpose, required arguments, defaults, units, one valid example and relevant exit-code categories.
- Every time value names its unit in the option or help text.
- Help does not imply live trading or profitability.
- --version prints semantic application version, Git revision and dirty status when available.

## Configuration behaviour

- Non-trivial commands accept a JSON config rather than dozens of flags.
- CLI flags may override only path/log/output-format fields for the MVP; scientific parameters remain in the version-controlled config.
- The command prints all validation failures in one pass when safe, ordered by JSON pointer.
- Unknown config keys fail schema validation instead of being ignored.
- Canonical effective config is stored in the run manifest.

## Navigation and workflow guidance

On success, each command prints:

1. What completed.
2. The immutable run/dataset/experiment/simulation ID.
3. Relative paths to the manifest and primary outputs.
4. The exact recommended next command.

On failure, it prints:

1. Stable error code and concise description.
2. Relevant bounded context.
3. A corrective action.
4. Whether partial files exist and how to inspect them.

The CLI never automatically starts the next expensive stage.

## Progress and loading behaviour

- Commands expected to exceed five seconds display progress on stderr.
- First update appears after five seconds; subsequent updates appear at least every 30 seconds or ten million messages.
- TTY output may update one line. Non-TTY output uses newline-delimited records.
- Required progress fields: stage, messages, source bytes, selected events, elapsed time and error count.
- Optional fields: current symbol count, throughput and output bytes.
- A spinner without numeric state is insufficient.
- stdout remains parseable when --format json is selected.

## Error presentation

Human form:

    ERR_ORDER_REFERENCE: Cancel references unknown order 90210155.
    Message 1,928,830 at source byte 81,202,119.
    No completed output was published. Inspect the source or rerun with debug logging.

JSON form follows the common envelope in 05-api-contracts.md.

Multiple config errors are presented together. Runtime stream errors stop at the first fatal point in strict mode.

## Empty and degraded states

- Empty input: error and no run.
- Valid replay with no selected events: completed empty replay, clearly labelled; downstream modelling refuses it.
- No plot backend: report completes with tables/text and warns that plots were omitted.
- Degraded permissive replay: yellow when colour is enabled, plus the literal word DEGRADED; never colour alone.
- No fills in a simulation: valid result, with zero-fill diagnostics and no division-by-zero metrics.
- Single-class partition: model command fails before training with class counts.

## Cancellation behaviour

- Ctrl-C once requests graceful cancellation and prints “Cancellation requested; closing partial outputs”.
- Ctrl-C twice may terminate immediately and warns that cleanup may be incomplete.
- Exit code is 130 for graceful cancellation.
- A cancelled run never prints the same completion wording or icon as success.

## Terminal and responsive behaviour

- Default human tables adapt at 80, 120 and wider columns.
- At widths below 80 columns, use vertical key/value output rather than truncating identifiers.
- Long paths are shortened in the middle for display; JSON retains full values.
- No essential result depends on cursor positioning.
- TERM=dumb disables colour and line rewriting automatically.
- NO_COLOR is respected in addition to --no-colour.

## Accessibility behaviour

- Status uses words and symbols, never colour alone.
- Colour contrast is not relied upon because terminal themes vary.
- Unicode decoration is optional; --ascii restricts output to ASCII.
- Tables have a JSON alternative and reports include plain-text summaries beside plots.
- HTML reports use semantic heading order, table headers, alt text and keyboard-accessible links.
- Animation is absent or disabled when non-interactive.

## Keyboard interaction

The CLI requires only normal shell editing, Enter and Ctrl-C. There are no custom full-screen keybindings. HTML reports are read-only and need no custom keyboard handlers.

## Design-system requirements

No branded visual system is required. Consistency rules:

- Stable terminology from 00-project-overview.md.
- ISO dates and UTC timestamps for run metadata.
- Comma-separated integer counts in human output; raw integers in JSON.
- Rates always show denominator/unit.
- Prices show four decimal places when converted from Price4.
- Warning, degraded, failed, cancelled and completed are distinct literal statuses.
- Plot palette must be colour-vision-deficiency aware and each series must also differ by line style or marker where practical.

## Reusable presentation components

Implement small renderers rather than allowing domain modules to format output:

- ResultEnvelopeRenderer.
- ErrorRenderer.
- ProgressRenderer.
- ValidationCheckTable.
- RunSummaryTable.
- MetricTable.
- ReproductionCommandBlock.
- PlotCaption and textual summary helper.

These are presentation abstractions only; they must not own research logic.

## Data fetching and caching

There is no remote fetching. Commands read local immutable artefacts.

- Manifests may be cached in memory for one command.
- Hash verification is not skipped because a path was previously read by another process.
- A local validation cache is deferred; if added, it must key on path, size, modification time and expected hash and must never override a mismatch.

## Optimistic versus confirmed updates

No user-visible operation is reported optimistically.

- “Running” and progress are provisional.
- “Completed” is printed only after artefact flush, hash verification, atomic rename and completed-manifest publication.
- Model/simulation metrics are shown only from a completed result.
- Cancellation requests are acknowledged as requested, not immediately described as cancelled.

## Surface acceptance criteria

### UI-001/UI-006 help

- Every command appears with a one-sentence purpose.
- Required and optional values are distinguishable.
- Defaults and units are present.
- Help exits 0 and requires no data.

### UI-002 inspect

- Human and JSON forms contain equivalent facts.
- Unknown symbols and malformed framing have stable errors.
- Large counts are not truncated.

### UI-003 replay

- Progress never contaminates JSON stdout.
- Success is emitted only after validation.
- Empty, degraded, failed and cancelled runs use different explicit statuses.

### UI-004 validation

- Each requested check has pass/fail/not-run status.
- Overall failure exits non-zero.
- Hash mismatches show expected and actual hashes but no raw content.

### UI-005 benchmark

- Hardware/build/fixture identity accompanies performance numbers.
- Median and dispersion/repetition count are visible.
- A missing release build is labelled non-publishable.

### UI-007 dataset/model

- Output states parent identity, partitions and row/class counts.
- Test metrics are visually separated from validation metrics.
- Any test-set rerun warning is prominent.

### UI-008 simulation

- Output separates assumptions from results.
- Zero fills, inventory-limit suppression and anomaly counts are shown.
- P&L components and units are explicit.
- The scenario table identifies all required latency/cost cells and both strategy names.
- Drawdown, absolute gross-notional turnover and the signed 100 ms adverse-selection proxy show
  units; unavailable markouts and coverage are explicit.

### UI-009 report

- A reader can identify data, code, config, split dates and assumptions without inspecting source code.
- Every plot has labelled axes, units, caption and text summary.
- Limitations and negative results are not hidden in an appendix.
- A simulation report includes the exact relative `simulate` and `report` reproduction commands.
