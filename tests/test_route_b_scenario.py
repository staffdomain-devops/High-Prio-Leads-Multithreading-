import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import build_matrix as bm  # noqa: E402
from generate_route_b import scenario_instructions  # noqa: E402


def ctx(state, connected=False, outcome="No answer", others=()):
    return {"state": state, "connected_this_round": connected, "last_outcome": outcome, "others": list(others)}


def test_spoke_unclear_with_mixed_references():
    text = scenario_instructions(2, ctx(bm.SPOKE_UNCLEAR, True, "Connected Call Back later", [
        {"first_name": "Alice", "state": bm.NOT_KDM},
        {"first_name": "Cara", "state": bm.SPOKE_UNCLEAR},
    ]), "City Beach")
    assert '"Thanks for taking my call earlier."' in text
    assert "(outcome: Connected Call Back later)" in text
    assert "Alice mentioned this probably sits with someone else" in text
    assert "you had a quick chat with Cara" in text
    assert "point you in the right direction" in text


def test_kdm_missed_drops_right_person_question():
    text = scenario_instructions(3, ctx(bm.KDM, False), "City Beach")
    assert "didn't manage to catch you" in text
    assert "REFERENCE LINE: none" in text
    assert "Do NOT ask whether they are the right person" in text


def test_kdm_connected_email_2():
    text = scenario_instructions(2, ctx(bm.KDM, True, "Connected Interested"), "City Beach")
    assert '"Great speaking with you earlier."' in text
    assert "confirmed decision maker" in text
