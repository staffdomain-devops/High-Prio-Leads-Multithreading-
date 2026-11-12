"""
Deterministic call matrix — pure logic, no IO.

Each round: previous matrix + this round's calls -> new matrix.
From the new matrix we derive who gets an email, which slots to clear, the
per-recipient context for the email routes, and the next call task's text.

Nothing here talks to HubSpot or Claude, so every scenario is unit-tested
(tests/test_build_matrix.py).
"""

import copy
import re

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
    "cd69394d-8b05-4f4e-a70b-26dc03316885": "Connected KDM",        # added 2026-11-06
    "52fef441-f82f-4024-90b8-3c5fb4f03c6d": "Connected - Not KDM",  # added 2026-11-06
}
CONNECTED_NOT_INTERESTED = "a44f5d58-3e2f-44ce-afbf-adf524cb190e"
CONNECTED = {
    "f240bbac-87c9-4f6e-bf70-924b57d47db7",  # Connected
    "e94640e4-419c-4ab9-9b49-fc383da05778",  # Connected Interested
    "d4d8824d-3110-441b-9db1-fde9d44cb784",  # Connected Call Back later
}
# Every other disposition (Busy, Left voicemail, No answer, Wrong number, ...) is MISSED


def _norm(label):
    """'Connected - Not KDM ' / 'connected–not–kdm' -> 'connected not kdm'."""
    return re.sub(r"[\s\-\u2013\u2014_]+", " ", (label or "").strip().lower()).strip()


# Call outcomes matched by LABEL. The two KDM outcomes carry the answer to "KDM on this call?"
# in the outcome itself, so the rep picks one option instead of filling two fields. plan_round
# attaches the live label from the portal's outcome list to every call, so adding these outcomes
# in HubSpot (Settings > Calling > Call outcomes) is enough: no GUID needs to be pasted here.
# Matching ignores case, spaces and dashes.
KDM_LABELS = {"connected kdm", "connected the kdm", "connected decision maker"}
NOT_KDM_LABELS = {"connected not kdm", "connected not the kdm", "connected not decision maker"}
NOT_INTERESTED_LABELS = {"connected not interested"}
CONNECTED_LABELS = {"connected", "connected interested", "connected call back later"}


def outcome_label(call):
    """The call outcome as text: the live label plan_round attached, else the built-in portal map."""
    if not call:
        return ""
    return (call.get("outcome") or DISPOSITION_LABELS.get(call.get("disposition")) or "").strip()


def is_connected(call):
    return _norm(outcome_label(call)).startswith("connected")

# Contact states. Anyone who connects stops getting emails; only MISSED contacts continue.
PENDING       = "PENDING"        # in the pool, not called yet (round 0)
MISSED        = "MISSED"         # called, didn't connect (or no call in window) — gets the next email
SPOKE_UNCLEAR = "SPOKE_UNCLEAR"  # connected, KDM unclear — stops, still referenced
NOT_KDM       = "NOT_KDM"        # connected, confirmed not the KDM — stops, still referenced
KDM           = "KDM"            # connected, confirmed KDM — the whole sequence stops
OPTED_OUT     = "OPTED_OUT"      # not interested, not the KDM — stops, never referenced
FROZEN        = "FROZEN"         # stopped because the KDM was reached or someone replied
CLOSED_LOST   = "CLOSED_LOST"    # the KDM said not interested — the whole sequence stops
REPLIED       = "REPLIED"        # replied by email — the whole sequence stops, the rep owns the thread

LIVE_STATES = {PENDING, MISSED}
REFERENCEABLE_STATES = {MISSED, SPOKE_UNCLEAR, NOT_KDM}

STATUS_ACTIVE      = "active"
STATUS_KDM_FOUND   = "kdm_found"    # terminal: the rep has the KDM, no more emails
STATUS_CLOSED_LOST = "closed_lost"  # terminal
STATUS_REPLIED     = "replied"      # terminal: a contact wrote back, the rep takes it from the inbox

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


def new_matrix(contacts, started_at=None):
    """Round 0: lock the pool and its call order. started_at = epoch ms the sequence started."""
    order = call_order(contacts)
    return {
        "version": 1,
        "round": 0,
        "started_at": started_at,
        "status": STATUS_ACTIVE,
        "kdm_contact_id": None,
        "replied_contact_ids": [],
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
    # No call in the window stays MISSED (still in the pool) but gets no email this round
    if call is None:
        return MISSED
    label = _norm(outcome_label(call))
    kdm_status = call.get("kdm_status")
    if label in KDM_LABELS:
        return KDM
    if label in NOT_KDM_LABELS:
        return NOT_KDM
    if label in NOT_INTERESTED_LABELS:
        # Not interested drops that contact only; the others continue and never mention them.
        # The legacy "KDM on this call? = Yes" still closes the whole opportunity as lost.
        return CLOSED_LOST if kdm_status == "Yes" else OPTED_OUT
    if label in CONNECTED_LABELS:
        # Plain "Connected" outcomes still read the legacy property when the rep filled it in
        return {"Yes": KDM, "No": NOT_KDM}.get(kdm_status, SPOKE_UNCLEAR)
    return MISSED


def _history_entry(round_no, call, state, outcome=None):
    return {
        "round": round_no,
        "call_id": str(call["id"]) if call else None,
        "outcome": outcome or outcome_label(call) or (call.get("disposition") if call else None) or "No call",
        "kdm_status": call.get("kdm_status") if call else None,
        "connected": is_connected(call),
        "state": state,
    }


def advance(matrix, round_no, calls_by_contact, window, opted_out=frozenset(), replied=frozenset()):
    """
    Apply round `round_no`'s calls. window = (start_ms, end_ms) of the round's task.
    opted_out: contacts who unsubscribed from email.
    replied: contacts who replied by email since the sequence started (see replies.py).
    A reply from anyone still in the pool stops the whole sequence: the rep owns the
    conversation now, and emails to the others would describe them as unreached.
    A decisive call result in the same round (KDM reached, KDM not interested) still
    sets the status.
    """
    if round_no != matrix["round"] + 1 or not 1 <= round_no <= TOTAL_ROUNDS:
        raise ValueError(f"cannot apply round {round_no} to a matrix at round {matrix['round']}")
    if matrix["status"] != STATUS_ACTIVE:
        raise ValueError(f"opportunity is {matrix['status']}")

    m = copy.deepcopy(matrix)
    m["round"] = round_no
    start_ms, end_ms = window

    decisive = None
    replied_now = []
    for cid in m["order"]:
        contact = m["contacts"][cid]
        if contact["state"] not in LIVE_STATES:
            continue
        call = latest_call_in_window(calls_by_contact.get(cid, []), start_ms, end_ms)
        state = classify_call(call)
        outcome = None
        # A decisive call result wins. Otherwise a reply beats whatever the call said, and
        # an unsubscribe takes a missed contact out of the pool
        if state not in (KDM, CLOSED_LOST) and cid in replied:
            state, outcome = REPLIED, "Replied by email"
            replied_now.append(cid)
        elif state == MISSED and cid in opted_out:
            state, outcome = OPTED_OUT, "Unsubscribed"
        contact["state"] = state
        contact["history"].append(_history_entry(round_no, call, state, outcome))
        if decisive is None and state in (KDM, CLOSED_LOST):
            decisive = cid

    if decisive:
        # Reaching the KDM ends the sequence. First KDM in call order wins; everyone else is frozen
        for cid in m["order"]:
            if cid != decisive and m["contacts"][cid]["state"] in LIVE_STATES | {KDM, CLOSED_LOST}:
                m["contacts"][cid]["state"] = FROZEN
        if m["contacts"][decisive]["state"] == KDM:
            m["status"] = STATUS_KDM_FOUND
            m["kdm_contact_id"] = decisive
        else:
            m["status"] = STATUS_CLOSED_LOST
    elif replied_now:
        # Someone wrote back. Nobody gets another email; the rep continues from the inbox
        for cid in m["order"]:
            if m["contacts"][cid]["state"] in LIVE_STATES:
                m["contacts"][cid]["state"] = FROZEN
        m["status"] = STATUS_REPLIED
        m["replied_contact_ids"] = replied_now
    return m


def recipients(matrix):
    """Contacts who get this round's email(s), in call order. Nobody once the sequence has stopped."""
    if matrix["status"] != STATUS_ACTIVE:
        return []
    return [cid for cid in matrix["order"] if matrix["contacts"][cid]["state"] in LIVE_STATES]


def _no_call_this_round(matrix, cid):
    history = matrix["contacts"][cid]["history"]
    return matrix["round"] >= 1 and bool(history) and history[-1]["round"] == matrix["round"] \
        and history[-1]["call_id"] is None


def uncalled(matrix):
    """Live contacts the rep didn't call this round — no email for them (it would claim a call)."""
    return [cid for cid in recipients(matrix) if _no_call_this_round(matrix, cid)]


def email_recipients(matrix):
    """Contacts who actually get this round's email: live and (from round 1) called this round."""
    skipped = set(uncalled(matrix))
    return [cid for cid in recipients(matrix) if cid not in skipped]


def release_contacts(before, after):
    """Contacts whose hplmt_active_opportunity_id should be cleared after this round."""
    if not recipients(after) or after["round"] >= TOTAL_ROUNDS:
        return list(after["order"])
    live_after = set(recipients(after))
    return [cid for cid in recipients(before) if cid not in live_after]


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


def display_names(matrix):
    """{contact_id: the name emails use for that person}. First name alone, unless someone else in
    the pool shares it (case-insensitive): then first name + surname for all of them, for the whole
    sequence, so an email never says "you, Michael or Michael". No surname on record → first name.
    No first name → "" (that contact is never named)."""
    contacts = matrix.get("contacts") or {}
    counts = {}
    for c in contacts.values():
        key = (c.get("first_name") or "").strip().lower()
        if key:
            counts[key] = counts.get(key, 0) + 1
    names = {}
    for cid, c in contacts.items():
        first = (c.get("first_name") or "").strip()
        last = (c.get("last_name") or "").strip()
        names[cid] = f"{first} {last}" if first and last and counts[first.lower()] > 1 else first
    return names


def colleague(name, recipient_first_name):
    """'your colleague Michael Tan' when the recipient is also a Michael, else the name unchanged.
    Stops a Michael reading "I also tried Michael" about someone else."""
    first = (recipient_first_name or "").strip().lower()
    if first and name and name.split()[0].lower() == first:
        return f"your colleague {name}"
    return name


def route_b_context(matrix, cid):
    """Scenario input for emails 2/3: recipient state + who else can be referenced.
    `name` is the display name (surname added when first names collide, see display_names)."""
    contact = matrix["contacts"][cid]
    names = display_names(matrix)
    others = [
        {
            "first_name": matrix["contacts"][o]["first_name"],
            "name": names[o],
            "state": matrix["contacts"][o]["state"],
            # Outcome of the call that set their state — drives how they are referenced
            "outcome": matrix["contacts"][o]["history"][-1]["outcome"],
        }
        for o in matrix["order"]
        if o != cid
        and matrix["contacts"][o]["state"] in REFERENCEABLE_STATES
        and names[o]
        and not _no_call_this_round(matrix, o)  # never claim we tried someone we didn't call
    ]
    return {
        "state": contact["state"],
        "last_outcome": contact["history"][-1]["outcome"],
        "others": others,
        "recipient_first_name": contact["first_name"],
    }


def route_a_context(matrix, cid):
    """Emails 1/4/5 never reference calls. Email 1 names the other people in the pool
    ("not sure if it's you, Ben or Cara"); emails 4/5 ignore `others`. Names are display names."""
    names = display_names(matrix)
    others = [
        names[o]
        for o in matrix["order"]
        if o != cid and matrix["contacts"][o]["state"] in LIVE_STATES and names[o]
    ]
    return {"state": matrix["contacts"][cid]["state"], "others": others,
            "recipient_first_name": matrix["contacts"][cid]["first_name"]}


MAX_NAMED_OTHERS = 2


def who_clause(others, recipient_first_name=None):
    """'you' / 'you or Ben' / 'you, Ben or Cara' / 'you, Ben, Cara or someone else there'.
    Fills the email 1 opener. Caps at MAX_NAMED_OTHERS names so it never reads as a roll call.
    Accepts route A `others` (names) or route B `others` (dicts with name / first_name).
    Someone who shares the recipient's first name becomes "your colleague Michael Tan"."""
    names = [(o.get("name") or o["first_name"]) if isinstance(o, dict) else o for o in others]
    names = [colleague(n, recipient_first_name) for n in names if n]
    if not names:
        return "you"
    named = names[:MAX_NAMED_OTHERS]
    last = "someone else there" if len(names) > MAX_NAMED_OTHERS else named.pop()
    return ", ".join(["you"] + named) + f" or {last}"


def next_task_text(matrix, company, job_title, contacts, buyer_frames=None):
    """(title, notes) for the next call task. Empty when there is no next call.

    buyer_frames ({contact ID: frame} from the research payload) adds an "Angle:" line per contact.
    """
    next_round = matrix["round"] + 1
    live = recipients(matrix)
    if not live or next_round > TOTAL_ROUNDS:
        return "", ""

    names = [
        short_name(matrix["contacts"][cid]["first_name"], matrix["contacts"][cid]["last_name"])
        for cid in live
    ]
    head = " · ".join(p for p in [f"Call {next_round} of {TOTAL_ROUNDS}", company, job_title] if p)
    title = f"{head} · Call in order: {' → '.join(names)}"
    reminder = (
        "Call everyone in order unless a KDM is confirmed. Log each call with Call outcome: "
        "'Connected - KDM' when you reached the decision maker (stops the sequence for everyone), "
        "'Connected - Not KDM' when it sits with someone else, "
        "'Connected Not Interested' to drop that person. 'KDM on this call?' is no longer needed."
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
        frame = (buyer_frames or {}).get(cid)
        if frame:
            lines.append(f"   Angle: {frame}")
        links = [f"{label}: {props[key]}" for label, key in
                 (("LinkedIn", "linkedin_url"), ("Sales Nav", "sales_navigator_url"), ("Email", "email"))
                 if props.get(key)]
        if links:
            lines.append(f"   {' · '.join(links)}")
    lines += ["", reminder]
    return title, "\n".join(lines)
