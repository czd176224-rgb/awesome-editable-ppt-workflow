"""Compatibility facade for shared revisioned workflow metrics."""

from __future__ import annotations

import sys
from pathlib import Path


EDITPPT_CLI = Path(__file__).resolve().parents[2] / "reconstruct-editable-slide" / "cli"
if str(EDITPPT_CLI) not in sys.path:
    sys.path.append(str(EDITPPT_CLI))

from editppt.runtime import workflow_metrics  # noqa: E402


PIPELINE_METRICS_FILE = workflow_metrics.PIPELINE_METRICS_FILE
SNAPSHOT_ROOT = workflow_metrics.SNAPSHOT_ROOT
page_metrics = workflow_metrics.page_metrics
build_pipeline_metrics = workflow_metrics.build_pipeline_metrics
write_pipeline_metrics = workflow_metrics.publish_pipeline_metrics
