"""Offline trial routing checks: no model, provider, or production data."""
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
import unittest
import tempfile
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))

def raises(error, match=""):
    return unittest.TestCase().assertRaisesRegex(error, match)
from complex_page_experiment import loop, review, provider

def test_review_route_is_required_and_validated():
    value = {"schema_version": "awesome-independent-visual-review-v1", "decision": "correct",
             "problems": [{"category": "primary_relationship", "detail": "Wrong plan", "repair_route": "replan"}]}
    assert review._validated_review(value)[1][0].repair_route == "replan"
    del value["problems"][0]["repair_route"]
    with raises(ValueError): review._validated_review(value)

def test_loop_replan_then_edit_shares_three_candidate_budget(tmp_path):
    workspace = NS(project_copy=tmp_path, experiment_id="live-page-001", recovery_round=None)
    (tmp_path / "02_v6/experiments/live-page-001").mkdir(parents=True)
    correction_operations = []
    def record_call(**kw):
        if kw["kind"] == "correction_decision":
            correction_operations.append(kw["operation"])
    recorder = NS(durable_call_count=lambda: 0, record_call=record_call,
                  record_candidate_preflight=lambda **kw: None)
    view = NS()
    plans, requests = [], []
    def direct(*args, **kw):
        revision = kw.get("revision", 1); plans.append(revision)
        if revision == 2:
            assert kw["review_feedback"][0]["repair_route"] == "replan"
            assert kw["previous_image"].name == "candidate1.png"
        return NS(revision=revision, actual_prompt=f"plan{revision}", selected_reference_ids=(),
                  page_plan={"numeric_authorities": []}, quality="high", model="fake", effort=None, duration_seconds=0)
    def request(*args, **kw): requests.append(kw); return kw
    def generate(*args, **kw):
        attempt=kw["attempt"]
        return NS(attempt=attempt,path=tmp_path/f"candidate{attempt}.png",request_identity=str(attempt))
    def inspect(*args, **kw):
        n=args[3].attempt
        route="replan" if n==1 else "edit"
        problems=() if n==3 else (review.ReviewProblem("primary_relationship","Wrong plan" if n==1 else "Wrong arrow",route),)
        return NS(decision="accept" if n==3 else "correct", problem_records=problems,
                  problems=tuple(p.detail for p in problems))
    replacements={"verify_source_unchanged":lambda *a:None,"load_accepted_image_seal":lambda *a:None,
      "_load_failed_outcome":lambda *a:None,"direct_page":direct,"build_experiment_image_request":request,
      "run_provider_attempt":generate,"preflight_candidate":lambda *a:NS(passed=True,sha256="a",problems=()),
      "review_candidate_once":inspect,"validate_published_review_authority":lambda *a,**kw:{},
      "seal_accepted_image":lambda *a,**kw:NS()}
    with ExitStack() as stack:
        for name, fn in replacements.items():stack.enter_context(patch.object(loop,name,fn))
        outcome=loop._run_candidate_loop_owned(workspace,timeout=1,recorder=recorder,
          material_view_factory=lambda *a:view,director_invoke=None,reviewer_invoke=None,provider_runner=None,max_corrections=2)
    assert plans==[1,2]
    assert [r["strategy"] for r in requests]==["initial","replan","edit_previous"]
    assert [r["attempt"] for r in requests]==[1,2,3]
    assert correction_operations == ["replan", "edit_previous"]
    assert outcome.status=="accepted"

def test_replan_cannot_be_executed_as_local_edit():
    problem=review.ReviewProblem("primary_relationship","Wrong plan","replan")
    with raises(ValueError,match="replanning"):
        loop._local_correction(NS(problem_records=(problem,),problems=(problem.detail,)),NS(),next_attempt=2)

def test_provider_replan_keeps_predecessor_without_image_input(tmp_path):
    # Exercise the actual request builder; only filesystem authority dependencies are mocked.
    workspace=NS(page_number=1,project_copy=tmp_path)
    previous=NS(attempt=1,prompt_sha256="old",input_sha256s=())
    captured=[]
    state={"source_identity":"source","confirmed_ui_revision":1,"confirmed_ui_digest":"ui","pages":[{"material_receipt":{"digest":"material"}}]}
    from inspect import signature
    def seal(*args,**kwargs):captured.append(kwargs)
    with ExitStack() as stack:
        stack.enter_context(patch.object(provider,"validate_published_complete_page_material_view",lambda *a:None))
        stack.enter_context(patch.object(provider,"_selected_materials",lambda *a:([],[],[])))
        stack.enter_context(patch.object(provider,"load",lambda *a:state))
        stack.enter_context(patch.object(provider,"_publish_request_seal",seal))
        # ImageRequest has further project-authority fields; use a bag to inspect resolved transport only.
        stack.enter_context(patch.object(provider.workflow_v6_image,"ImageRequest",lambda **kw:NS(**kw)))
        view=NS(sha256="material",value={})
        request=provider.build_experiment_image_request(workspace,view,attempt=2,prompt="new plan",quality="high",selected_reference_ids=(),strategy="replan",previous_candidate=previous)
    assert request.input_images==() and request.operation=="generate"
    assert captured[0]["previous_candidate"] is previous
    assert captured[0]["strategy"]=="replan"

def test_review_goal_is_independent_of_actual_prompt():
    goal={"title":"Approved title","emphasis":"APPROVED_FOCUS","previous_connection":"","next_connection":""}
    prompt=review._review_prompt(NS(value={}),NS(selected_reference_ids=()),"WRONG_COMPILER_FOCUS",(),"taskbook",goal)
    assert "APPROVED_FOCUS" in prompt and "WRONG_COMPILER_FOCUS" in prompt
    assert "not factual or intent authority" in prompt
    assert "independently loaded user-confirmed page emphasis" in prompt

if __name__ == "__main__":
    test_review_goal_is_independent_of_actual_prompt()
    test_review_route_is_required_and_validated()
    with tempfile.TemporaryDirectory() as directory:
        test_loop_replan_then_edit_shares_three_candidate_budget(Path(directory))
        test_provider_replan_keeps_predecessor_without_image_input(Path(directory))
    test_replan_cannot_be_executed_as_local_edit()
    print("OFFLINE_REPLAN_CHECKS_PASSED")
