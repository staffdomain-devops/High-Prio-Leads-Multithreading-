import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import build_matrix as bm  # noqa: E402

NO_ANSWER = "73a0d17f-1163-4015-bdd5-ec830791da20"
VOICEMAIL = "b2cf5968-551e-4856-9783-52b3da59a7d0"
CONNECTED = "f240bbac-87c9-4f6e-bf70-924b57d47db7"
CALL_BACK = "d4d8824d-3110-441b-9db1-fde9d44cb784"
NOT_INTERESTED = "a44f5d58-3e2f-44ce-afbf-adf524cb190e"

WINDOWS = {1: (1000, 1999), 2: (2000, 2999), 3: (3000, 3999)}

CONTACTS = {
    "101": {"firstname": "Alice", "lastname": "Archer", "contact_icp_rating": "Prime (MM)", "kdm_rank": "1",
            "jobtitle": "CIO", "phone": "+61 400 000 001"},
    "102": {"firstname": "Ben", "lastname": "Baker", "contact_icp_rating": "Prime (MM)", "kdm_rank": "2"},
    "103": {"firstname": "Cara", "lastname": "Cole", "contact_icp_rating": "Core (MM)", "kdm_rank": "1"},
}
A, B, C = "101", "102", "103"


def call(round_no, disposition, kdm_status=None, offset=10):
    start, _ = WINDOWS[round_no]
    return {"id": f"{round_no}{disposition[:4]}{offset}", "timestamp": start + offset,
            "disposition": disposition, "kdm_status": kdm_status}


def run_round(matrix, round_no, calls):
    return bm.advance(matrix, round_no, {cid: [c] for cid, c in calls.items()}, WINDOWS[round_no])


def states(matrix):
    return {cid: matrix["contacts"][cid]["state"] for cid in matrix["order"]}


# --- ordering -----------------------------------------------------------------

def test_call_order_icp_then_kdm_rank_then_id():
    contacts = {
        "5": {"contact_icp_rating": "Core (SMB)", "kdm_rank": "1"},
        "4": {"contact_icp_rating": "Prime (SMB)", "kdm_rank": "3"},
        "3": {"contact_icp_rating": "Prime (SMB)", "kdm_rank": "2"},
        "2": {"contact_icp_rating": None, "kdm_rank": "1"},
        "1": {"contact_icp_rating": "Non-ICP", "kdm_rank": None},
        "6": {"contact_icp_rating": "Prime (MM)", "kdm_rank": "2"},
    }
    assert bm.call_order(contacts) == ["3", "6", "4", "5", "1", "2"]


def test_round_zero_everyone_pending_and_receives_email_1():
    m = bm.new_matrix(CONTACTS)
    assert m["order"] == [A, B, C]
    assert bm.recipients(m) == [A, B, C]


# --- scenario 1: nobody connects, everyone references everyone else ------------

def test_all_missed_each_email_references_the_other_two():
    m = run_round(bm.new_matrix(CONTACTS), 1, {A: call(1, NO_ANSWER), B: call(1, VOICEMAIL), C: call(1, NO_ANSWER)})
    assert states(m) == {A: bm.MISSED, B: bm.MISSED, C: bm.MISSED}
    assert bm.recipients(m) == [A, B, C]
    ctx = bm.route_b_context(m, B)
    assert ctx["state"] == bm.MISSED
    assert ctx["others"] == [{"first_name": "Alice", "state": bm.MISSED, "outcome": "No answer"},
                              {"first_name": "Cara", "state": bm.MISSED, "outcome": "No answer"}]


# --- scenario 2: KDM found mid-round -----------------------------------------

def test_kdm_found_stops_everyone_including_the_kdm():
    before = bm.new_matrix(CONTACTS)
    m = run_round(before, 1, {A: call(1, NO_ANSWER), B: call(1, CONNECTED, "Yes")})
    assert m["status"] == bm.STATUS_KDM_FOUND
    assert m["kdm_contact_id"] == B
    assert states(m) == {A: bm.FROZEN, B: bm.KDM, C: bm.FROZEN}
    assert bm.recipients(m) == []
    assert bm.slots_to_clear(before, m) == {A: [2, 3, 4, 5], B: [2, 3, 4, 5], C: [2, 3, 4, 5]}
    assert bm.next_task_text(m, "City Beach", "Assistant Buyer", CONTACTS) == ("", "")
    with pytest.raises(ValueError):
        run_round(m, 2, {})


def test_first_kdm_in_call_order_wins():
    m = run_round(bm.new_matrix(CONTACTS), 1, {A: call(1, CONNECTED, "Yes"), B: call(1, CONNECTED, "Yes")})
    assert m["kdm_contact_id"] == A
    assert states(m)[B] == bm.FROZEN


# --- not interested ---------------------------------------------------------

def test_kdm_not_interested_drops_whole_sequence():
    before = bm.new_matrix(CONTACTS)
    m = run_round(before, 1, {A: call(1, NO_ANSWER), B: call(1, NOT_INTERESTED, "Yes"), C: call(1, NO_ANSWER)})
    assert m["status"] == bm.STATUS_CLOSED_LOST
    assert bm.recipients(m) == []
    assert bm.slots_to_clear(before, m) == {A: [2, 3, 4, 5], B: [2, 3, 4, 5], C: [2, 3, 4, 5]}
    assert bm.next_task_text(m, "City Beach", "Assistant Buyer", CONTACTS) == ("", "")
    with pytest.raises(ValueError):
        run_round(m, 2, {})


@pytest.mark.parametrize("kdm_status", [None, "Unclear", "No"])
def test_not_interested_non_kdm_opts_out_and_is_never_referenced(kdm_status):
    m = run_round(bm.new_matrix(CONTACTS), 1,
                  {A: call(1, NOT_INTERESTED, kdm_status), B: call(1, NO_ANSWER), C: call(1, NO_ANSWER)})
    assert m["status"] == bm.STATUS_ACTIVE
    assert states(m)[A] == bm.OPTED_OUT
    assert bm.recipients(m) == [B, C]
    assert bm.route_b_context(m, B)["others"] == [{"first_name": "Cara", "state": bm.MISSED, "outcome": "No answer"}]


# --- connected but not KDM / unclear ---------------------------------------------

def test_not_kdm_leaves_pool_but_is_referenced():
    m = run_round(bm.new_matrix(CONTACTS), 1, {A: call(1, CONNECTED, "No"), B: call(1, NO_ANSWER), C: call(1, NO_ANSWER)})
    assert states(m)[A] == bm.NOT_KDM
    assert bm.recipients(m) == [B, C]
    assert {"first_name": "Alice", "state": bm.NOT_KDM, "outcome": "Connected"} in bm.route_b_context(m, C)["others"]
    # still referenced in the next round
    m2 = run_round(m, 2, {B: call(2, NO_ANSWER), C: call(2, NO_ANSWER)})
    assert {"first_name": "Alice", "state": bm.NOT_KDM, "outcome": "Connected"} in bm.route_b_context(m2, B)["others"]


def test_spoke_unclear_stops_but_is_referenced():
    m = run_round(bm.new_matrix(CONTACTS), 1, {A: call(1, CALL_BACK, "Unclear"), B: call(1, NO_ANSWER), C: call(1, CONNECTED)})
    assert states(m) == {A: bm.SPOKE_UNCLEAR, B: bm.MISSED, C: bm.SPOKE_UNCLEAR}
    assert m["status"] == bm.STATUS_ACTIVE
    assert bm.recipients(m) == [B]
    assert bm.route_b_context(m, B)["others"] == [
        {"first_name": "Alice", "state": bm.SPOKE_UNCLEAR, "outcome": "Connected Call Back later"},
        {"first_name": "Cara", "state": bm.SPOKE_UNCLEAR, "outcome": "Connected"}]
    assert bm.slots_to_clear(bm.new_matrix(CONTACTS), m) == {A: [2, 3, 4, 5], C: [2, 3, 4, 5]}


@pytest.mark.parametrize("disposition", [CONNECTED, CALL_BACK, "e94640e4-419c-4ab9-9b49-fc383da05778"])
def test_any_connect_stops_that_contact(disposition):
    m = run_round(bm.new_matrix(CONTACTS), 1, {A: call(1, disposition), B: call(1, NO_ANSWER), C: call(1, NO_ANSWER)})
    assert A not in bm.recipients(m)
    assert bm.recipients(m) == [B, C]


def test_everyone_connects_without_kdm_leaves_nobody():
    m = run_round(bm.new_matrix(CONTACTS), 1,
                  {A: call(1, CONNECTED, "No"), B: call(1, CALL_BACK), C: call(1, NOT_INTERESTED)})
    assert m["status"] == bm.STATUS_ACTIVE
    assert bm.recipients(m) == []
    assert bm.next_task_text(m, "City Beach", "Assistant Buyer", CONTACTS) == ("", "")


def test_kdm_yes_on_non_connected_call_is_ignored():
    m = run_round(bm.new_matrix(CONTACTS), 1, {A: call(1, VOICEMAIL, "Yes")})
    assert states(m)[A] == bm.MISSED
    assert m["status"] == bm.STATUS_ACTIVE


def test_only_person_left_has_no_references():
    m = run_round(bm.new_matrix(CONTACTS), 1,
                  {A: call(1, NOT_INTERESTED), B: call(1, NOT_INTERESTED), C: call(1, NO_ANSWER)})
    assert bm.recipients(m) == [C]
    assert bm.route_b_context(m, C)["others"] == []


# --- call window ------------------------------------------------------------

def test_latest_call_in_window_wins_and_outside_calls_ignored():
    calls = {A: [
        {"id": "old", "timestamp": 500, "disposition": CONNECTED, "kdm_status": "Yes"},  # before the window
        {"id": "a1", "timestamp": 1100, "disposition": NO_ANSWER, "kdm_status": None},
        {"id": "a2", "timestamp": 1500, "disposition": CALL_BACK, "kdm_status": "Unclear"},
    ]}
    m = bm.advance(bm.new_matrix(CONTACTS), 1, calls, WINDOWS[1])
    assert states(m)[A] == bm.SPOKE_UNCLEAR
    assert states(m)[B] == bm.MISSED  # no call in window counts as MISSED
    assert m["contacts"][B]["history"][-1]["outcome"] == "No call"


def test_round_must_be_next_in_sequence():
    with pytest.raises(ValueError):
        bm.advance(bm.new_matrix(CONTACTS), 2, {}, WINDOWS[2])


# --- multi-round pool ---------------------------------------------------------

def test_three_rounds_all_missed_then_email_4_5_for_everyone():
    m = bm.new_matrix(CONTACTS)
    for r in (1, 2, 3):
        m = run_round(m, r, {A: call(r, NO_ANSWER), B: call(r, NO_ANSWER), C: call(r, NO_ANSWER)})
    assert m["round"] == 3
    assert bm.recipients(m) == [A, B, C]
    assert bm.route_a_context(m, A) == {"state": bm.MISSED}
    assert bm.next_task_text(m, "City Beach", "Assistant Buyer", CONTACTS) == ("", "")


def test_kdm_found_in_round_two_freezes_previous_missed():
    m = run_round(bm.new_matrix(CONTACTS), 1, {A: call(1, NO_ANSWER), B: call(1, NO_ANSWER), C: call(1, NO_ANSWER)})
    m2 = run_round(m, 2, {A: call(2, NO_ANSWER), B: call(2, NO_ANSWER), C: call(2, CONNECTED, "Yes")})
    assert states(m2) == {A: bm.FROZEN, B: bm.FROZEN, C: bm.KDM}
    assert bm.slots_to_clear(m, m2) == {A: [3, 4, 5], B: [3, 4, 5], C: [3, 4, 5]}


# --- task text ----------------------------------------------------------------

def test_task_text_pool_mode():
    m = bm.new_matrix(CONTACTS)
    title, notes = bm.next_task_text(m, "City Beach", "Assistant Buyer", CONTACTS)
    assert title == "Call 1 of 3 · City Beach · Assistant Buyer · Call in order: Alice A → Ben B → Cara C"
    assert notes.splitlines()[0] == "1. Alice Archer (CIO) · +61 400 000 001 · last outcome: not called yet"
    assert "unless a KDM is confirmed" in notes


def test_task_text_lists_only_contacts_still_to_reach():
    m = run_round(bm.new_matrix(CONTACTS), 1, {A: call(1, CONNECTED, "No"), B: call(1, NO_ANSWER), C: call(1, NO_ANSWER)})
    title, notes = bm.next_task_text(m, "City Beach", "Assistant Buyer", CONTACTS)
    assert title == "Call 2 of 3 · City Beach · Assistant Buyer · Call in order: Ben B → Cara C"
    assert notes.splitlines()[0] == "1. Ben Baker · no phone · last outcome: No answer"


def test_task_text_adds_buyer_frame_and_links():
    contacts = {**CONTACTS, A: {**CONTACTS[A], "linkedin_url": "https://linkedin.com/in/alice", "email": "a@cb.com"}}
    m = bm.new_matrix(CONTACTS)
    _, notes = bm.next_task_text(m, "City Beach", "Assistant Buyer", contacts, {A: "Cares about data security."})
    lines = notes.splitlines()
    assert lines[1] == "   Angle: Cares about data security."
    assert lines[2] == "   LinkedIn: https://linkedin.com/in/alice · Email: a@cb.com"
    assert lines[3].startswith("2. Ben Baker")  # no frame, no links: nothing extra
