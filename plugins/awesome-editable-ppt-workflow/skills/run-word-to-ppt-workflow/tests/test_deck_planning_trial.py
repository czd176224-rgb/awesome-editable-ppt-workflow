"""Disposable offline contract check; no model or network calls."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import json
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deck_planning import generate_plan, validate_pages, seal_plan, digest, source_digest
from director_taskbook import TASKBOOK_FIELDS, confirmed_taskbook_prompt, taskbook_digest


def planning_prompt_contract():
    from unittest.mock import patch
    from codex_subscription_runtime import CodexStructuredResult

    selected = [{"output_page_number": 1, "source_page_id": 2}]
    taskbook = dict.fromkeys(TASKBOOK_FIELDS, "已确认约束")
    complete_word = {
        "page_count": 3,
        "pages": [
            {"source_page_id": number, "blocks": [{"source_block_id": f"b{number}", "text": f"第{number}页"}]}
            for number in range(1, 4)
        ],
    }
    selected_word = {"pages": [complete_word["pages"][1]]}
    plan = {"pages": [{"output_page_number": 1, "chapter_title": "章节", "title": "页面目的", "emphasis": "主要事实与次要说明",
                       "previous_connection": "承接前文", "next_connection": "引出后文"}]}
    captured = {}

    def stub(project, **kwargs):
        captured["prompt"] = kwargs["prompt"]
        return CodexStructuredResult(
            plan, "offline-thread", "offline-turn", "stub", "offline", "offline", None, {}, {},
        )

    with TemporaryDirectory() as tmp:
        project = Path(tmp)
        (project / "00_source").mkdir()
        (project / "02_v6").mkdir()
        (project / "00_source/source.docx").write_bytes(b"disposable source")
        (project / "02_v6/paginated_word_source.json").write_text(
            json.dumps(selected_word, ensure_ascii=False), encoding="utf-8",
        )
        with patch("extract_docx_pages.extract_auto", return_value=complete_word), patch(
            "codex_subscription_runtime.invoke_structured", stub,
        ):
            assert generate_plan(project, taskbook, selected) == plan

    prompt, request_text = captured["prompt"].split("\n", 1)
    request = json.loads(request_text)
    assert request["complete_word"] == complete_word
    assert request["selected_word"] == selected_word
    assert set(request["taskbook"]) == set(TASKBOOK_FIELDS)
    assert "优先阅读" not in prompt
    assert "页面目的、原文信息主次与前后衔接" in prompt
    assert "不指定版式、图形类别或空间位置" in prompt

    with patch("director_taskbook.load_confirmed_taskbook", return_value=taskbook):
        downstream = confirmed_taskbook_prompt(project)
    assert "core_exhibit" not in downstream
    assert "support_groups" not in downstream
    assert "local_visuals" not in downstream
    assert "confirmed title, emphasis, previous_connection, and next_connection" in downstream
    assert "Do not prescribe layouts, graphic types, or placements" not in downstream
    assert "the page director decides the concrete layout, graphic expression, and placement" in downstream

def main():
    planning_prompt_contract()
    selected = [{"output_page_number": 1, "source_page_id": 20}]
    plan = {"pages": [{"output_page_number": 1, "chapter_title": "章节", "title": "标题", "emphasis": "原文重点", "previous_connection": "", "next_connection": ""}]}
    assert validate_pages(plan, selected) == plan
    with TemporaryDirectory() as tmp:
        project = Path(tmp)
        (project / "00_source").mkdir()
        (project / "02_v6").mkdir()
        (project / "00_source/source.docx").write_bytes(b"disposable source")
        book = dict.fromkeys(TASKBOOK_FIELDS, "test")
        record = {"taskbook_digest": taskbook_digest(book), "source_digest": source_digest(project), "selection_digest": digest(selected)}
        (project / "02_v6/deck_plan_draft.json").write_text(json.dumps(record), encoding="utf-8")
        assert seal_plan(project, book, selected, plan)["plan"] == plan
        changed = {**book, "presenter": "changed"}
        try:
            seal_plan(project, changed, selected, plan)
        except ValueError:
            pass
        else:
            raise AssertionError("stale taskbook was accepted")
        try:
            validate_pages(plan, [{"output_page_number": 2}])
        except ValueError:
            pass
        else:
            raise AssertionError("reordered page accepted")
    print("deck planning offline contract: PASS")

def end_to_end(source):
    import shutil
    from unittest.mock import patch
    from codex_subscription_runtime import CodexStructuredResult
    from confirm_ui.server import create_app
    from workflow_v6_state import load
    from deck_planning import confirmed_page_plan
    with TemporaryDirectory() as tmp:
        project = Path(tmp) / "project"
        shutil.copytree(source, project)
        client = create_app(project).test_client()
        rec = client.get("/api/recommendations").get_json()
        template = rec["templates"][0]
        selected = json.loads((project / "02_v6/page_composition.json").read_text(encoding="utf-8"))["pages"]
        payload = {**template["defaults"], "submission_id": "offline-plan-check", "revision": 0,
                   "selected_director_template_id": template["id"], "director_taskbook": template["director_taskbook"],
                   "confirmed_pages": selected, "structure_confirmed": True}
        calls = []
        def stub(project, **kwargs):
            data = json.loads(kwargs["prompt"].split("\n", 1)[1])
            assert data["complete_word"]["page_count"] == 42
            assert len(data["complete_word"]["pages"]) == 42
            assert set(data["taskbook"]) == set(TASKBOOK_FIELDS)
            assert [p["source_page_id"] for p in data["selected_pages"]] == [20, 21, 22]
            assert [p["source_page_id"] for p in data["selected_word"]["pages"]] == [20, 21, 22]
            calls.append(kwargs)
            value = {"pages": [{"output_page_number": n, "chapter_title": "章节", "title": f"离线测试标题{n}", "emphasis": f"离线重点{n}",
                               "previous_connection": "前文联系", "next_connection": "后文联系"} for n in range(1, 4)]}
            return CodexStructuredResult(value, "offline-thread", "offline-turn", "stub", "offline", "offline", None, {}, {})
        with patch("codex_subscription_runtime.invoke_structured", stub):
            response = client.post("/api/deck-plan", json=payload)
        assert response.status_code == 200, response.get_json()
        assert len(calls) == 1
        payload["deck_plan"] = response.get_json()
        payload["deck_plan"]["pages"][0].update({
            "title": "用户编辑标题", "emphasis": "用户编辑重点",
            "previous_connection": "用户编辑前文衔接", "next_connection": "用户编辑后文衔接",
        })
        response = client.post("/api/confirm", json=payload)
        assert response.status_code == 200, response.get_json()
        state = load(project)
        assert state["director_confirmation"]["deck_plan"]["plan"]["pages"][0]["title"] == "用户编辑标题"
        assert confirmed_page_plan(project, 1) == payload["deck_plan"]["pages"][0]
        frozen = json.loads((project / "02_v6/page_composition.json").read_text(encoding="utf-8"))
        assert frozen["pages"][0]["fixed_page_title"] == "用户编辑标题"
        assert [p["source_page_id"] for p in frozen["pages"]] == [20, 21, 22]
        assert client.post("/api/confirm", json=payload).status_code == 409
    print("deck planning disposable API roundtrip: PASS (42 source pages, source IDs 20-22, sealed titles, second submit rejected)")

if __name__ == "__main__":
    main()
    if len(sys.argv) > 1:
        end_to_end(Path(sys.argv[1]))
