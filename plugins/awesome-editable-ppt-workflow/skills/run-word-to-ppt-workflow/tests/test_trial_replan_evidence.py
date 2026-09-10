"""Offline causal-chain checks using the real durable EvidenceRecorder."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from complex_page_experiment.evidence import EvidenceRecorder


class ReplanEvidenceTest(unittest.TestCase):
    def test_replan_then_edit_requires_and_persists_correction_decisions(self):
        # All events are synthetic and confined to a disposable local directory.
        # No provider, model, production files, or mocked recorder is involved.
        with tempfile.TemporaryDirectory(prefix="replan-evidence-") as td:
            base = Path(td)
            project = base / "project"
            root = project / "04_v6" / "experiments" / "replan-unit"
            root.mkdir(parents=True)
            (base / "source_snapshot.json").write_text(json.dumps({
                "experiment_id": "replan-unit", "page_number": 1,
                "source_snapshot_sha256": "a" * 64,
            }), encoding="utf-8")
            recorder = EvidenceRecorder(root, project_copy=project, experiment_id="replan-unit")

            def call(kind, attempt, operation="offline-test", **metadata):
                recorder.record_call(
                    kind=kind, attempt=attempt, model="deterministic-local",
                    effort=None, operation=operation, duration_seconds=0.0,
                    status="ok", metadata=metadata,
                )

            def passed(attempt):
                recorder.record_candidate_preflight(
                    attempt=attempt, candidate_sha256=str(attempt) * 64,
                    request_identity=str(attempt + 3) * 64, passed=True, problems=(),
                )

            call("page_director", 1)
            call("image2", 1)
            passed(1)
            call("visual_review", 1)
            with self.assertRaisesRegex(ValueError, "causal path"):
                call("image2", 2)
            call("correction_decision", 1, "replan", problem_count=1, quota_bearing=False)
            call("page_director", 2)
            call("image2", 2)
            passed(2)
            call("visual_review", 2)
            call("correction_decision", 2, "edit_previous", problem_count=1, quota_bearing=False)
            call("image2", 3)
            events = [json.loads(line) for line in (root / "evidence.jsonl").read_text().splitlines()]
            self.assertEqual(
                [e["operation"] for e in events if e.get("kind") == "correction_decision"],
                ["replan", "edit_previous"],
            )
            self.assertEqual(
                [e["attempt"] for e in events if e.get("kind") == "image2"], [1, 2, 3],
            )
            # Reopening exercises validation of the persisted causal event chain.
            EvidenceRecorder(root, project_copy=project, experiment_id="replan-unit")


if __name__ == "__main__":
    unittest.main()
