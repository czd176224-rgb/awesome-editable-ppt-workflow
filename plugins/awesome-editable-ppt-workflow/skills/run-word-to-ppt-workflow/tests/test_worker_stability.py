"""Offline checks for duplicate requests, owned child processes, and CLI outcomes."""
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import workflow_v6_reconstruction_worker as worker
import workflow_v6_cli as cli


def test_duplicate_page_requests_are_serialized_across_processes(tmp_path):
    log = tmp_path / "calls.txt"
    script = f'''
import sys,time
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, {str(SCRIPTS)!r})
import workflow_v6_reconstruction_worker as worker
def owned(*args, **kwargs):
    with Path({str(log)!r}).open('a') as f: f.write('start\\n')
    time.sleep(0.25)
    with Path({str(log)!r}).open('a') as f: f.write('end\\n')
worker._reconstruct_accepted_page_owned = owned
worker.reconstruct_accepted_page(SimpleNamespace(project_copy=Path({str(tmp_path)!r}), page_number=1), None)
'''
    processes = [subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    try:
        for process in processes:
            _, error = process.communicate(timeout=15)
            assert process.returncode == 0, error.decode(errors="replace")
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
    assert log.read_text().splitlines() == ["start", "end", "start", "end"]


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object cleanup")
@pytest.mark.parametrize("parent_exits", [False, True])
def test_timeout_kills_descendants_even_after_parent_exit(tmp_path, parent_exits):
    marker = tmp_path / "late-write.txt"
    child = f"import time,pathlib; time.sleep(1.2); pathlib.Path({str(marker)!r}).write_text('late')"
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); " + ("pass" if parent_exits else "time.sleep(10)")
    with pytest.raises(subprocess.TimeoutExpired):
        worker._run_worker_process([sys.executable, "-c", parent], timeout=0.4)
    time.sleep(1.3)
    assert not marker.exists(), "descendant wrote after worker timeout"


@pytest.mark.parametrize("command,status,expected", [
    ("run-pages", "failed", 1), ("recover-failed-pages", "failed", 1),
    ("run-pages", "validation_incomplete", 1), ("run-pages", "deferred", 0),
    ("assemble", "complete", 0), ("assemble", "validation_incomplete", 1),
    ("preflight", False, 1), ("preflight", True, 0),
])
def test_cli_exit_code_matches_result(monkeypatch, capsys, command, status, expected):
    import workflow_v6_preflight
    import workflow_v6_reconstruction
    monkeypatch.setattr(cli, "_require_valid_project", lambda _: None)
    report = SimpleNamespace(to_dict=lambda: {"failed_pages": {}, "assembly": {"status": status}})
    monkeypatch.setattr(cli, "run_pages", lambda *a, **k: report)
    monkeypatch.setattr(cli, "recover_failed_pages", lambda *a, **k: report)
    monkeypatch.setattr(workflow_v6_preflight, "preflight_project", lambda *a: {"passed": status})
    monkeypatch.setattr(workflow_v6_reconstruction, "assemble_v6_deck", lambda *a: {"status": status})
    argv = ["workflow_v6_cli.py", command, "--project", "."]
    if command in {"run-pages", "recover-failed-pages"}:
        argv += ["--pages", "1"]
    if command == "recover-failed-pages":
        argv += ["--recovery-round", "1"]
    monkeypatch.setattr(sys, "argv", argv)
    assert cli.main() == expected
    assert capsys.readouterr().out
