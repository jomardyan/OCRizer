# Using OCRizer from Python

Install the base package for headless processing; the `gui` extra is only needed
for the desktop interface. Install Tesseract with the English and Polish models
separately, as described in the README.

Use the planner and batch runner together to retain the same validation,
collision handling, worker isolation and cancellation as the CLI. Save this
example in a Python script and run it as a file:

```python
import multiprocessing
from pathlib import Path

from ocrizer.batch import BatchRunner
from ocrizer.config import OcrSettings, OutputSettings
from ocrizer.planner import plan_jobs


def main() -> None:
    settings = OcrSettings(language="auto", export_text=True)
    output = OutputSettings(output_dir=Path("processed"))
    plan = plan_jobs(["documents"], output, batch=True, export_text=settings.export_text)
    runner = BatchRunner(plan.jobs, settings, workers=2)
    summary = runner.run()
    for result in summary.results:
        print(result.job.input_path, result.status.value, result.output_path, result.message)
    print(summary.describe())
    if summary.failed or summary.cancelled:
        raise SystemExit(1)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
```

The `if __name__ == "__main__"` guard is required because workers use the
`spawn` multiprocessing start method on every platform. Running this directly
in an interactive shell/notebook without an importable main module is not
supported. `run()` blocks; desktop/event-loop callers should put it in a
background thread. Call `runner.cancel()` from another thread to stop the
batch. A runner instance represents one batch and cannot be reused.

`BatchRunner(..., on_event=callback)` calls `callback(BatchEvent)` for document
start, progress and finish events from the runner thread. Marshal updates to
your UI's main thread where required. `summary.results` contains a `JobResult`
per input, including status, output/text paths, language, timing, and actionable
error details. Existing searchable documents can return `SKIPPED` with no
output file.

Pass `tesseract_path` to `BatchRunner` to select a non-default executable.
Dependency validation is enabled by default. Missing tools raise
`MissingDependencyError`; invalid settings raise `ConfigError`, and invalid
paths/plans raise `UsageError`, all from `ocrizer.errors`. Document processing
failures are returned as failed results so other inputs can continue.

The default conflict policy chooses a new output filename and preserves the
source. If you explicitly choose `ConflictPolicy.OVERWRITE` or
`OutputSettings(in_place=True)`, pass `overwrite=True` to `BatchRunner` as well.
Keep `export_text` consistent in both `OcrSettings` and `plan_jobs` so the planner
reserves sidecar names before any workers start.
