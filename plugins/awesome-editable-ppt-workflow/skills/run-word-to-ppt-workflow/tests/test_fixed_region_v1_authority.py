"""Authority tests for the V4 workflow and unchanged V2 geometry."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from contract_version import CURRENT_CONTRACT, require_supported_contract  # noqa: E402
from fixed_region_contract import (  # noqa: E402
    BODY_BOX_CM,
    BODY_REMAINDER_CM,
    FOOTER_LINE,
    GEOMETRY_TOLERANCE_RATIO,
    LOGO_BOX_CM,
    PAGE_NUMBER_BOX_CM,
    SLIDE_SIZE_CM,
    TITLE_BOX_CM,
)


def test_word_ppt_workflow_v4_is_the_only_supported_workflow_contract() -> None:
    assert CURRENT_CONTRACT == "word-ppt-workflow-v4"
    assert require_supported_contract({"workflow_contract_version": CURRENT_CONTRACT}) == CURRENT_CONTRACT
    with pytest.raises(ValueError, match="word-ppt-workflow-v4"):
        require_supported_contract({"workflow_contract_version": "body-frame-v2"})


def test_single_authority_contains_all_locked_geometry() -> None:
    assert SLIDE_SIZE_CM == {"w": 25.4, "h": 14.288}
    assert BODY_BOX_CM == {"x": 0.81, "y": 2.3, "w": 23.78, "h": 11.18}
    assert BODY_REMAINDER_CM == {"left": 0.81, "top": 2.3, "right": 0.81, "bottom": 0.808}
    assert GEOMETRY_TOLERANCE_RATIO == 0.001
    assert BODY_BOX_CM["x"] + BODY_BOX_CM["w"] + BODY_REMAINDER_CM["right"] == pytest.approx(SLIDE_SIZE_CM["w"])
    assert BODY_BOX_CM["y"] + BODY_BOX_CM["h"] + BODY_REMAINDER_CM["bottom"] == pytest.approx(SLIDE_SIZE_CM["h"])
    assert TITLE_BOX_CM == {"x": 0.9, "y": 0.5, "w": 20.066, "h": 1.4288}
    assert LOGO_BOX_CM == {"x": 21.844, "y": 0.57152, "w": 2.667, "h": 1.0716}
    assert FOOTER_LINE == {"x": 0.9, "y": 13.64504, "w": 23.6, "h": 0.028576, "color": "#B8C0CC"}
    assert PAGE_NUMBER_BOX_CM == {"x": 23.368, "y": 13.687904, "w": 1.143, "h": 0.3572}
