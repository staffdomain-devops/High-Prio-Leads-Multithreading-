"""
Post-generation fact check. Pure: no IO, no HubSpot, no Claude.

The prompt tells the model which other people it may mention and what happened
with each of them. This module reads the finished email and refuses it when the
words don't match the facts, so a claim like "had a quick chat with Cara" can
never reach HubSpot unless Cara actually connected.

Rules, per other pool contact whose first name appears in the body:
  - not in the allowed list              -> fail (opted out, frozen, uncalled, or not referenceable this email)
  - allowed, state MISSED                -> the sentence must not say we spoke to them
  - allowed, connected (SPOKE_UNCLEAR,
    NOT_KDM)                             -> the sentence must not say we missed them
  - allowed, SPOKE_UNCLEAR               -> the sentence must not say they pointed us elsewhere
Plus, for emails that are not allowed to mention calls at all (Route A: 1, 4, 5),
any call claim in the body fails. And when the caller says email 1 must name the
other people on the list (`required_names`, from the prompt set's who-clause), a
body that leaves any of them out fails.

Names shorter than 3 letters, or equal to the recipient's own first name, are not
checked (too many false matches). A failure is meant to stop that one email and
land in the dead-letter record; the other contacts' emails are unaffected.
"""

import re

import build_matrix as bm

# Phrases that claim we reached someone
CONNECTED_PHRASES = (
    "chat with", "had a chat", "had a quick chat", "spoke with", "spoke to", "spoke briefly", "talked to", "talked with",
    "got hold of", "got through to", "caught up with", "reached", "who reckons", "who said", "who mentioned",
    "keen to hear", "asked me to call back", "call back another time", "pointed me", "sits with someone else",
    "did get", "managed to get",
)
# Phrases that claim we missed someone
MISSED_PHRASES = (
    "didn't get through", "did not get through", "no luck", "couldn't get", "could not get", "didn't catch",
    "did not catch", "missed", "tried", "no answer", "voicemail", "haven't managed", "haven't been able",
)
# Phrases that say they told us it's not them
REFERRAL_PHRASES = ("sits with someone else", "pointed me", "not the right person", "should be talking to someone else",
                    "someone else's call", "said I should", "told me to")
# Past-tense claims of a call having happened (for emails that must not reference calls).
# Deliberately not "quick call", "on the phone" or "pick up": email 1 is allowed to say a call is coming.
CALL_PHRASES = (
    "had a chat", "had a quick chat", "spoke with", "spoke to", "spoke briefly", "talked to", "talked with",
    "got hold of", "got through to", "caught up with",
    "tried calling", "tried to call", "tried giving you a call", "tried you", "called you", "gave you a call",
    "didn't catch you", "did not catch you", "couldn't catch you", "didn't get through", "did not get through",
    "no luck", "voicemail", "left you a message", "left a message",
)

MIN_NAME_LEN = 3


class FactCheckError(RuntimeError):
    pass


def _sentences(text):
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def _mentions(sentence, name):
    return re.search(rf"\b{re.escape(name)}\b", sentence, flags=re.I) is not None


def _clauses(sentence):
    """Split on commas, semicolons and ' and ' so two people in one sentence are judged separately."""
    return [c.strip() for c in re.split(r",|;|\band\b", sentence) if c.strip()]


def _window(sentence, name):
    """The clause that names the person, plus the next clause only when it's a relative clause about them
    ('a quick chat with Ben, who reckons this sits with someone else')."""
    clauses = _clauses(sentence)
    for i, c in enumerate(clauses):
        if _mentions(c, name):
            nxt = clauses[i + 1] if i + 1 < len(clauses) else ""
            if re.match(r"^(who|which|that)\b", nxt, flags=re.I):
                return c + " " + nxt
            return c
    return sentence


def _has_any(sentence, phrases):
    low = sentence.lower()
    return any(p in low for p in phrases)


def check_body(body, recipient_first_name, pool_first_names, allowed_others, calls_allowed):
    """
    body:                the email body (plain text or with <br>)
    recipient_first_name: so the recipient's own name is never flagged
    pool_first_names:    first names of every contact in the Opportunity's pool
    allowed_others:      {first_name: state} for the people this email may mention (route B context `others`)
    calls_allowed:       False for emails that must not reference calls at all (route A)
    Returns a list of problems. Empty means the email passes.
    """
    problems = []
    sentences = _sentences(body)
    allowed = {n.lower(): st for n, st in allowed_others.items() if n}
    checkable = {n for n in pool_first_names
                 if n and len(n) >= MIN_NAME_LEN and n.lower() != (recipient_first_name or "").lower()}

    if not calls_allowed:
        for s in sentences:
            if _has_any(s, CALL_PHRASES):
                problems.append(f"claims a call but this email must not reference calls: \"{s}\"")

    for name in checkable:
        for s in sentences:
            if not _mentions(s, name):
                continue
            state = allowed.get(name.lower())
            if state is None:
                problems.append(f"mentions {name}, who must not be referenced in this email: \"{s}\"")
                continue
            w = _window(s, name)
            if state == bm.MISSED and _has_any(w, CONNECTED_PHRASES):
                problems.append(f"says we reached {name}, but {name} was missed: \"{s}\"")
            if state in (bm.SPOKE_UNCLEAR, bm.NOT_KDM) and _has_any(w, MISSED_PHRASES) and not _has_any(w, CONNECTED_PHRASES):
                problems.append(f"says we missed {name}, but {name} connected: \"{s}\"")
            if state == bm.SPOKE_UNCLEAR and _has_any(w, REFERRAL_PHRASES):
                problems.append(f"says {name} pointed us elsewhere, but that was never confirmed: \"{s}\"")
    return problems


def allowed_others_for(plan, cid):
    """{name: state} this contact's email may mention, from the plan's route context. Names are the
    display names the prompt was given (surname included when two people share a first name)."""
    ctx = (plan.get("contexts") or {}).get(cid) or {}
    others = ctx.get("others") or []
    if others and isinstance(others[0], dict):            # route B: facts about each person
        return {(o.get("name") or o.get("first_name")): o["state"] for o in others if o.get("name") or o.get("first_name")}
    if plan.get("round") == 0:                              # route A, email 1: names only, no call facts
        return {n: bm.PENDING for n in others if n}
    return {}                                               # route A, emails 4/5: nobody


def ambiguous_first_names(pool_names):
    """First names that two or more display names share ("Michael Brown", "Michael Tan" -> {"michael"}).
    A body that uses such a first name on its own is ambiguous and is refused."""
    firsts = [n.split()[0].lower() for n in pool_names if n and " " in n.strip()]
    return {f for f in set(firsts) if firsts.count(f) > 1 and len(f) >= MIN_NAME_LEN}


def bare_name_problems(body, recipient_first_name, pool_names):
    problems = []
    recipient = (recipient_first_name or "").strip().lower()
    for first in ambiguous_first_names(pool_names):
        if first == recipient:      # "Hi Michael" greets the recipient; can't be told apart, so not checked
            continue
        full = [n for n in pool_names if n.split()[0].lower() == first]
        for s in _sentences(body):
            if _mentions(s, first) and not any(_mentions(s, n) for n in full):
                problems.append(f"says {first.title()} without a surname, but {len(full)} people there are called "
                                f"{first.title()} ({', '.join(full)}): \"{s}\"")
    return problems


def missing_names(body, required_names):
    """The required first names that the body never mentions."""
    sentences = _sentences(body)
    return [n for n in required_names if n and not any(_mentions(s, n) for s in sentences)]


def check_emails(plan, cid, emails, required_names=()):
    """Check every email for this contact against the plan. Raises FactCheckError with all problems.
    `required_names`: first names email 1 must mention (the prompt set's who-clause); empty means no such rule."""
    matrix = plan.get("matrix_after") or {}
    names = bm.display_names(matrix)                     # surname added where first names collide
    pool = list(names.values())
    recipient_first = (plan.get("contacts", {}).get(cid) or {}).get("firstname", "")
    recipient = names.get(cid) or recipient_first
    allowed = allowed_others_for(plan, cid)
    calls_allowed = plan.get("route") == "B"
    problems = []
    for n, email in emails.items():
        for p in check_body(email.get("body", ""), recipient, pool, allowed, calls_allowed):
            problems.append(f"email_{n}: {p}")
        for p in bare_name_problems(email.get("body", ""), recipient_first, pool):
            problems.append(f"email_{n}: {p}")
        for p in check_body(email.get("subject", ""), recipient, pool, allowed, calls_allowed):
            if "must not be referenced" in p or "reached" in p:
                problems.append(f"email_{n} subject: {p}")
        if int(n) == 1 and required_names:
            for name in missing_names(email.get("body", ""), required_names):
                problems.append(f"email_{n}: does not name {name}, who the opening sentence must name")
    if problems:
        raise FactCheckError("\n".join(problems))
    return True
