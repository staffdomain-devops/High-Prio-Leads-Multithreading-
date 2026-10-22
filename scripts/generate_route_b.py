"""
Route B — emails 2 and 3, generated from a generic call scenario.

Only contacts we have not reached get these emails (anyone who connects stops).
The scenario (who else can be referenced, and what happened with them) comes
from the call matrix and is turned into fixed instructions here, so the model
only writes prose around facts it is given.
"""

import os
import sys

import build_matrix as bm
import emailgen
from claude import MODEL
from utils import write_dlq

WHAT_HAPPENED = "We just tried calling this person and did not get through."

OPENERS = {
    2: ('Open with exactly: "I just tried giving you a quick call. To be fair, I\'m not entirely sure '
        'you\'re the right person to speak with about this, but I noticed you recently posted a role '
        'for [job title]."'),
    3: ('Open with exactly: "I may be barking up the wrong tree here, but I just tried giving you a '
        'quick call about the [job title] role I saw posted online, and haven\'t managed to catch you."'),
}

CLOSE = "Ask whether they're the right person, and if not, whether they could point you in the right direction."

MISSED_FACT = "you also tried calling {name} and didn't get through"

# Connected contacts are referenced by their call outcome. Connected Not Interested
# contacts are OPTED_OUT and never reach here.
CONNECTED_FACTS = {
    "Connected": "you had a quick chat with {name}",
    "Connected Interested": "you spoke with {name}, who was keen to hear more",
    "Connected Call Back later": "you spoke briefly with {name}, who asked you to call back another time",
}
NOT_KDM_SUFFIX = " and {name} mentioned this probably sits with someone else"


def reference_fact(other):
    name = other["first_name"]
    if other["state"] == bm.MISSED:
        return MISSED_FACT.format(name=name)
    fact = CONNECTED_FACTS.get(other["outcome"], CONNECTED_FACTS["Connected"]).format(name=name)
    if other["state"] == bm.NOT_KDM:
        fact += NOT_KDM_SUFFIX.format(name=name)
    return fact


def reference_line(others, company):
    if not others:
        return "REFERENCE LINE: none. Do not mention anyone else at the company."
    facts = "\n".join(f"  - {reference_fact(o)}" for o in others)
    return (
        f"REFERENCE LINE: add exactly one natural sentence about who else at {company or 'the company'} "
        f"you have been trying to reach, using only these facts:\n{facts}\n"
        "  First names only, no job titles. Refer to these people by name only: no he, she, his or her. "
        "Do not list them; weave them into one sentence."
    )


def scenario_instructions(email_no, ctx, company):
    """Fixed instructions for one recipient. Pure — unit-tested."""
    if ctx["state"] != bm.MISSED:
        raise ValueError(f"route B only emails MISSED contacts, got {ctx['state']}")
    return "\n\n".join([
        "WHAT HAPPENED: " + WHAT_HAPPENED,
        "OPENER: " + OPENERS[email_no],
        reference_line(ctx["others"], company),
        "CLOSE: " + CLOSE,
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
