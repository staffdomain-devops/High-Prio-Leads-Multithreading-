import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import build_matrix as bm  # noqa: E402
from generate_route_b import scenario_instructions  # noqa: E402


def ctx(state=bm.MISSED, others=()):
    return {"state": state, "last_outcome": "No answer", "others": list(others)}


def other(name, state, outcome):
    return {"first_name": name, "state": state, "outcome": outcome}


def test_connected_references_follow_call_outcome():
    text = scenario_instructions(2, ctx(others=[
        other("Alice", bm.SPOKE_UNCLEAR, "Connected"),
        other("Cara", bm.SPOKE_UNCLEAR, "Connected Interested"),
        other("Dan", bm.SPOKE_UNCLEAR, "Connected Call Back later"),
        other("Eve", bm.MISSED, "Left voicemail"),
    ]), "City Beach")
    assert '"I just tried giving you a quick call. To be fair' in text
    assert "you had a quick chat with Alice" in text
    assert "you spoke with Cara, who was keen to hear more" in text
    assert "you spoke briefly with Dan, who asked you to call back another time" in text
    assert "you also tried calling Eve and didn't get through" in text
    assert "point you in the right direction" in text


def test_not_kdm_adds_sits_with_someone_else():
    text = scenario_instructions(2, ctx(others=[other("Alice", bm.NOT_KDM, "Connected Interested")]), "City Beach")
    assert ("you spoke with Alice, who was keen to hear more "
            "and Alice mentioned this probably sits with someone else") in text


def test_email_3_opener_and_no_references():
    text = scenario_instructions(3, ctx(), "City Beach")
    assert "barking up the wrong tree" in text
    assert "REFERENCE LINE: none" in text


@pytest.mark.parametrize("state", [bm.SPOKE_UNCLEAR, bm.KDM, bm.NOT_KDM])
def test_connected_contacts_never_get_route_b(state):
    with pytest.raises(ValueError):
        scenario_instructions(2, ctx(state), "City Beach")
