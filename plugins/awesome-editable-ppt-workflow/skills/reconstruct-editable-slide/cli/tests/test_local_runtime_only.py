from pathlib import Path

import pytest
import numpy as np

from editppt.runtime import main, runtime_env, text_hints


def test_cloud_ocr_entrypoints_are_removed_and_local_doctor_remains(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_AUTH_FILE", str(tmp_path / "missing-auth.json"))
    monkeypatch.setenv("PADDLE_OCR_TOKEN", "ignored-legacy-setting")
    status = runtime_env.collect_status()
    assert status["codex_oauth"]["ready"] is False
    assert "paddle" not in status
    assert not (Path(runtime_env.__file__).parent / "paddle_text_hints.py").exists()
    parser = main.build_parser()
    assert parser.parse_args(["doctor", "--json"]).func is main.cmd_doctor
    assert parser.parse_args(["setup"]).func is main.cmd_setup
    with pytest.raises(SystemExit) as error:
        parser.parse_args(["config", "--paddle-ocr-token", "unused"])
    assert error.value.code == 2
    assert text_hints.split_runs(np.array([0, 1, 1, 0, 0, 1, 0]), min_gap=2) == [(1, 3), (5, 6)]
