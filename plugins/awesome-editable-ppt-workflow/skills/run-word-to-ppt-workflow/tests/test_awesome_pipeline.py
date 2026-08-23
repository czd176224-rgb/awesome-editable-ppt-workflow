from __future__ import annotations

import sys
import threading
import time
import json
from types import SimpleNamespace
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


from workflow_v6_contract import new_page, new_project
from workflow_v6_state import create


def _project(root: Path, pages: int = 2) -> Path:
    create(
        root,
        new_project(
            word_source={"path": "source.docx", "sha256": "a" * 64},
            logo_source={"path": "logo.svg", "sha256": "b" * 64},
            pages=[new_page(number, title=f"page {number}") for number in range(1, pages + 1)],
        ),
    )
    source = root / "02_v6"
    source.mkdir()
    source.joinpath("page_composition.json").write_text(json.dumps({
        "artifact_version": "page-composition-v1", "page_count": pages, "warnings": [],
        "pages": [
            {"output_page_number": number, "source_page_id": number, "page_role": "content", "role_source": "explicit", "chapter_title": "", "fixed_page_title": f"page {number}", "source_page_number": number, "material_source_block_ids": [f"block-{number}"], "visible_page_number": True}
            for number in range(1, pages + 1)
        ],
    }), encoding="utf-8")
    return root


def test_native_special_page_bypasses_creative_stages_and_assembles_with_content(tmp_path: Path) -> None:
    # Break caught: a cover invokes director/Image2/reviewer or is omitted from final assembly.
    from workflow_v6_pipeline import PipelineConfiguration, PipelineDependencies, run_pages

    project = _project(tmp_path, pages=2)
    (project / "02_v6" / "page_composition.json").write_text(json.dumps({
        "artifact_version": "page-composition-v1", "page_count": 2, "warnings": [],
        "pages": [
            {"output_page_number": 1, "source_page_id": 1, "page_role": "cover", "role_source": "explicit", "chapter_title": "", "fixed_page_title": "Cover", "source_page_number": 1, "material_source_block_ids": ["b1"], "visible_page_number": False},
            {"output_page_number": 2, "source_page_id": 2, "page_role": "content", "role_source": "explicit", "chapter_title": "", "fixed_page_title": "Content", "source_page_number": 2, "material_source_block_ids": ["b2"], "visible_page_number": True},
        ],
    }), encoding="utf-8")
    calls = {"workspace": [], "director": [], "provider": [], "reviewer": [], "native": []}
    assembled: list[dict[int, object]] = []

    def loop(workspace, *, director_invoke, provider_runner, reviewer_invoke, **_kwargs):
        page = workspace["page"]
        director_invoke(page)
        provider_runner([str(page)], 1)
        reviewer_invoke(page)
        return SimpleNamespace(status="accepted", accepted={"page": page}, attempts=(), failure_problems=(), correction_count=0)

    report = run_pages(
        project, [1, 2],
        dependencies=PipelineDependencies(
            open_workspace=lambda root, page: calls["workspace"].append(page) or {"project": root, "page": page},
            evidence_recorder=lambda workspace: object(),
            candidate_loop=loop,
            director_invoke=lambda page: calls["director"].append(page),
            provider_runner=lambda request, timeout: calls["provider"].append(int(request[0])),
            reviewer_invoke=lambda page: calls["reviewer"].append(page),
            native_page_renderer=lambda root, page: calls["native"].append(page) or {"page_role": "cover", "page_pptx": "06_v6/pages/page_001/page.pptx"},
            assemble_project=lambda root, outcomes: assembled.append(outcomes),
        ),
        configuration=PipelineConfiguration(page_workers=2, initial_page_concurrency=2, maximum_page_concurrency=2),
    )

    assert calls == {"workspace": [2], "director": [2], "provider": [2], "reviewer": [2], "native": [1]}
    assert report.completed_pages == (1, 2)
    assert report.page_outcomes[1].status == "page_complete"
    assert report.to_dict()["page_outcomes"]["1"]["status"] == "page_complete"
    assert set(assembled[0]) == {1, 2}


def test_pipeline_dispatch_reads_composition_through_secure_project_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Break caught: dispatch follows a replaced composition pathname with Path.read_text().
    import workflow_v6_pipeline as pipeline
    import workflow_v6_secure_io as secure_io

    project = _project(tmp_path, pages=1)
    (project / "02_v6/page_composition.json").write_bytes(b"placeholder")
    composition = json.dumps({
        "artifact_version": "page-composition-v1", "page_count": 1, "warnings": [],
        "pages": [{"output_page_number": 1, "source_page_id": 1, "page_role": "content", "role_source": "explicit", "chapter_title": "", "fixed_page_title": "Content", "source_page_number": 1, "material_source_block_ids": ["b1"], "visible_page_number": True}],
    }).encode()
    seen: list[tuple[Path, Path]] = []
    monkeypatch.setattr(secure_io, "reject_reparse_chain", lambda path: seen.append((Path(path), Path("."))))
    monkeypatch.setattr(secure_io, "read_bytes", lambda root, relative: seen.append((Path(root), Path(relative))) or composition)
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: pytest.fail("authority path must not be reopened"))

    report = pipeline.run_pages(
        project, [1],
        dependencies=pipeline.PipelineDependencies(
            open_workspace=lambda root, page: {"page": page}, evidence_recorder=lambda workspace: object(),
            candidate_loop=lambda workspace, **kwargs: {"accepted": workspace["page"]},
        ),
        configuration=pipeline.PipelineConfiguration(page_workers=1, initial_page_concurrency=1, maximum_page_concurrency=1),
    )

    assert report.completed_pages == (1,)
    assert (project.resolve(), Path("02_v6/page_composition.json")) in seen


def test_current_confirmed_project_missing_composition_fails_closed(tmp_path: Path) -> None:
    # Break caught: lost frozen composition silently routes a current cover through Image2.
    from workflow_v6_pipeline import PipelineConfiguration, PipelineDependencies, run_pages
    from workflow_v6_state import load, save

    project = _project(tmp_path, pages=1)
    state = load(project)
    state["style_confirmation"] = {"status": "confirmed", "contract": {"primary_color": "#17365D"}}
    state["confirmed_ui_revision"] = 1
    state["confirmed_ui_digest"] = "a" * 64
    state["page_materials_status"] = "pending"
    save(project, state)
    (project / "02_v6/page_composition.json").unlink()
    calls: list[int] = []

    with pytest.raises(ValueError, match="composition"):
        run_pages(
            project, [1],
            dependencies=PipelineDependencies(
                open_workspace=lambda root, page: calls.append(page), evidence_recorder=lambda workspace: object(),
                candidate_loop=lambda workspace, **kwargs: {},
            ),
            configuration=PipelineConfiguration(page_workers=1, initial_page_concurrency=1, maximum_page_concurrency=1),
        )

    assert calls == []


def test_explicit_legacy_confirmation_without_composition_remains_content_only(tmp_path: Path) -> None:
    # Break caught: fail-closed current authority accidentally removes the prior confirmed V6 pipeline.
    from workflow_v6_pipeline import PipelineConfiguration, PipelineDependencies, run_pages

    project = tmp_path / "legacy"
    (project / "confirm_ui").mkdir(parents=True)
    (project / "confirm_ui/result.json").write_text(json.dumps({
        "status": "confirmed", "revision": 1, "confirmed_at": "2026-08-23T00:00:00+08:00",
        "production_profile": "balanced", "global_visual_contract": {},
        "confirmed_pages": [{"page_number": 1, "effective_body": "Legacy body"}],
    }), encoding="utf-8")
    calls: list[int] = []

    report = run_pages(
        project, [1],
        dependencies=PipelineDependencies(
            open_workspace=lambda root, page: calls.append(page) or {"page": page},
            evidence_recorder=lambda workspace: object(), candidate_loop=lambda workspace, **kwargs: {"accepted": 1},
        ),
        configuration=PipelineConfiguration(page_workers=1, initial_page_concurrency=1, maximum_page_concurrency=1),
    )

    assert report.completed_pages == (1,)
    assert calls == [1]


def test_run_pages_overlaps_independent_pages_and_reports_actual_stage_peak(tmp_path: Path) -> None:
    # Break caught: replacing the bounded page executor with sequential calls.
    from workflow_v6_pipeline import PipelineConfiguration, PipelineDependencies, run_pages

    project = _project(tmp_path)
    active = 0
    peak = 0
    lock = threading.Lock()
    both_started = threading.Event()
    release = threading.Event()

    def open_workspace(root: Path, page: int):
        return {"project": root, "page": page}

    def recorder(workspace):
        return object()

    def loop(workspace, **_kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                both_started.set()
        release.wait(2)
        with lock:
            active -= 1
        return {"accepted": workspace["page"]}

    dependencies = PipelineDependencies(
        open_workspace=open_workspace, evidence_recorder=recorder, candidate_loop=loop,
    )
    configuration = PipelineConfiguration(page_workers=2, initial_page_concurrency=2, maximum_page_concurrency=2)
    thread = threading.Thread(target=lambda: run_pages(project, [1, 2], dependencies=dependencies, configuration=configuration))
    thread.start()
    assert both_started.wait(1)
    release.set()
    thread.join(3)

    assert not thread.is_alive()
    assert peak == 2


def test_run_pages_isolates_page_failure_and_reuses_completed_page_receipt(tmp_path: Path) -> None:
    # Break caught: a failed page aborts unrelated pages or a completed page invokes the creative loop again.
    from workflow_v6_pipeline import PipelineConfiguration, PipelineDependencies, run_pages

    project = _project(tmp_path, pages=3)
    calls: list[int] = []
    accepted: set[int] = set()

    def loop(workspace, **_kwargs):
        page = workspace["page"]
        calls.append(page)
        if page in accepted:
            return {"accepted": page, "recovered": True}
        if page == 2:
            raise ValueError("one page is invalid")
        accepted.add(page)
        return {"accepted": page}

    dependencies = PipelineDependencies(
        open_workspace=lambda root, page: {"project": root, "page": page},
        evidence_recorder=lambda workspace: object(),
        candidate_loop=loop,
    )
    configuration = PipelineConfiguration(page_workers=2, initial_page_concurrency=2, maximum_page_concurrency=2)

    first = run_pages(project, [1, 2, 3], dependencies=dependencies, configuration=configuration)
    second = run_pages(project, [1], dependencies=dependencies, configuration=configuration)

    assert first.completed_pages == (1, 3)
    assert first.failed_pages == {2: "ValueError: one page is invalid"}
    assert second.completed_pages == (1,)
    # The runner re-opens the real loop for a resume; that loop's accepted seal
    # is the authority that skips external work.
    assert calls.count(1) == 2


def test_rate_limit_contracts_future_launches_without_cancelling_running_pages_and_recovers() -> None:
    # Break caught: a 429 cancels already-running work or permanently pins the scheduler at one.
    from adaptive_scheduler import AdaptiveScheduler, RoundOutcome

    scheduler = AdaptiveScheduler(4, initial_concurrency=3, maximum_concurrency=3)

    assert scheduler.record_round(RoundOutcome(rate_limits=1)).concurrency == 1
    assert scheduler.record_round(RoundOutcome(successes=1, completed=1, expected=1)).concurrency == 2
    assert scheduler.record_round(RoundOutcome(successes=1, completed=1, expected=1)).concurrency == 3


def test_mixed_429_batch_holds_gate_and_scheduler_at_one_until_a_later_round(tmp_path: Path) -> None:
    # Break caught: successes already in flight undo a same-batch 429 contraction.
    from adaptive_scheduler import SCHEDULER_STATE_FILE
    from workflow_v6_pipeline import PipelineConfiguration, PipelineDependencies, run_pages

    project = _project(tmp_path, pages=3)
    barrier = threading.Barrier(3)

    class TooManyRequests(Exception):
        status_code = 429

    def provider(request, _timeout):
        barrier.wait(timeout=1)
        if request[0] == "1":
            raise TooManyRequests("quota")
        time.sleep(0.03)

    def loop(workspace, *, provider_runner, **_kwargs):
        provider_runner([str(workspace["page"])], 1)
        return {"accepted": workspace["page"]}

    report = run_pages(
        project,
        [1, 2, 3],
        dependencies=PipelineDependencies(
            open_workspace=lambda root, page: {"project": root, "page": page},
            evidence_recorder=lambda workspace: object(), candidate_loop=loop,
            provider_runner=provider,
        ),
        configuration=PipelineConfiguration(
            page_workers=3, initial_page_concurrency=3, maximum_page_concurrency=3,
            image2_concurrency=3, provider_profile="speed",
        ),
    )

    assert report.completed_pages == (2, 3)
    assert report.failed_pages[1] == "TooManyRequests: quota"
    assert report.scheduler_concurrency == 1
    assert json.loads((project / SCHEDULER_STATE_FILE).read_text(encoding="utf-8"))["active_limit"] == 1


def test_recovered_page_does_not_restore_429_capacity_without_a_provider_success(tmp_path: Path) -> None:
    # Break caught: accepted recovery is incorrectly treated as Image2 stability.
    from adaptive_scheduler import SCHEDULER_STATE_FILE
    from workflow_v6_pipeline import PipelineConfiguration, PipelineDependencies, run_pages

    project = _project(tmp_path, pages=3)
    barrier = threading.Barrier(2)

    class TooManyRequests(Exception):
        status_code = 429

    def provider(request, _timeout):
        barrier.wait(timeout=1)
        if request[0] == "1":
            raise TooManyRequests("quota")
        if request[0] == "2":
            time.sleep(0.03)
            return None
        pytest.fail("accepted recovery must not call the provider")

    def loop(workspace, *, provider_runner, **_kwargs):
        if workspace["page"] == 3:
            return {"accepted": 3, "recovered": True}
        provider_runner([str(workspace["page"])], 1)
        return {"accepted": workspace["page"]}

    report = run_pages(
        project,
        [1, 2, 3],
        dependencies=PipelineDependencies(
            open_workspace=lambda root, page: {"project": root, "page": page},
            evidence_recorder=lambda workspace: object(), candidate_loop=loop,
            provider_runner=provider,
        ),
        configuration=PipelineConfiguration(
            page_workers=2, initial_page_concurrency=2, maximum_page_concurrency=3,
            provider_profile="speed",
        ),
    )

    assert report.completed_pages == (2, 3)
    assert report.failed_pages[1] == "TooManyRequests: quota"
    assert report.scheduler_concurrency == 1
    assert json.loads((project / SCHEDULER_STATE_FILE).read_text(encoding="utf-8"))["active_limit"] == 1


def test_sliding_window_refills_after_fast_pages_without_waiting_for_a_slow_sibling(tmp_path: Path) -> None:
    # Break caught: an all-futures barrier leaves idle page slots while one sibling is slow.
    from workflow_v6_pipeline import PipelineConfiguration, PipelineDependencies, run_pages

    project = _project(tmp_path, pages=4)
    slow_started = threading.Event()
    page_four_started = threading.Event()
    release_slow = threading.Event()
    finished: list[object] = []

    def loop(workspace, **_kwargs):
        page = workspace["page"]
        if page == 3:
            slow_started.set()
            assert release_slow.wait(2)
        if page == 4:
            page_four_started.set()
        return {"accepted": page}

    thread = threading.Thread(target=lambda: finished.append(run_pages(
        project,
        [1, 2, 3, 4],
        dependencies=PipelineDependencies(
            open_workspace=lambda root, page: {"project": root, "page": page},
            evidence_recorder=lambda workspace: object(), candidate_loop=loop,
        ),
        configuration=PipelineConfiguration(
            page_workers=3, initial_page_concurrency=3, maximum_page_concurrency=3,
        ),
    )))
    thread.start()
    assert slow_started.wait(1)
    try:
        assert page_four_started.wait(1)
    finally:
        release_slow.set()
    thread.join(3)

    assert not thread.is_alive()
    assert finished[0].completed_pages == (1, 2, 3, 4)


def test_concurrent_429_cannot_apply_stale_recovery_or_launch_an_idle_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Break caught: a 429 between eligibility and recovery expands stale capacity.
    import workflow_v6_pipeline as pipeline

    project = _project(tmp_path, pages=4)
    first_throttle = threading.Event()
    recovery_checked = threading.Event()
    allow_second_429 = threading.Event()
    second_throttle = threading.Event()
    release_recovery = threading.Event()
    release_page_three = threading.Event()
    pending_page_started = threading.Event()
    page_two_provider_succeeded = threading.Event()
    page_three_provider_active = threading.Event()
    throttle_count = 0
    throttle_lock = threading.Lock()
    real_rate_limited = pipeline._ThrottleEpoch.rate_limited
    real_permits_recovery = pipeline._ThrottleEpoch.permits_recovery

    class TooManyRequests(Exception):
        status_code = 429

    def rate_limited(epoch, gate, error, provider_call=None):
        nonlocal throttle_count
        if provider_call is None:
            real_rate_limited(epoch, gate, error)
        else:
            real_rate_limited(epoch, gate, error, provider_call)
        with throttle_lock:
            throttle_count += 1
            (first_throttle if throttle_count == 1 else second_throttle).set()

    def permits_recovery(epoch, generations):
        allowed = real_permits_recovery(epoch, generations)
        if allowed:
            recovery_checked.set()
            assert release_recovery.wait(2)
        return allowed

    monkeypatch.setattr(pipeline._ThrottleEpoch, "rate_limited", rate_limited)
    monkeypatch.setattr(pipeline._ThrottleEpoch, "permits_recovery", permits_recovery)

    def provider(request, _timeout):
        page = request[0]
        if page == "1":
            raise TooManyRequests("first")
        if page == "3":
            page_three_provider_active.set()
            assert allow_second_429.wait(2)
            raise TooManyRequests("second")
        page_two_provider_succeeded.set()

    def loop(workspace, *, provider_runner, **_kwargs):
        page = workspace["page"]
        if page == 1:
            provider_runner(["1"], 1)
        if page == 2:
            assert first_throttle.wait(2)
            provider_runner(["2"], 1)
            assert page_three_provider_active.wait(2)
            return {"accepted": 2}
        if page == 3:
            assert first_throttle.wait(2)
            assert page_two_provider_succeeded.wait(2)
            try:
                provider_runner(["3"], 1)
            except TooManyRequests:
                assert release_page_three.wait(2)
                raise
        pending_page_started.set()
        return {"accepted": 4}

    configuration = pipeline.PipelineConfiguration(
        page_workers=3, initial_page_concurrency=3, maximum_page_concurrency=3,
        image2_concurrency=3, provider_profile="speed",
    )
    report_holder: list[object] = []
    thread = threading.Thread(target=lambda: report_holder.append(pipeline.run_pages(
        project,
        [1, 2, 3, 4],
        dependencies=pipeline.PipelineDependencies(
            open_workspace=lambda root, page: {"project": root, "page": page},
            evidence_recorder=lambda workspace: object(), candidate_loop=loop,
            provider_runner=provider,
        ),
        configuration=configuration,
    )))
    thread.start()
    assert recovery_checked.wait(2)
    allow_second_429.set()
    assert not second_throttle.wait(0.2)
    release_recovery.set()
    assert second_throttle.wait(2)
    try:
        assert not pending_page_started.wait(0.3)
    finally:
        release_page_three.set()
    thread.join(4)

    assert not thread.is_alive()
    assert report_holder[0].scheduler_concurrency == 1


def test_429_arriving_after_recovery_check_does_not_launch_pending_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Break caught: an active provider's late 429 waits behind stale recovery,
    # which expands the scheduler/gate and admits another provider call.
    import workflow_v6_pipeline as pipeline

    project = _project(tmp_path, pages=4)
    first_throttle = threading.Event()
    page_two_provider_succeeded = threading.Event()
    page_three_provider_active = threading.Event()
    allow_second_429 = threading.Event()
    second_429_handler_entered = threading.Event()
    release_second_429 = threading.Event()
    pending_page_started = threading.Event()
    pending_provider_started = threading.Event()
    recovery_round_intercepted = False
    real_rate_limited = pipeline._ThrottleEpoch.rate_limited
    real_record_round = pipeline.AdaptiveScheduler.record_round

    class TooManyRequests(Exception):
        status_code = 429

    def rate_limited(epoch, gate, error, provider_call=None):
        if str(error) == "second":
            second_429_handler_entered.set()
            assert release_second_429.wait(2)
        if provider_call is None:
            result = real_rate_limited(epoch, gate, error)
        else:
            result = real_rate_limited(epoch, gate, error, provider_call)
        if str(error) == "first":
            first_throttle.set()
        return result

    def record_round(scheduler, outcome, *, allow_recovery=True):
        nonlocal recovery_round_intercepted
        if outcome.successes == 1 and not recovery_round_intercepted:
            recovery_round_intercepted = True
            assert page_three_provider_active.wait(2)
            # _ThrottleEpoch has completed its recovery decision before calling
            # AdaptiveScheduler.record_round. Deliver the 429 only now.
            allow_second_429.set()
            assert second_429_handler_entered.wait(2)
        return real_record_round(
            scheduler, outcome, allow_recovery=allow_recovery,
        )

    monkeypatch.setattr(pipeline._ThrottleEpoch, "rate_limited", rate_limited)
    monkeypatch.setattr(pipeline.AdaptiveScheduler, "record_round", record_round)

    def provider(request, _timeout):
        page = request[0]
        if page == "1":
            raise TooManyRequests("first")
        if page == "2":
            page_two_provider_succeeded.set()
            return None
        if page == "3":
            page_three_provider_active.set()
            assert allow_second_429.wait(2)
            raise TooManyRequests("second")
        pending_provider_started.set()

    def loop(workspace, *, provider_runner, **_kwargs):
        page = workspace["page"]
        if page == 1:
            provider_runner(["1"], 1)
        if page == 2:
            assert first_throttle.wait(2)
            provider_runner(["2"], 1)
            assert page_three_provider_active.wait(2)
            return {"accepted": 2}
        if page == 3:
            assert first_throttle.wait(2)
            assert page_two_provider_succeeded.wait(2)
            provider_runner(["3"], 1)
        pending_page_started.set()
        provider_runner(["4"], 1)
        return {"accepted": 4}

    report_holder: list[object] = []
    thread = threading.Thread(target=lambda: report_holder.append(pipeline.run_pages(
        project,
        [1, 2, 3, 4],
        dependencies=pipeline.PipelineDependencies(
            open_workspace=lambda root, page: {"project": root, "page": page},
            evidence_recorder=lambda workspace: object(), candidate_loop=loop,
            provider_runner=provider,
        ),
        configuration=pipeline.PipelineConfiguration(
            page_workers=3, initial_page_concurrency=3, maximum_page_concurrency=3,
            image2_concurrency=3, provider_profile="speed",
        ),
    )))
    thread.start()
    assert second_429_handler_entered.wait(2)
    try:
        assert not pending_page_started.wait(0.3)
        assert not pending_provider_started.wait(0.3)
    finally:
        release_second_429.set()
    thread.join(4)

    assert not thread.is_alive()
    assert report_holder[0].completed_pages == (2, 4)
    assert report_holder[0].failed_pages == {
        1: "TooManyRequests: first", 3: "TooManyRequests: second",
    }
    assert report_holder[0].scheduler_concurrency == 2


def test_stage_limits_bound_real_director_provider_and_review_calls(tmp_path: Path) -> None:
    # Break caught: a stage wrapper stops enforcing its configured semaphore.
    from workflow_v6_pipeline import PipelineConfiguration, PipelineDependencies, run_pages

    project = _project(tmp_path)
    active = {"director": 0, "provider": 0, "review": 0}
    peaks = {key: 0 for key in active}
    lock = threading.Lock()

    def stage(name):
        def invoke(*_args, **_kwargs):
            with lock:
                active[name] += 1
                peaks[name] = max(peaks[name], active[name])
            time.sleep(0.03)
            with lock:
                active[name] -= 1
            return {"name": name}
        return invoke

    def loop(workspace, *, director_invoke, provider_runner, reviewer_invoke, **_kwargs):
        director_invoke()
        provider_runner([], 1)
        reviewer_invoke()
        return {"accepted": workspace["page"]}

    dependencies = PipelineDependencies(
        open_workspace=lambda root, page: {"project": root, "page": page},
        evidence_recorder=lambda workspace: object(), candidate_loop=loop,
        director_invoke=stage("director"), provider_runner=stage("provider"), reviewer_invoke=stage("review"),
    )
    configuration = PipelineConfiguration(page_workers=2, initial_page_concurrency=2, maximum_page_concurrency=2, director_concurrency=1, image2_concurrency=1, review_concurrency=1)

    report = run_pages(project, [1, 2], dependencies=dependencies, configuration=configuration)

    assert peaks == {"director": 1, "provider": 1, "review": 1}
    assert report.stage_peaks == {"director": 1, "image2": 1, "review": 1, "reconstruction": 0, "assembly": 0}
