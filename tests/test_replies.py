import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import replies as rp  # noqa: E402

ALICE = "alice@example.com"
SEQUENCE_START = 1_000


def email(direction=rp.INCOMING, ts=2_000, from_email=ALICE, subject="Re: Junior Buying Assistant, Brisbane",
          thread="thr-1", eid="e1"):
    return {"id": eid, "direction": direction, "timestamp": ts, "from_email": from_email,
            "subject": subject, "thread_id": thread}


OUR_THREADS = {"thr-1"}


def test_incoming_email_from_the_contact_is_a_reply():
    assert rp.is_reply(email(), ALICE, OUR_THREADS, SEQUENCE_START)


def test_our_own_sent_email_is_not_a_reply():
    assert not rp.is_reply(email(direction=rp.OUTGOING, from_email="rep@staffdomain.com"), ALICE, OUR_THREADS,
                           SEQUENCE_START)


def test_hubspot_notification_logged_on_the_contact_is_not_a_reply():
    # The portal logs HubSpot's own task notifications as incoming emails on contacts
    notification = email(from_email="noreply@notifications.hubspot.com", thread="other",
                         subject='You\'ve been assigned the task "Deal: ..."')
    assert not rp.is_reply(notification, ALICE, OUR_THREADS, SEQUENCE_START)


def test_reply_in_our_thread_from_another_address_counts():
    # Replied from a personal address, but it threads on the email we sent
    assert rp.is_reply(email(from_email="alice.personal@gmail.com", thread="thr-1"), ALICE, OUR_THREADS,
                       SEQUENCE_START)


def test_incoming_from_a_stranger_in_an_unknown_thread_does_not_count():
    assert not rp.is_reply(email(from_email="someone@else.com", thread="nope"), ALICE, OUR_THREADS, SEQUENCE_START)


def test_reply_before_the_sequence_started_is_ignored():
    assert not rp.is_reply(email(ts=SEQUENCE_START - 1), ALICE, OUR_THREADS, SEQUENCE_START)
    assert rp.is_reply(email(ts=SEQUENCE_START), ALICE, OUR_THREADS, SEQUENCE_START)


def test_from_address_match_ignores_case_and_whitespace():
    assert rp.is_reply(email(from_email=" Alice@Example.com "), ALICE, set(), SEQUENCE_START)


def test_blank_contact_email_never_matches_a_blank_sender():
    assert not rp.is_reply(email(from_email="", thread="nope"), "", OUR_THREADS, SEQUENCE_START)


@pytest.mark.parametrize("subject", [
    "Automatic reply: Junior Buying Assistant, Brisbane",
    "Auto-Reply: Re: your email",
    "AutoReply",
    "Out of Office: Re: Junior Buying Assistant",
    "Out of the office until Monday",
    "Away from my desk",
    "OOO - back 12 Nov",
    "On annual leave",
    "On leave",
    "Undeliverable: Junior Buying Assistant, Brisbane",
    "Delivery Status Notification (Failure)",
    "Mail delivery failed: returning message to sender",
    "Automated response",
])
def test_auto_responders_are_not_replies(subject):
    assert rp.is_auto_reply(subject)
    assert not rp.is_reply(email(subject=subject), ALICE, OUR_THREADS, SEQUENCE_START)


@pytest.mark.parametrize("subject", [
    # Fragments like "out", "auto" or "reply" must not swallow these
    "Re: Automotive Technician role",
    "About outsourcing the buying team",
    "Re: Headcount layout for Q1",
    "Quick reply on the Assistant Buyer role",
    "Re: Junior Buying Assistant, Brisbane",
    "",
])
def test_real_subjects_are_replies(subject):
    assert not rp.is_auto_reply(subject)
    assert rp.is_reply(email(subject=subject), ALICE, OUR_THREADS, SEQUENCE_START)


def test_sent_thread_ids_only_counts_our_outgoing_emails():
    emails = [
        email(direction=rp.OUTGOING, from_email="rep@staffdomain.com", thread="thr-1"),
        email(direction=rp.OUTGOING, from_email="rep@staffdomain.com", thread=None),
        email(direction=rp.INCOMING, thread="thr-9"),
    ]
    assert rp.sent_thread_ids(emails) == {"thr-1"}


def test_replies_by_contact_returns_the_latest_reply_per_contact():
    emails_by_contact = {
        "101": [
            email(direction=rp.OUTGOING, from_email="rep@staffdomain.com", thread="thr-1", eid="sent"),
            email(ts=2_000, eid="first"),
            email(ts=3_000, eid="second"),
            email(ts=4_000, subject="Automatic reply", eid="ooo"),
        ],
        "102": [email(ts=2_500, from_email="noreply@notifications.hubspot.com", thread="other")],
        "103": [],
    }
    got = rp.replies_by_contact(emails_by_contact, {"101": ALICE, "102": "ben@example.com", "103": None},
                                SEQUENCE_START)
    assert list(got) == ["101"]
    assert got["101"]["id"] == "second"
