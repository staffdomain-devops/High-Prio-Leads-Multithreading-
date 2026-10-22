"""
Route B — emails 2 and 3, generated from a generic call scenario.

The scenario (what happened on this person's call, who else can be referenced)
comes from the call matrix and is turned into fixed instructions here, so the
model only writes prose around facts it is given.
"""

import os
import sys

import build_matrix as bm
import emailgen
from claude import MODEL
from utils import write_dlq

WHAT_HAPPENED = {
    bm.MISSED: "We called this person this round and did not get through.",
    bm.SPOKE_UNCLEAR: ("We spoke with this person by phone this round (outcome: {outcome}). "
                       "They have not confirmed whether they are the decision maker."),
    "KDM_CONNECTED": ("We spoke with this person by phone this round (outcome: {outcome}). "
                      "They are the confirmed decision maker for this role."),
    "KDM_MISSED": ("This person is the confirmed decision maker for this role and we have spoken before. "
                   "We called again this round and did not get through."),
}

OPENERS = {
    2: {
        bm.MISSED: ('Open with exactly: "I just tried giving you a quick call. To be fair, I\'m not entirely sure '
                    'you\'re the right person to speak with about this, but I noticed you recently posted a role '
                    'for [job title]."'),
        bm.SPOKE_UNCLEAR: ('Open with exactly: "Thanks for taking my call earlier." Then, in your own words, say '
                           'you\'re still not entirely sure they\'re the right person to speak with about the '
                           '[job title] role they posted.'),
        "KDM_CONNECTED": ('Open with exactly: "Great speaking with you earlier." Then briefly tie back to the '
                          '[job title] role they posted. Do NOT question whether they are the right person.'),
        "KDM_MISSED": ('Open with exactly: "I just tried giving you a quick call about the [job title] role you '
                       'recently posted." Do NOT question whether they are the right person.'),
    },
    3: {
        bm.MISSED: ('Open with exactly: "I may be barking up the wrong tree here, but I just tried giving you a '
                    'quick call about the [job title] role I saw posted online, and haven\'t managed to catch you."'),
        bm.SPOKE_UNCLEAR: 'Open with exactly: "Thanks again for the chat earlier about the [job title] role."',
        "KDM_CONNECTED": 'Open with exactly: "Good speaking with you again earlier about the [job title] role."',
        "KDM_MISSED": ('Open with exactly: "I just tried giving you a quick call about the [job title] role and '
                       'didn\'t manage to catch you."'),
    },
}

CLOSE_POOL = "Ask whether they're the right person, and if not, whether they could point you in the right direction."
CLOSE_KDM = ("Offer to send a few candidate profiles or book a short call. "
             "Do NOT ask whether they are the right person or who else to speak to.")

REFERENCE_FACTS = {
    bm.MISSED: "you also tried calling {name} and didn't get through",
    bm.SPOKE_UNCLEAR: "you had a quick chat with {name}",
    bm.NOT_KDM: "{name} mentioned this probably sits with someone else",
}


def scenario_key(ctx):
    if ctx["state"] == bm.KDM:
        return "KDM_CONNECTED" if ctx["connected_this_round"] else "KDM_MISSED"
    return ctx["state"]


def reference_line(others, company):
    if not others:
        return "REFERENCE LINE: none. Do not mention anyone else at the company."
    facts = "\n".join(f"  - {REFERENCE_FACTS[o['state']].format(name=o['first_name'])}" for o in others)
    return (
        f"REFERENCE LINE: add exactly one natural sentence about who else at {company or 'the company'} "
        f"you have been trying to reach, using only these facts:\n{facts}\n"
        "  First names only, no job titles. Refer to these people by name only: no he, she, his or her. "
        "Do not list them; weave them into one sentence."
    )


def scenario_instructions(email_no, ctx, company):
    """Fixed instructions for one recipient. Pure — unit-tested."""
    key = scenario_key(ctx)
    return "\n\n".join([
        "WHAT HAPPENED: " + WHAT_HAPPENED[key].format(outcome=ctx["last_outcome"]),
        "OPENER: " + OPENERS[email_no][key],
        reference_line(ctx["others"], company),
        "CLOSE: " + (CLOSE_KDM if ctx["state"] == bm.KDM else CLOSE_POOL),
    ])


def main():
    cid = os.environ["CONTACT_ID"]
    plan, research = emailgen.load_inputs()
    round_no = plan["round"]
    write_dlq(plan["opportunity_id"], cid, "generate_route_b", "Script started", retry_count=0)

    if round_no not in (1, 2):
        raise RuntimeError(f"route B handles rounds 1 and 2, got {round_no}")
    if emailgen.already_written(cid, round_no):
        print(f"Contact {cid}: round {round_no} email already written — skipping (re-run)")
        return

    slots = bm.ROUND_SLOTS[round_no]
    email_no = slots[0]
    ctx = plan["contexts"][cid]
    tokens = emailgen.tokens_for(plan, research, cid)
    tokens["scenario"] = scenario_instructions(email_no, ctx, tokens["contact.company"])
    tokens["email_spec"] = emailgen.render(f"specs/email_{email_no}.md", tokens)
    tokens["output_format"] = emailgen.output_format(slots)
    prompt = emailgen.render("route_b_prompt.md", tokens)

    print(f"Route B · round {round_no} · contact {cid} ({tokens['contact.first_name']}) · email {email_no}")
    print(tokens["scenario"])
    print(f"Calling {MODEL}...")
    try:
        emails = emailgen.generate(prompt, slots, f"raw_{cid}.txt")
        emailgen.write_contact(plan, research, cid, emails)
    except Exception as exc:
        write_dlq(plan["opportunity_id"], cid, "generate_route_b", exc, retry_count=1)
        raise


if __name__ == "__main__":
    sys.exit(main())
