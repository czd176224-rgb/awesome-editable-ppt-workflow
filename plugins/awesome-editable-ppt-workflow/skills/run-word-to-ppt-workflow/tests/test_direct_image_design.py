"""Disposable, offline checks for the director's unmodified Image2 handoff."""
import copy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from complex_page_experiment import director


def example():
    text = "已签约；实缴金额尚未披露。"
    view = SimpleNamespace(
        value={
            "page_number": 1,
            "fixed_page_title": "已有进展与判断边界",
            "complete_word_content": [
                {"source_block_id": "b1", "type": "paragraph", "text": text},
            ],
            "visual_contract": {
                "background_color": "#F7F6F2", "primary_color": "#17212B",
                "secondary_color": "#176B67", "cjk_font": "Microsoft YaHei",
            },
            "numeric_source_candidates": [],
        },
        multimodal_images=(),
    )
    value = {
        "schema_version": "awesome-page-design-v1", "page_number": 1,
        "quality": "high", "selected_references": [],
        "page_plan": {
            "content_inventory": [
                {"source_block_id": "b1", "source_quote": "已签约；",
                 "display_copy": "已签约", "target": "body"},
                {"source_block_id": "b1", "source_quote": "实缴金额尚未披露。",
                 "display_copy": "实缴金额尚未披露", "target": "body"},
            ],
            "context_bridges": [],
            "image_prompt": "先呈现已有进展，再说明判断边界。正文显示“已签约”；以清晰文字突出“实缴金额尚未披露”。",
            "quantitative_exhibits": None, "numeric_authorities": None,
        },
    }
    return view, value


class DirectDesignTest(unittest.TestCase):
    def test_one_source_block_can_have_several_visible_roles_without_layout_slots(self):
        view, value = example()
        try:
            selected = director._validate_director_value(value, view)
        except ValueError as exc:
            self.fail(f"Direct design must be accepted without structure slots: {exc}")
        self.assertEqual(selected, ())

    def test_missing_qualifier_is_rejected(self):
        view, value = example()
        value["page_plan"]["content_inventory"].pop()
        with self.assertRaisesRegex(ValueError, "omits"):
            director._validate_content_inventory(value["page_plan"], view)

    def test_coverage_record_cannot_claim_copy_absent_from_image_prompt(self):
        view, value = example()
        value["page_plan"]["image_prompt"] = "正文只显示已签约。"
        with self.assertRaisesRegex(ValueError, "image_prompt"):
            director._validate_content_inventory(value["page_plan"], view)

    def test_fixed_title_target_must_really_be_covered_by_confirmed_title(self):
        view, value = example()
        value["page_plan"]["content_inventory"][0]["target"] = "fixed_title"
        with self.assertRaisesRegex(ValueError, "fixed title"):
            director._validate_content_inventory(value["page_plan"], view)

    def test_reference_number_uses_selected_transport_order(self):
        view, value = example()
        value["selected_references"] = [{"material_id": "source-four", "use": "identity", "preserve": "identity"}]
        with patch.object(director, "_image_material_ids", return_value=("source-four",)):
            value["page_plan"]["image_prompt"] += " 使用Image-4。"
            with self.assertRaisesRegex(ValueError, "input order"):
                director._validate_director_value(value, view)
            value["page_plan"]["image_prompt"] = value["page_plan"]["image_prompt"].replace("Image-4", "Image-1")
            self.assertEqual(director._validate_director_value(value, view), ("source-four",))

    def test_visible_copy_lines_can_occupy_separate_places_in_the_design(self):
        view, value = example()
        value["page_plan"]["content_inventory"][1]["display_copy"] = "实缴金额\n尚未披露"
        value["page_plan"]["image_prompt"] = "左侧显示已签约；右侧标签实缴金额，下方注明尚未披露。"
        director._validate_content_inventory(value["page_plan"], view)
        value["page_plan"]["image_prompt"] = "显示已签约和实缴金额\\n尚未披露。"
        director._validate_content_inventory(value["page_plan"], view)
        value["page_plan"]["image_prompt"] = "显示已签约和实缴金额。"
        with self.assertRaisesRegex(ValueError, "image_prompt"):
            director._validate_content_inventory(value["page_plan"], view)

    def test_director_output_is_sent_verbatim_and_receives_confirmed_style(self):
        view, value = example()
        calls = []
        def invoke(project, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                value=copy.deepcopy(value), model="offline-test", effort=None,
                duration_seconds=0.0, model_provider="offline", usage={},
                safe_trace={}, thread_id="offline-thread", turn_id="offline-turn",
            )
        with tempfile.TemporaryDirectory(prefix="direct-design-") as td:
            root = Path(td)
            (root / "02_v6/experiments/offline").mkdir(parents=True)
            ws = SimpleNamespace(project_copy=root, page_number=1, experiment_id="offline")
            with patch.object(director, "_validate_material_view", return_value=()), \
                 patch.object(director, "project_emphasis_pages", return_value=[1], create=True), \
                 patch.object(director, "confirmed_taskbook_prompt", return_value="已确认受众和目的"), \
                 patch("deck_planning.confirmed_page_inputs", return_value={
                     "plan": {"title": "已有进展与判断边界", "emphasis": "边界"}, "context": [],
                     "composition": {"page_role": "content", "chapter_title": "运营判断"}}), \
                 patch.object(director, "_publish_director_authority"):
                complete_prompt = value["page_plan"]["image_prompt"]
                value["page_plan"]["image_prompt"] = "正文只显示已签约。"
                with self.assertRaisesRegex(ValueError, "image_prompt"):
                    director.direct_page(ws, view, timeout=1, invoke=invoke)
                self.assertEqual(len(list(root.rglob("*.returned-*.json"))), 1)
                value["page_plan"]["image_prompt"] = complete_prompt
                artifact = director.direct_page(ws, view, timeout=1, invoke=invoke)
                self.assertEqual(len(list(root.rglob("*.returned-*.json"))), 2)
                self.assertEqual(len(list(root.rglob("*.input.txt"))), 1)
        self.assertEqual(len(calls), 2)
        self.assertIn("#F7F6F2", calls[0]["prompt"])
        self.assertIn("Microsoft YaHei", calls[0]["prompt"])
        self.assertEqual(artifact.actual_prompt, value["page_plan"]["image_prompt"])
        self.assertEqual(artifact.actual_prompt, artifact.page_plan["image_prompt"])


if __name__ == "__main__":
    unittest.main()
