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
from utils import prompt_version, write_dlq

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

# The fixed wording of the call scenario, per prompt set. The default set is the
# constants above, unchanged. A prompt set without its own entry uses the default.
DEFAULT_TEXT = {
    "what_happened": WHAT_HAPPENED,
    "openers": OPENERS,
    "close": CLOSE,
    "missed_fact": MISSED_FACT,
    "connected_facts": CONNECTED_FACTS,
    "not_kdm_suffix": NOT_KDM_SUFFIX,
    "reference_none": "REFERENCE LINE: none. Do not mention anyone else at the company.",
    "reference_intro": (
        "REFERENCE LINE: add exactly one natural sentence about who else at {company} "
        "you have been trying to reach, using only these facts:\n{facts}\n"
        "  First names only, no job titles. Refer to these people by name only: no he, she, his or her. "
        "Do not list them; weave them into one sentence."
    ),
}

JP_TEXT = {
    "what_happened": WHAT_HAPPENED,
    "openers": {
        2: 'Open with exactly: "Just tried giving you a quick call and didn\'t catch you."',
        # Email 3 keeps the default opener until it is reworked
        3: OPENERS[3],
    },
    "close": 'End with exactly: "Are you the right person to chat to about this? If not, who should I be talking to?"',
    "missed_fact": "you tried {name} too and didn't get through",
    "connected_facts": {
        "Connected": "you had a quick chat with {name}",
        "Connected Interested": "you spoke with {name}, who was keen to hear more",
        "Connected Call Back later": "you spoke briefly with {name}, who asked you to call back another time",
    },
    "not_kdm_suffix": " and {name} reckons it probably sits with someone else",
    "reference_none": (
        "REFERENCE LINE: none. Straight after the opener, say in a few words that you're not sure "
        "if they're the right person for this. Do not mention anyone else at the company."
    ),
    "reference_intro": (
        "REFERENCE LINE: straight after the opener, one short sentence about who else you tried, "
        "using only these facts:\n{facts}\n"
        "  First names only, no job titles, no he, she, his or her. Do not name the company. "
        "Finish the sentence with the point that you're still not sure who the right person is."
    ),
}

ROUTE_B_TEXT = {"": DEFAULT_TEXT, "jp": JP_TEXT}


def route_b_text():
    """The fixed scenario wording for the selected prompt set."""
    return ROUTE_B_TEXT.get(prompt_version(), DEFAULT_TEXT)


def reference_fact(other, text=DEFAULT_TEXT):
    name = other["first_name"]
    if other["state"] == bm.MISSED:
        return text["missed_fact"].format(name=name)
    facts = text["connected_facts"]
    fact = facts.get(other["outcome"], facts["Connected"]).format(name=name)
    if other["state"] == bm.NOT_KDM:
        fact += text["not_kdm_suffix"].format(name=name)
    return fact


def reference_line(others, company, text=DEFAULT_TEXT):
    if not others:
        return text["reference_none"]
    facts = "\n".join(f"  - {reference_fact(o, text)}" for o in others)
    return text["reference_intro"].format(company=company or "the company", facts=facts)


def scenario_instructions(email_no, ctx, company, text=None):
    """Fixed instructions for one recipient. Pure — unit-tested. `text` defaults to the selected prompt set's."""
    if ctx["state"] != bm.MISSED:
        raise ValueError(f"route B only emails MISSED contacts, got {ctx['state']}")
    text = text or route_b_text()
    return "\n\n".join([
        "WHAT HAPPENED: " + text["what_happened"],
        "OPENER: " + text["openers"][email_no],
        reference_line(ctx["others"], company, text),
        "CLOSE: " + text["close"],
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
    print(f"Calling {MODEL} (prompt set: {prompt_version() or 'default'})...")
    try:
        emails = emailgen.generate(prompt, slots, f"raw_{cid}.txt")
        emailgen.write_contact(plan, research, cid, emails)
    except Exception as exc:
        write_dlq(plan["opportunity_id"], cid, "generate_route_b", exc, retry_count=1)
        raise


if __name__ == "__main__":
    sys.exit(main())
