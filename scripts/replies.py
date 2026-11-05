"""
Reply detection — pure logic, no IO.

A logged email counts as a reply from a contact when it is incoming, arrived after the
sequence started, comes from the contact (or sits in the thread of an email we sent them),
and is not an auto-responder. plan_round applies this to the emails on each live contact;
the HubSpot enrol action (hubspot/enroll_sequences.js) applies the same rule as a backstop
for email 5, so keep the two in step.

Email dicts: {id, direction, timestamp (epoch ms), from_email, subject, thread_id}.
"""

import re

INCOMING = "INCOMING_EMAIL"
OUTGOING = "EMAIL"

# Whole phrases only. Fragments such as "out", "auto" or "reply" would swallow real
# replies ("Re: Automotive Technician role", "About outsourcing").
AUTO_REPLY_PATTERNS = [
    r"automatic reply",
    r"auto[- ]?reply",
    r"auto[- ]?response",
    r"automated (reply|response|message)",
    r"out of (the )?office",
    r"away from (the |my )?(office|desk)",
    r"\booo\b",
    r"on (annual |parental |maternity |paternity |sick |extended )?leave",
    r"undeliverable",
    r"delivery (status|failure|has failed)",
    r"mail delivery",
    r"failure notice",
    r"returned mail",
    r"message not delivered",
]
_AUTO_REPLY = re.compile("|".join(f"(?:{p})" for p in AUTO_REPLY_PATTERNS), re.IGNORECASE)


def is_auto_reply(subject):
    return bool(_AUTO_REPLY.search(subject or ""))


def _addr(value):
    return (value or "").strip().lower()


def sent_thread_ids(emails):
    """Thread IDs of the emails we sent this contact."""
    return {e.get("thread_id") for e in emails if e.get("direction") == OUTGOING and e.get("thread_id")}


def is_reply(email, contact_email, sent_threads, since_ms):
    """True when `email` is a genuine reply from the contact, received at or after since_ms."""
    if email.get("direction") != INCOMING:
        return False
    if (email.get("timestamp") or 0) < (since_ms or 0):
        return False
    from_contact = bool(_addr(contact_email)) and _addr(email.get("from_email")) == _addr(contact_email)
    in_our_thread = bool(email.get("thread_id")) and email.get("thread_id") in sent_threads
    if not (from_contact or in_our_thread):
        return False
    return not is_auto_reply(email.get("subject"))


def replies_by_contact(emails_by_contact, contact_emails, since_ms):
    """{contact_id: latest qualifying reply} for every contact who replied since since_ms.

    emails_by_contact: {cid: [email dicts]} — all emails logged on the contact.
    contact_emails: {cid: email address}.
    """
    out = {}
    for cid, emails in emails_by_contact.items():
        threads = sent_thread_ids(emails)
        hits = [e for e in emails if is_reply(e, contact_emails.get(cid), threads, since_ms)]
        if hits:
            out[cid] = max(hits, key=lambda e: (e.get("timestamp") or 0, str(e.get("id"))))
    return out
