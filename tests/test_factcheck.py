import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import build_matrix as bm  # noqa: E402
import factcheck  # noqa: E402

POOL = ["Alice", "Ben", "Cara"]
LM = "Liqui Moly run a multi-million dollar operation with a two-person local team: https://x"


def check(body, allowed, recipient="Alice", calls_allowed=True):
    return factcheck.check_body(body, recipient, POOL, allowed, calls_allowed)


# --- route B: facts about others ------------------------------------------------

def test_truthful_email_2_passes():
    body = ("Just tried you and didn't catch you. Same with Ben and Cara, so I'm still not 100% sure who the right person is.<br><br>"
            "Quick FYI, I'm not a recruiter. " + LM)
    assert check(body, {"Ben": bm.MISSED, "Cara": bm.MISSED}) == []


def test_claiming_a_chat_with_a_missed_contact_fails():
    body = "Tried you again, no luck. Had a quick chat with Cara, who reckons this sits with someone else."
    problems = check(body, {"Cara": bm.MISSED})
    assert len(problems) == 1 and "says we reached Cara, but Cara was missed" in problems[0]


def test_saying_we_missed_someone_who_connected_fails():
    body = "Tried you again, no luck. Couldn't get Ben either."
    problems = check(body, {"Ben": bm.NOT_KDM})
    assert len(problems) == 1 and "says we missed Ben, but Ben connected" in problems[0]


def test_connected_contact_described_truthfully_passes():
    body = "Tried you again, no luck. Had a quick chat with Ben, who reckons this sits with someone else, and couldn't get Cara either."
    assert check(body, {"Ben": bm.NOT_KDM, "Cara": bm.MISSED}) == []


def test_unclear_kdm_cannot_be_said_to_have_pointed_elsewhere():
    body = "Had a quick chat with Ben, who reckons this sits with someone else."
    problems = check(body, {"Ben": bm.SPOKE_UNCLEAR})
    assert any("pointed us elsewhere" in p for p in problems)
    assert check("Had a quick chat with Ben.", {"Ben": bm.SPOKE_UNCLEAR}) == []


def test_mentioning_someone_not_in_the_allowed_list_fails():
    # Cara opted out (or had no call this round): she is not in `others`, so her name must not appear
    body = "Tried you and didn't catch you. Tried Ben and Cara too."
    problems = check(body, {"Ben": bm.MISSED})
    assert len(problems) == 1 and "mentions Cara, who must not be referenced" in problems[0]


def test_recipients_own_name_and_short_names_are_ignored():
    assert check("Alice, this one is for you. Tried Ben too.", {"Ben": bm.MISSED}) == []
    assert factcheck.check_body("I will call Al later.", "Alice", ["Al", "Ben"], {}, True) == []


def test_names_match_whole_words_only():
    # "Cara" must not match "Carrera"
    assert check("Carrera by Design run their drafting with our people.", {}) == []


# --- route A: no calls at all -----------------------------------------------------

def test_email_1_may_name_others_but_not_claim_calls():
    body = "Not sure if it's you, Ben or Cara I should be asking, but I saw the role you've got up. How are you going finding someone?"
    assert check(body, {"Ben": bm.PENDING, "Cara": bm.PENDING}, calls_allowed=False) == []
    body = "I tried calling you earlier about the role."
    problems = check(body, {"Ben": bm.PENDING, "Cara": bm.PENDING}, calls_allowed=False)
    assert len(problems) == 1 and "must not reference calls" in problems[0]


def test_emails_4_and_5_may_not_name_anyone():
    body = "I know this isn't for everyone. Ben will tell you the same."
    problems = check(body, {}, calls_allowed=False)
    assert len(problems) == 1 and "mentions Ben" in problems[0]


def test_two_people_in_one_sentence_are_judged_separately():
    body = "Tried you again, no luck. Had a quick chat with Ben, who reckons this sits with someone else, and tried Cara too with no luck."
    assert check(body, {"Ben": bm.NOT_KDM, "Cara": bm.MISSED}) == []
    # ...and the reverse is still caught
    body = "Couldn't get Ben, but had a quick chat with Cara."
    problems = check(body, {"Ben": bm.NOT_KDM, "Cara": bm.MISSED})
    assert len(problems) == 2


def test_email_1_may_promise_a_call_but_not_claim_one():
    body = "Keen for a quick chat this week. Otherwise I'll try my luck on the phone and give you a quick call."
    assert check(body, {"Ben": bm.PENDING}, calls_allowed=False) == []
    body = "I left you a voicemail yesterday about the role."
    assert len(check(body, {"Ben": bm.PENDING}, calls_allowed=False)) == 1


# --- plan wiring -----------------------------------------------------------------

def plan_for(route, round_no, others, recipient="101"):
    return {
        "route": route, "round": round_no,
        "contacts": {"101": {"firstname": "Alice"}, "102": {"firstname": "Ben"}, "103": {"firstname": "Cara"}},
        "matrix_after": {"contacts": {"101": {"first_name": "Alice"}, "102": {"first_name": "Ben"}, "103": {"first_name": "Cara"}}},
        "contexts": {recipient: {"state": bm.MISSED, "others": others}},
    }


def test_check_emails_uses_route_b_context_and_raises_with_every_problem():
    plan = plan_for("B", 2, [{"first_name": "Ben", "state": bm.MISSED, "outcome": "No answer"}])
    emails = {3: {"subject": "Second try", "body": "Had a quick chat with Ben, and Cara said hi."}}
    with pytest.raises(factcheck.FactCheckError) as exc:
        factcheck.check_emails(plan, "101", emails)
    text = str(exc.value)
    assert "email_3: says we reached Ben" in text and "email_3: mentions Cara" in text


def test_check_emails_round_0_allows_names_only():
    plan = plan_for("A", 0, ["Ben", "Cara"])
    assert factcheck.check_emails(plan, "101", {1: {"subject": "Role", "body": "Not sure if it's you, Ben or Cara I should be asking."}})
    with pytest.raises(factcheck.FactCheckError):
        factcheck.check_emails(plan, "101", {1: {"subject": "Role", "body": "Just tried calling you, Ben and Cara."}})


def test_check_emails_round_3_allows_nobody():
    plan = plan_for("A", 3, ["Cara"])
    assert factcheck.check_emails(plan, "101", {4: {"subject": "x", "body": "This is what we do."}, 5: {"subject": "x", "body": "Leaving it with you."}})
    with pytest.raises(factcheck.FactCheckError, match="mentions Cara"):
        factcheck.check_emails(plan, "101", {4: {"subject": "x", "body": "Cara might know."}, 5: {"subject": "x", "body": "ok"}})
