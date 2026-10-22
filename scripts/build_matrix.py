"""
Deterministic call matrix — pure logic, no IO.

Each round: previous matrix + this round's calls -> new matrix.
From the new matrix we derive who gets an email, which slots to clear, the
per-recipient context for the email routes, and the next call task's text.

Nothing here talks to HubSpot or Claude, so every scenario is unit-tested
(tests/test_build_matrix.py).
"""

import copy

TOTAL_ROUNDS = 3

# Email slots written in each round (round 0 = before any calls)
ROUND_SLOTS = {0: [1], 1: [2], 2: [3], 3: [4, 5]}
ROUND_ROUTE = {0: "A", 1: "B", 2: "B", 3: "A"}

# hs_call_disposition option values in this portal -> label
DISPOSITION_LABELS = {
    "9d9162e7-6cf3-4944-bf63-4dff82258764": "Busy",
    "e94640e4-419c-4ab9-9b49-fc383da05778": "Connected Interested",
    "a44f5d58-3e2f-44ce-afbf-adf524cb190e": "Connected Not Interested",
    "d4d8824d-3110-441b-9db1-fde9d44cb784": "Connected Call Back later",
    "a4c4c377-d246-4b32-a13b-75a56a4cd0ff": "Left live message",
    "b2cf5968-551e-4856-9783-52b3da59a7d0": "Left voicemail",
    "73a0d17f-1163-4015-bdd5-ec830791da20": "No answer",
    "17b47fee-58de-441e-a44c-c6300d46f273": "Wrong number",
    "f240bbac-87c9-4f6e-bf70-924b57d47db7": "Connected",
    "802ebb52-912d-437b-9a68-244f9d347918": "Gate Keeper",
}
CONNECTED_NOT_INTERESTED = "a44f5d58-3e2f-44ce-afbf-adf524cb190e"
CONNECTED = {
    "f240bbac-87c9-4f6e-bf70-924b57d47db7",  # Connected
    "e94640e4-419c-4ab9-9b49-fc383da05778",  # Connected Interested
    "d4d8824d-3110-441b-9db1-fde9d44cb784",  # Connected Call Back later
}
# Every other disposition (Busy, Left voicemail, No answer, Wrong number, ...) is MISSED

# Contact states
PENDING       = "PENDING"        # in the pool, not called yet (round 0)
MISSED        = "MISSED"         # called, didn't connect (or no call in window)
SPOKE_UNCLEAR = "SPOKE_UNCLEAR"  # connected, KDM question unanswered
NOT_KDM       = "NOT_KDM"        # connected, confirmed not the KDM — leaves the pool
KDM           = "KDM"            # confirmed KDM — everyone else frozen
OPTED_OUT     = "OPTED_OUT"      # not interested, not the KDM — leaves the pool, never referenced
FROZEN        = "FROZEN"         # someone else became the KDM / closed the opportunity
CLOSED_LOST   = "CLOSED_LOST"    # the KDM said not interested — sequence over

LIVE_STATES = {PENDING, MISSED, SPOKE_UNCLEAR}
REFERENCEABLE_STATES = {MISSED, SPOKE_UNCLEAR, NOT_KDM}

STATUS_ACTIVE      = "active"
STATUS_KDM_FOUND   = "kdm_found"
STATUS_CLOSED_LOST = "closed_lost"

ICP_TIERS = ("Prime", "Core", "Opportunistic", "Non-ICP")


def icp_tier(rating):
    """Prime (MM) / Prime (SMB) -> 0 ... Non-ICP -> 3, blank -> 4. Size suffix ignored."""
    for i, tier in enumerate(ICP_TIERS):
        if (rating or "").startswith(tier):
            return i
    return len(ICP_TIERS)


def _rank(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")


def call_order(contacts):
    """contacts: {contact_id: props}. ICP rating, then kdm_rank ascending, then contact ID."""
    return sorted(
        contacts,
        key=lambda cid: (
            icp_tier(contacts[cid].get("contact_icp_rating")),
            _rank(contacts[cid].get("kdm_rank")),
            int(cid),
        ),
    )


def short_name(first, last):
    return f"{first or ''} {(last or '')[:1]}".strip()


def new_matrix(contacts):
    """Round 0: lock the pool and its call order."""
    order = call_order(contacts)
    return {
        "version": 1,
        "round": 0,
        "status": STATUS_ACTIVE,
        "kdm_contact_id": None,
        "order": order,
        "contacts": {
            cid: {
                "first_name": contacts[cid].get("firstname") or "",
                "last_name": contacts[cid].get("lastname") or "",
                "state": PENDING,
                "history": [],
            }
            for cid in order
        },
    }


def latest_call_in_window(calls, start_ms, end_ms):
    """calls: [{id, timestamp (epoch ms), disposition, kdm_status}]. Latest in window wins."""
    in_window = [c for c in calls if start_ms <= c["timestamp"] <= end_ms]
    return max(in_window, key=lambda c: (c["timestamp"], str(c["id"])), default=None)


def classify_call(call):
    # No call in the window counts as MISSED — HubSpot enforces "call everyone"
    if call is None:
        return MISSED
    disposition = call.get("disposition")
    kdm_status = call.get("kdm_status")
    if disposition == CONNECTED_NOT_INTERESTED:
        return CLOSED_LOST if kdm_status == "Yes" else OPTED_OUT
    if disposition in CONNECTED:
        return {"Yes": KDM, "No": NOT_KDM}.get(kdm_status, SPOKE_UNCLEAR)
    return MISSED


def _history_entry(round_no, call, state):
    disposition = call.get("disposition") if call else None
    return {
        "round": round_no,
        "call_id": str(call["id"]) if call else None,
        "outcome": DISPOSITION_LABELS.get(disposition, disposition or "No call"),
        "kdm_status": call.get("kdm_status") if call else None,
        "connected": disposition in CONNECTED or disposition == CONNECTED_NOT_INTERESTED,
        "state": state,
    }


def advance(matrix, round_no, calls_by_contact, window):
    """Apply round `round_no`'s calls. window = (start_ms, end_ms) of the round's task."""
    if round_no != matrix["round"] + 1 or not 1 <= round_no <= TOTAL_ROUNDS:
        raise ValueError(f"cannot apply round {round_no} to a matrix at round {matrix['round']}")
    if matrix["status"] == STATUS_CLOSED_LOST:
        raise ValueError("opportunity is closed_lost")

    m = copy.deepcopy(matrix)
    m["round"] = round_no
    start_ms, end_ms = window

    if m["status"] == STATUS_KDM_FOUND:
        cid = m["kdm_contact_id"]
        call = latest_call_in_window(calls_by_contact.get(cid, []), start_ms, end_ms)
        # Once confirmed, the KDM stays the KDM; only "not interested" ends the sequence
        state = CLOSED_LOST if classify_call(call) in (CLOSED_LOST, OPTED_OUT) else KDM
        if state == CLOSED_LOST:
            m["status"] = STATUS_CLOSED_LOST
        m["contacts"][cid]["state"] = state
        m["contacts"][cid]["history"].append(_history_entry(round_no, call, state))
        return m

    decisive = None
    for cid in m["order"]:
        contact = m["contacts"][cid]
        if contact["state"] not in LIVE_STATES:
            continue
        call = latest_call_in_window(calls_by_contact.get(cid, []), start_ms, end_ms)
        state = classify_call(call)
        contact["state"] = state
        contact["history"].append(_history_entry(round_no, call, state))
        if decisive is None and state in (KDM, CLOSED_LOST):
            decisive = cid

    if decisive:
        # First KDM decision in call order wins; every other live contact is frozen
        for cid in m["order"]:
            if cid != decisive and m["contacts"][cid]["state"] in LIVE_STATES | {KDM, CLOSED_LOST}:
                m["contacts"][cid]["state"] = FROZEN
        if m["contacts"][decisive]["state"] == KDM:
            m["status"] = STATUS_KDM_FOUND
            m["kdm_contact_id"] = decisive
        else:
            m["status"] = STATUS_CLOSED_LOST
    return m


def recipients(matrix):
    """Contacts who get this round's email(s), in call order."""
    if matrix["status"] == STATUS_CLOSED_LOST:
        return []
    if matrix["status"] == STATUS_KDM_FOUND:
        return [matrix["kdm_contact_id"]]
    return [cid for cid in matrix["order"] if matrix["contacts"][cid]["state"] in LIVE_STATES]


def slots_to_clear(before, after):
    """{contact_id: [slots]} for contacts who dropped out this round."""
    live_before = set(recipients(before))
    live_after = set(recipients(after))
    first_slot = ROUND_SLOTS[after["round"]][0]
    return {
        cid: list(range(first_slot, 6))
        for cid in before["order"]
        if cid in live_before and cid not in live_after
    }


def route_b_context(matrix, cid):
    """Scenario input for emails 2/3: recipient state + who else can be referenced."""
    contact = matrix["contacts"][cid]
    last = contact["history"][-1]
    others = []
    if matrix["status"] != STATUS_KDM_FOUND:
        others = [
            {"first_name": matrix["contacts"][o]["first_name"], "state": matrix["contacts"][o]["state"]}
            for o in matrix["order"]
            if o != cid
            and matrix["contacts"][o]["state"] in REFERENCEABLE_STATES
            and matrix["contacts"][o]["first_name"]
        ]
    return {
        "state": contact["state"],
        "connected_this_round": last["connected"],
        "last_outcome": last["outcome"],
        "others": others,
    }


def route_a_context(matrix, cid):
    """Emails 1/4/5 don't reference anyone; the KDM gets their last outcome as soft context."""
    contact = matrix["contacts"][cid]
    is_kdm = contact["state"] == KDM
    return {
        "state": contact["state"],
        "is_kdm": is_kdm,
        "last_outcome": contact["history"][-1]["outcome"] if is_kdm and contact["history"] else "",
    }


def next_task_text(matrix, company, job_title, contacts):
    """(title, notes) for the next call task. Empty when there is no next call."""
    next_round = matrix["round"] + 1
    if matrix["status"] == STATUS_CLOSED_LOST or next_round > TOTAL_ROUNDS:
        return "", ""

    live = recipients(matrix)
    names = [
        short_name(matrix["contacts"][cid]["first_name"], matrix["contacts"][cid]["last_name"])
        for cid in live
    ]
    head = " · ".join(p for p in [f"Call {next_round} of {TOTAL_ROUNDS}", company, job_title] if p)
    if matrix["status"] == STATUS_KDM_FOUND:
        title = f"{head} · KDM: {names[0]}"
        reminder = "Confirmed KDM. Log the call with Call outcome."
    else:
        title = f"{head} · Call in order: {' → '.join(names)}"
        reminder = (
            "Call everyone in order unless a KDM is confirmed. "
            "Log each call with Call outcome and 'KDM on this call?'."
        )

    lines = []
    for i, cid in enumerate(live, 1):
        props = contacts.get(cid, {})
        entry = matrix["contacts"][cid]
        full_name = f"{entry['first_name']} {entry['last_name']}".strip()
        role = f" ({props['jobtitle']})" if props.get("jobtitle") else ""
        phone = props.get("phone") or props.get("mobilephone") or "no phone"
        last = entry["history"][-1]["outcome"] if entry["history"] else "not called yet"
        lines.append(f"{i}. {full_name}{role} · {phone} · last outcome: {last}")
    lines += ["", reminder]
    return title, "\n".join(lines)
