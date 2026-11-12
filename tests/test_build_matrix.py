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


def labelled_call(round_no, label, kdm_status=None, offset=10):
    """A call whose outcome is only known by its label (plan_round attaches it from the live list)."""
    start, _ = WINDOWS[round_no]
    return {"id": f"{round_no}lbl{offset}", "timestamp": start + offset,
            "disposition": "guid-not-in-the-map", "outcome": label, "kdm_status": kdm_status}


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
    assert ctx["others"] == [{"first_name": "Alice", "name": "Alice", "state": bm.MISSED, "outcome": "No answer"},
                              {"first_name": "Cara", "name": "Cara", "state": bm.MISSED, "outcome": "No answer"}]
    assert ctx["recipient_first_name"] == "Ben"


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
    assert bm.route_b_context(m, B)["others"] == [{"first_name": "Cara", "name": "Cara", "state": bm.MISSED, "outcome": "No answer"}]


# --- connected but not KDM / unclear ---------------------------------------------

def test_not_kdm_leaves_pool_but_is_referenced():
    m = run_round(bm.new_matrix(CONTACTS), 1, {A: call(1, CONNECTED, "No"), B: call(1, NO_ANSWER), C: call(1, NO_ANSWER)})
    assert states(m)[A] == bm.NOT_KDM
    assert bm.recipients(m) == [B, C]
    alice = {"first_name": "Alice", "name": "Alice", "state": bm.NOT_KDM, "outcome": "Connected"}
    assert alice in bm.route_b_context(m, C)["others"]
    # still referenced in the next round
    m2 = run_round(m, 2, {B: call(2, NO_ANSWER), C: call(2, NO_ANSWER)})
    assert alice in bm.route_b_context(m2, B)["others"]


def test_spoke_unclear_stops_but_is_referenced():
    m = run_round(bm.new_matrix(CONTACTS), 1, {A: call(1, CALL_BACK, "Unclear"), B: call(1, NO_ANSWER), C: call(1, CONNECTED)})
    assert states(m) == {A: bm.SPOKE_UNCLEAR, B: bm.MISSED, C: bm.SPOKE_UNCLEAR}
    assert m["status"] == bm.STATUS_ACTIVE
    assert bm.recipients(m) == [B]
    assert bm.route_b_context(m, B)["others"] == [
        {"first_name": "Alice", "name": "Alice", "state": bm.SPOKE_UNCLEAR, "outcome": "Connected Call Back later"},
        {"first_name": "Cara", "name": "Cara", "state": bm.SPOKE_UNCLEAR, "outcome": "Connected"}]
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


# --- replies ------------------------------------------------------------------

def test_reply_stops_everyone_and_hands_over_to_the_rep():
    before = bm.new_matrix(CONTACTS)
    m = bm.advance(before, 1, {cid: [call(1, NO_ANSWER)] for cid in (A, B, C)}, WINDOWS[1], replied={B})
    assert states(m) == {A: bm.FROZEN, B: bm.REPLIED, C: bm.FROZEN}
    assert m["status"] == bm.STATUS_REPLIED and m["replied_contact_ids"] == [B]
    assert m["contacts"][B]["history"][-1]["outcome"] == "Replied by email"
    # Nothing else goes out: no recipients, no next task, every slot cleared, every lock released
    assert bm.recipients(m) == [] and bm.email_recipients(m) == []
    assert bm.next_task_text(m, "City Beach", "Assistant Buyer", CONTACTS) == ("", "")
    assert bm.slots_to_clear(before, m) == {A: [2, 3, 4, 5], B: [2, 3, 4, 5], C: [2, 3, 4, 5]}
    assert set(bm.release_contacts(before, m)) == {A, B, C}
    with pytest.raises(ValueError):
        bm.advance(m, 2, {}, WINDOWS[2])


def test_reply_beats_a_missed_call_and_an_unsubscribe():
    m = bm.new_matrix(CONTACTS)
    m = bm.advance(m, 1, {A: [call(1, VOICEMAIL)]}, WINDOWS[1], opted_out={A}, replied={A})
    assert m["contacts"][A]["state"] == bm.REPLIED and m["status"] == bm.STATUS_REPLIED


def test_reply_from_a_contact_who_connected_as_non_kdm_still_stops_everyone():
    m = bm.new_matrix(CONTACTS)
    m = bm.advance(m, 1, {A: [call(1, CONNECTED, "No")], B: [call(1, NO_ANSWER)]}, WINDOWS[1], replied={A})
    assert states(m) == {A: bm.REPLIED, B: bm.FROZEN, C: bm.FROZEN}
    assert m["status"] == bm.STATUS_REPLIED


def test_kdm_reached_in_the_same_round_outranks_a_reply():
    m = bm.new_matrix(CONTACTS)
    m = bm.advance(m, 1, {A: [call(1, CONNECTED, "Yes")], B: [call(1, NO_ANSWER)]}, WINDOWS[1], replied={B})
    assert m["status"] == bm.STATUS_KDM_FOUND and m["kdm_contact_id"] == A
    assert states(m) == {A: bm.KDM, B: bm.REPLIED, C: bm.FROZEN}
    assert bm.recipients(m) == []


def test_reply_from_someone_already_out_of_the_pool_changes_nothing():
    m = bm.new_matrix(CONTACTS)
    m = run_round(m, 1, {A: call(1, CONNECTED, "No"), B: call(1, NO_ANSWER), C: call(1, NO_ANSWER)})
    m = bm.advance(m, 2, {B: [call(2, NO_ANSWER)], C: [call(2, NO_ANSWER)]}, WINDOWS[2], replied={A})
    assert m["status"] == bm.STATUS_ACTIVE and bm.recipients(m) == [B, C]


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
    assert bm.route_a_context(m, A) == {"state": bm.MISSED, "others": ["Ben", "Cara"], "recipient_first_name": "Alice"}
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


# --- email 1 opener: who else is in the pool -----------------------------------

def test_route_a_context_names_other_live_contacts_in_call_order():
    m = bm.new_matrix(CONTACTS)
    assert bm.route_a_context(m, A)["others"] == ["Ben", "Cara"]
    assert bm.route_a_context(m, B)["others"] == ["Alice", "Cara"]
    assert bm.route_a_context(m, C)["others"] == ["Alice", "Ben"]


def test_route_a_context_drops_contacts_who_connected():
    m = run_round(bm.new_matrix(CONTACTS), 1, {A: call(1, NO_ANSWER), B: call(1, CONNECTED, "No"), C: call(1, NO_ANSWER)})
    assert bm.route_a_context(m, A)["others"] == ["Cara"]


def test_who_clause():
    assert bm.who_clause([]) == "you"
    assert bm.who_clause(["Ben"]) == "you or Ben"
    assert bm.who_clause(["Ben", "Cara"]) == "you, Ben or Cara"
    assert bm.who_clause(["Ben", "Cara", "Dan"]) == "you, Ben, Cara or someone else there"
    assert bm.who_clause(["Ben", "Cara", "Dan", "Eve"]) == "you, Ben, Cara or someone else there"
    assert bm.who_clause(["", "Ben"]) == "you or Ben"
    # route B contexts carry dicts
    assert bm.who_clause([{"first_name": "Ben", "state": bm.MISSED, "outcome": "No answer"}]) == "you or Ben"
    assert bm.who_clause([{"first_name": "Ben", "name": "Ben Baker", "state": bm.MISSED, "outcome": "No answer"}]) == "you or Ben Baker"


# --- two people in the pool share a first name -------------------------------------

TWINS = {
    "201": {"firstname": "Michael", "lastname": "Brown", "contact_icp_rating": "Prime (MM)", "kdm_rank": "1"},
    "202": {"firstname": "Sarah", "lastname": "Lee", "contact_icp_rating": "Prime (MM)", "kdm_rank": "2"},
    "203": {"firstname": "michael", "lastname": "Tan", "contact_icp_rating": "Core (MM)", "kdm_rank": "1"},
}


def test_shared_first_names_get_their_surname_everywhere():
    m = bm.new_matrix(TWINS)
    assert m["order"] == ["201", "202", "203"]
    assert bm.display_names(m) == {"201": "Michael Brown", "202": "Sarah", "203": "michael Tan"}
    assert bm.route_a_context(m, "202")["others"] == ["Michael Brown", "michael Tan"]
    assert bm.route_a_context(m, "201")["others"] == ["Sarah", "michael Tan"]


def test_who_clause_calls_a_namesake_your_colleague():
    m = bm.new_matrix(TWINS)
    assert bm.who_clause(bm.route_a_context(m, "201")["others"], "Michael") == "you, Sarah or your colleague michael Tan"
    assert bm.who_clause(bm.route_a_context(m, "202")["others"], "Sarah") == "you, Michael Brown or michael Tan"
    assert bm.colleague("Michael Tan", "MICHAEL ") == "your colleague Michael Tan"
    assert bm.colleague("Michaela Tan", "Michael") == "Michaela Tan"
    assert bm.colleague("Sarah", None) == "Sarah"


def test_shared_first_names_in_route_b_references():
    m = run_round(bm.new_matrix(TWINS), 1,
                  {"201": call(1, NO_ANSWER), "202": call(1, NO_ANSWER), "203": call(1, CONNECTED, "No")})
    ctx = bm.route_b_context(m, "201")
    assert [(o["name"], o["state"]) for o in ctx["others"]] == [("Sarah", bm.MISSED), ("michael Tan", bm.NOT_KDM)]
    assert ctx["recipient_first_name"] == "Michael"
    assert [o["name"] for o in bm.route_b_context(m, "202")["others"]] == ["Michael Brown", "michael Tan"]


def test_namesake_without_a_surname_keeps_the_first_name_and_nameless_contacts_are_left_out():
    m = bm.new_matrix({"1": {"firstname": "Michael", "lastname": ""}, "2": {"firstname": "Michael", "lastname": "Tan"},
                       "3": {"firstname": "", "lastname": "Nameless"}})
    assert bm.display_names(m) == {"1": "Michael", "2": "Michael Tan", "3": ""}
    assert bm.route_a_context(m, "2")["others"] == ["Michael"]
    assert bm.route_a_context(m, "1")["others"] == ["Michael Tan"]


# --- outcomes recognised by label (KDM folded into the call outcome) --------------

@pytest.mark.parametrize("label", ["Connected - KDM", "connected kdm", " Connected \u2013 KDM ", "Connected_KDM"])
def test_connected_kdm_outcome_stops_everyone_without_the_kdm_property(label):
    m = run_round(bm.new_matrix(CONTACTS), 1,
                  {A: call(1, NO_ANSWER), B: labelled_call(1, label), C: call(1, NO_ANSWER)})
    assert states(m) == {A: bm.FROZEN, B: bm.KDM, C: bm.FROZEN}
    assert m["status"] == bm.STATUS_KDM_FOUND and m["kdm_contact_id"] == B
    assert bm.recipients(m) == []
    assert m["contacts"][B]["history"][-1]["connected"] is True


@pytest.mark.parametrize("label", ["Connected - Not KDM", "connected not kdm", "Connected-Not-KDM"])
def test_connected_not_kdm_outcome_stops_that_contact_and_is_referenced(label):
    m = run_round(bm.new_matrix(CONTACTS), 1,
                  {A: labelled_call(1, label), B: call(1, NO_ANSWER), C: call(1, NO_ANSWER)})
    assert states(m) == {A: bm.NOT_KDM, B: bm.MISSED, C: bm.MISSED}
    assert bm.recipients(m) == [B, C]
    assert {"first_name": "Alice", "name": "Alice", "state": bm.NOT_KDM, "outcome": label} in bm.route_b_context(m, B)["others"]


def test_not_interested_by_label_drops_only_that_contact():
    m = run_round(bm.new_matrix(CONTACTS), 1,
                  {A: labelled_call(1, "Connected Not Interested "), B: call(1, NO_ANSWER), C: call(1, NO_ANSWER)})
    assert states(m) == {A: bm.OPTED_OUT, B: bm.MISSED, C: bm.MISSED}
    assert m["status"] == bm.STATUS_ACTIVE
    assert bm.recipients(m) == [B, C]
    assert all(o["first_name"] != "Alice" for o in bm.route_b_context(m, B)["others"])


def test_live_label_wins_over_the_built_in_map_and_unknown_labels_are_missed():
    # A known GUID whose live label says voicemail is a missed call; an unknown outcome is missed too
    renamed = dict(call(1, CONNECTED, "Yes"), outcome="Left voicemail")
    m = run_round(bm.new_matrix(CONTACTS), 1,
                  {A: renamed, B: labelled_call(1, "Spoke to reception"), C: call(1, NO_ANSWER)})
    assert states(m) == {A: bm.MISSED, B: bm.MISSED, C: bm.MISSED}
    assert m["contacts"][B]["history"][-1]["outcome"] == "Spoke to reception"
    assert m["contacts"][B]["history"][-1]["connected"] is False


def test_task_notes_tell_the_rep_which_outcome_to_pick():
    _, notes = bm.next_task_text(bm.new_matrix(CONTACTS), "City Beach", "Assistant Buyer", CONTACTS)
    assert "'Connected - KDM'" in notes and "'Connected - Not KDM'" in notes and "'Connected Not Interested'" in notes
