from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def test_decimal_percentage_does_not_create_a_fragment_anchor() -> None:
    from build_page_contracts import AMOUNT_RE, NUMBER_RE

    text = "持有12.43%的股权，并最终收购100%。"
    assert NUMBER_RE.findall(text) == ["12.43%", "100%"]
    assert AMOUNT_RE.findall(text) == ["12.43%", "100%"]
