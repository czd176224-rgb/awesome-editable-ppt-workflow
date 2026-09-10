"""Offline checks for review of the director's verbatim Image2 prompt."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch
import json
import hashlib
import hmac
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from complex_page_experiment import director, review
from complex_page_experiment.director import DirectorArtifact


def _director_fixture():
    source = "已签约；实缴金额尚未披露。"
    image_prompt = (
        "以轻线条连接进展与判断边界。正文显示“已签约”，并明确显示"
        "“实缴金额尚未披露”。核心语义标签为“进展已确认，金额待披露”。"
    )
    material_view = SimpleNamespace(
        value={
            "page_number": 1,
            "fixed_page_title": "已有进展与判断边界",
            "complete_word_content": [
                {"source_block_id": "b1", "type": "paragraph", "text": source},
            ],
            "visual_contract": {
                "background_color": "#F7F6F2",
                "primary_color": "#17212B",
                "secondary_color": "#176B67",
                "cjk_font": "Microsoft YaHei",
            },
            "numeric_source_candidates": [],
        },
        multimodal_images=(),
        sha256="b" * 64,
    )
    value = {
        "schema_version": "awesome-page-design-v1",
        "page_number": 1,
        "quality": "high",
        "selected_references": [],
        "page_plan": {
            "content_inventory": [
                {
                    "source_block_id": "b1",
                    "source_quote": "已签约；",
                    "display_copy": "已签约",
                    "target": "body",
                },
                {
                    "source_block_id": "b1",
                    "source_quote": "实缴金额尚未披露。",
                    "display_copy": "实缴金额尚未披露",
                    "target": "body",
                },
            ],
            "context_bridges": [],
            "image_prompt": image_prompt,
            "quantitative_exhibits": None,
            "numeric_authorities": None,
        },
    }
    artifact = DirectorArtifact(
        value=value,
        actual_prompt=image_prompt,
        selected_reference_ids=(),
        quality="high",
        model="offline-director",
        effort=None,
        duration_seconds=0.0,
        model_provider="offline",
        usage={},
        runtime_trace={},
        thread_id="offline-thread",
        turn_id="offline-turn",
    )
    return SimpleNamespace(page_number=1, project_copy=Path(".")), material_view, artifact


class DirectImageReviewTest(unittest.TestCase):
    def test_review_accepts_direct_prompt_identity_without_six_part_fields(self):
        workspace, material_view, artifact = _director_fixture()
        with patch.object(review, "project_emphasis_pages", return_value=[]):
            review._validate_director(workspace, artifact, material_view)

    def test_review_rejects_tampered_prompt_string(self):
        workspace, material_view, artifact = _director_fixture()
        with patch.object(review, "project_emphasis_pages", return_value=[]):
            with self.assertRaisesRegex(ValueError, "director artifact identity"):
                review._validate_director(
                    workspace,
                    replace(artifact, actual_prompt=artifact.actual_prompt + "篡改"),
                    material_view,
                )

    def test_signed_direct_prompt_authority_rejects_changed_bytes(self):
        _workspace, material_view, artifact = _director_fixture()
        with tempfile.TemporaryDirectory(prefix="direct-review-authority-") as td:
            root = Path(td)
            (root / "02_v6/experiments/offline").mkdir(parents=True)
            workspace = SimpleNamespace(
                page_number=1,
                project_copy=root,
                experiment_id="offline",
                source_snapshot_sha256="a" * 64,
            )
            with patch.object(director, "signing_key", return_value=("test-key", b"k" * 32)), \
                 patch.object(director, "verification_key", return_value=b"k" * 32):
                director._publish_director_authority(workspace, material_view, artifact)
                review.validate_published_director_authority(
                    workspace, material_view, artifact
                )
                authority = root / "02_v6/experiments/offline/director_v2.json"
                value = json.loads(authority.read_text(encoding="utf-8"))
                old = dict(value)
                old["schema_version"] = "awesome-consulting-page-director-authority-v2"
                old.pop("hmac_sha256")
                old["hmac_sha256"] = hmac.new(
                    b"k" * 32, director._canonical_bytes(old).rstrip(b"\n"), hashlib.sha256
                ).hexdigest()
                authority.write_text(json.dumps(old), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "differs from published director authority"):
                    director.load_published_director_authority(workspace, material_view)
                value["actual_prompt"] += "篡改"
                authority.write_text(
                    json.dumps(value, ensure_ascii=False), encoding="utf-8"
                )
                with self.assertRaisesRegex(ValueError, "signature"):
                    review.validate_published_director_authority(
                        workspace, material_view, artifact
                    )

    def test_review_diagnoses_against_confirmed_authority_without_redesigning(self):
        _workspace, material_view, artifact = _director_fixture()
        candidate = SimpleNamespace(selected_reference_ids=())
        prompt = review._review_prompt(
            material_view,
            candidate,
            artifact.actual_prompt,
            (),
            "已确认任务书",
            {"title": "已有进展与判断边界", "emphasis": "判断边界"},
        )
        self.assertIn(
            "Do not reselect the layout, hierarchy, reading order, or visual form",
            prompt,
        )
        self.assertIn("confirmed style as an execution requirement", prompt)
        self.assertIn(artifact.actual_prompt, prompt)

    def test_review_allows_core_semantic_label_that_does_not_copy_full_title(self):
        _workspace, material_view, artifact = _director_fixture()
        prompt = review._review_prompt(
            material_view,
            SimpleNamespace(selected_reference_ids=()),
            artifact.actual_prompt,
            (),
            "已确认任务书",
            {"title": "已有进展与判断边界", "emphasis": "判断边界"},
        )
        self.assertIn(
            "A body-level core semantic label may overlap the fixed title's meaning",
            prompt,
        )


if __name__ == "__main__":
    unittest.main()
