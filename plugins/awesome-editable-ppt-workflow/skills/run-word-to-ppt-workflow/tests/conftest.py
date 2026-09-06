from __future__ import annotations

import os

import pytest


def pytest_addoption(parser) -> None:
    parser.getgroup("editable-ppt-live").addoption(
        "--run-live-app-server",
        action="store_true",
        default=False,
        help="run tests that call the installed Codex App Server",
    )
    parser.getgroup("editable-ppt-live").addoption(
        "--run-huangshi-release",
        action="store_true",
        default=False,
        help="run the local Huangshi release-evidence tests",
    )


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "live_app_server: requires explicit live Codex App Server access and is skipped by default",
    )
    config.addinivalue_line(
        "markers",
        "huangshi_release: requires explicit local Huangshi release evidence and is skipped by default",
    )


def pytest_collection_modifyitems(config, items) -> None:
    live_enabled = (
        config.getoption("--run-live-app-server")
        and os.environ.get("EDITABLE_PPT_RUN_LIVE_APP_SERVER_TESTS") == "1"
    )
    huangshi_enabled = (
        config.getoption("--run-huangshi-release")
        and os.environ.get("EDITABLE_PPT_RUN_HUANGSHI_RELEASE_TESTS") == "1"
    )
    skip_live = pytest.mark.skip(
        reason=(
            "live App Server tests require both --run-live-app-server and "
            "EDITABLE_PPT_RUN_LIVE_APP_SERVER_TESTS=1"
        )
    )
    skip_huangshi = pytest.mark.skip(
        reason=(
            "Huangshi release tests require both --run-huangshi-release and "
            "EDITABLE_PPT_RUN_HUANGSHI_RELEASE_TESTS=1"
        )
    )
    for item in items:
        if not live_enabled and item.get_closest_marker("live_app_server") is not None:
            item.add_marker(skip_live)
        if not huangshi_enabled and item.get_closest_marker("huangshi_release") is not None:
            item.add_marker(skip_huangshi)
