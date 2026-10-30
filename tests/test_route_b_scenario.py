import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import build_matrix as bm  # noqa: E402
from generate_route_b import DEFAULT_TEXT, JP_TEXT, scenario_instructions  # noqa: E402


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


# --- JP prompt set wording -----------------------------------------------------

def test_jp_email_2_wording():
    text = scenario_instructions(2, ctx(others=[
        other("Ben", bm.MISSED, "No answer"),
        other("Cara", bm.NOT_KDM, "Connected"),
    ]), "Coastal Threads", text=JP_TEXT)
    assert '"Just tried giving you a quick call and didn\'t catch you."' in text
    assert "you tried Ben too and didn't get through" in text
    assert "you had a quick chat with Cara and Cara reckons it probably sits with someone else" in text
    assert "still not sure who the right person is" in text
    assert "Do not name the company" in text
    assert '"Are you the right person to chat to about this? If not, who should I be talking to?"' in text
    assert "To be fair" not in text


def test_jp_email_2_nobody_else_to_mention():
    text = scenario_instructions(2, ctx(), "Coastal Threads", text=JP_TEXT)
    assert "REFERENCE LINE: none" in text
    assert "not sure if they're the right person" in text


def test_jp_email_3_still_uses_default_opener():
    assert "barking up the wrong tree" in scenario_instructions(3, ctx(), "X", text=JP_TEXT)


def test_default_text_is_the_module_constants():
    assert scenario_instructions(2, ctx(), "X") == scenario_instructions(2, ctx(), "X", text=DEFAULT_TEXT)
