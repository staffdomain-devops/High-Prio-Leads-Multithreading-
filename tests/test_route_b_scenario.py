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
    ]), "City Beach", text=DEFAULT_TEXT)
    assert '"I just tried giving you a quick call. To be fair' in text
    assert "you had a quick chat with Alice" in text
    assert "you spoke with Cara, who was keen to hear more" in text
    assert "you spoke briefly with Dan, who asked you to call back another time" in text
    assert "you also tried calling Eve and didn't get through" in text
    assert "point you in the right direction" in text


def test_not_kdm_adds_sits_with_someone_else():
    text = scenario_instructions(2, ctx(others=[other("Alice", bm.NOT_KDM, "Connected Interested")]), "City Beach", text=DEFAULT_TEXT)
    assert ("you spoke with Alice, who was keen to hear more "
            "and Alice mentioned this probably sits with someone else") in text


def test_namesakes_carry_surnames_and_the_recipients_namesake_is_your_colleague():
    c = ctx(others=[{"first_name": "Michael", "name": "Michael Tan", "state": bm.MISSED, "outcome": "No answer"},
                    {"first_name": "Michael", "name": "Michael Brown", "state": bm.NOT_KDM, "outcome": "Connected"},
                    {"first_name": "Sarah", "name": "Sarah", "state": bm.MISSED, "outcome": "Busy"}])
    c["recipient_first_name"] = "Michael"
    text = scenario_instructions(2, c, "City Beach", text=DEFAULT_TEXT)
    assert "you also tried calling your colleague Michael Tan and didn't get through" in text
    assert ("you had a quick chat with your colleague Michael Brown "
            "and Michael Brown mentioned this probably sits with someone else") in text
    assert "you also tried calling Sarah and didn't get through" in text
    assert "name exactly as written" in text
    # a different recipient gets plain surnamed names, and old contexts without `name` still work
    c2 = ctx(others=c["others"][:2])
    c2["recipient_first_name"] = "Sarah"
    text = scenario_instructions(2, c2, "City Beach", text=JP_TEXT)
    assert "you tried Michael Tan too and didn't get through" in text
    assert "your colleague" not in text
    assert "you tried Eve too" in scenario_instructions(2, ctx(others=[other("Eve", bm.MISSED, "Busy")]), "X", text=JP_TEXT)


def test_email_3_opener_and_no_references():
    text = scenario_instructions(3, ctx(), "City Beach", text=DEFAULT_TEXT)
    assert "barking up the wrong tree" in text
    assert "REFERENCE LINE: none" in text


@pytest.mark.parametrize("state", [bm.SPOKE_UNCLEAR, bm.KDM, bm.NOT_KDM])
def test_connected_contacts_never_get_route_b(state):
    with pytest.raises(ValueError):
        scenario_instructions(2, ctx(state), "City Beach", text=DEFAULT_TEXT)


# --- JP prompt set wording: facts fixed, phrasing free ---------------------------

def test_jp_email_2_gives_facts_and_guidance_not_a_script():
    text = scenario_instructions(2, ctx(others=[
        other("Ben", bm.MISSED, "No answer"),
        other("Cara", bm.NOT_KDM, "Connected"),
    ]), "Coastal Threads", text=JP_TEXT)
    assert "just tried calling them and didn't catch them" in text
    assert "you tried Ben too and didn't get through" in text
    assert "you had a quick chat with Cara and Cara reckons it probably sits with someone else" in text
    assert "using only these facts and nothing more" in text
    assert "Do not name the company" in text
    assert "who you should be talking to" in text
    assert "Open with exactly" not in text and "End with exactly" not in text
    assert "To be fair" not in text


def test_jp_email_2_nobody_else_to_mention():
    text = scenario_instructions(2, ctx(), "Coastal Threads", text=JP_TEXT)
    assert "REFERENCE LINE: none" in text
    assert "still not sure they're the right person" in text


def test_jp_email_3_wording():
    text = scenario_instructions(3, ctx(others=[other("Cara", bm.MISSED, "No answer")]), "Coastal Threads", text=JP_TEXT)
    assert "tried them again and still had no luck" in text
    assert "you tried Cara too and didn't get through" in text
    assert "point you at them" in text
    assert "barking up the wrong tree" not in text
    assert "REFERENCE LINE: none" in scenario_instructions(3, ctx(), "X", text=JP_TEXT)


def test_default_text_is_the_module_constants(monkeypatch):
    monkeypatch.setenv("PROMPT_VERSION", "default")
    assert scenario_instructions(2, ctx(), "X") == scenario_instructions(2, ctx(), "X", text=DEFAULT_TEXT)
    assert "barking up the wrong tree" in scenario_instructions(3, ctx(), "X", text=DEFAULT_TEXT)
    assert "Open with exactly" in scenario_instructions(2, ctx(), "X", text=DEFAULT_TEXT)


def test_blank_prompt_version_means_jp_wording(monkeypatch):
    """What the HubSpot webhook triggers: no prompt_version at all."""
    monkeypatch.delenv("PROMPT_VERSION", raising=False)
    assert scenario_instructions(2, ctx(), "X") == scenario_instructions(2, ctx(), "X", text=JP_TEXT)
    monkeypatch.setenv("PROMPT_VERSION", "")
    assert scenario_instructions(3, ctx(), "X") == scenario_instructions(3, ctx(), "X", text=JP_TEXT)
