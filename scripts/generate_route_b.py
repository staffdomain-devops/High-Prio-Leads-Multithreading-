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
import factcheck
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
        "  Use each person's name exactly as written above (some carry a surname because two people there "
        "share a first name), no job titles. Refer to these people by name only: no he, she, his or her. "
        "Do not list them; weave them into one sentence."
    ),
}

JP_TEXT = {
    # Facts are fixed. How they are said is the model's, within the voice guide.
    "what_happened": WHAT_HAPPENED,
    "openers": {
        2: ("Start by saying, in your own words, that you just tried calling them and didn't catch them. "
            "One sentence."),
        3: ("Start by saying, in your own words, that you tried them again and still had no luck. "
            "Shorter than last time."),
    },
    "close": {
        2: ("Finish by asking whether they're the right person for this and, if not, who you should be talking to. "
            "Your words, one or two sentences, no pressure. Pitch the register to the company size in the prospect details "
            "(see the voice guide): plainer for a small business, a bit more shape for a large one."),
        3: ("Finish with an easy out: you're happy either way, and if it's someone else's call they can point you at them. "
            "Your words, register to match the company size."),
    },
    "missed_fact": "you tried {name} too and didn't get through",
    "connected_facts": {
        "Connected": "you had a quick chat with {name}",
        "Connected Interested": "you spoke with {name}, who was keen to hear more",
        "Connected Call Back later": "you spoke briefly with {name}, who asked you to call back another time",
    },
    "not_kdm_suffix": " and {name} reckons it probably sits with someone else",
    "reference_none": {
        2: ("REFERENCE LINE: none. Do not mention anyone else at the company. "
            "You can say you're still not sure they're the right person."),
        3: "REFERENCE LINE: none. Do not mention anyone else at the company.",
    },
    "reference_intro": {
        2: ("REFERENCE LINE: straight after the opener, one short sentence about who else you tried, "
            "using only these facts and nothing more:\n{facts}\n"
            "  Names exactly as written above (a surname is there only when two people share a first name), "
            "no job titles, no he, she, his or her. Do not name the company. "
            "It's fine to add that you're still not sure who the right person is."),
        3: ("REFERENCE LINE: straight after the opener, a few words about who else you tried, "
            "using only these facts and nothing more:\n{facts}\n"
            "  As short as it can be. Names exactly as written above, no job titles, no he, she, his or her. "
            "Do not name the company."),
    },
}

ROUTE_B_TEXT = {"": DEFAULT_TEXT, "jp": JP_TEXT}


def route_b_text():
    """The fixed scenario wording for the selected prompt set."""
    return ROUTE_B_TEXT.get(prompt_version(), DEFAULT_TEXT)


def reference_fact(other, text=DEFAULT_TEXT, recipient_first_name=None):
    """One fact about another person. `name` (display name: surname added when two people in the pool
    share a first name) wins over `first_name`; someone who shares the recipient's first name is
    introduced as "your colleague Michael Tan" the first time, then by name."""
    bare = other.get("name") or other["first_name"]
    name = bm.colleague(bare, recipient_first_name)
    if other["state"] == bm.MISSED:
        return text["missed_fact"].format(name=name)
    facts = text["connected_facts"]
    fact = facts.get(other["outcome"], facts["Connected"]).format(name=name)
    if other["state"] == bm.NOT_KDM:
        fact += text["not_kdm_suffix"].format(name=bare)
    return fact


def _for_email(entry, email_no):
    """A text entry is either one string for every email, or a dict keyed by email number."""
    return entry[email_no] if isinstance(entry, dict) else entry


def reference_line(others, company, text=DEFAULT_TEXT, email_no=2, recipient_first_name=None):
    if not others:
        return _for_email(text["reference_none"], email_no)
    facts = "\n".join(f"  - {reference_fact(o, text, recipient_first_name)}" for o in others)
    return _for_email(text["reference_intro"], email_no).format(company=company or "the company", facts=facts)


def scenario_instructions(email_no, ctx, company, text=None):
    """Fixed instructions for one recipient. Pure — unit-tested. `text` defaults to the selected prompt set's."""
    if ctx["state"] != bm.MISSED:
        raise ValueError(f"route B only emails MISSED contacts, got {ctx['state']}")
    text = text or route_b_text()
    return "\n\n".join([
        "WHAT HAPPENED: " + text["what_happened"],
        "OPENER: " + text["openers"][email_no],
        reference_line(ctx["others"], company, text, email_no, ctx.get("recipient_first_name")),
        "CLOSE: " + _for_email(text["close"], email_no),
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
        # fact-checked; one revision is requested before an email is refused
        emails = emailgen.generate_checked(plan, cid, prompt, slots, f"raw_{cid}.txt")
        emailgen.write_contact(plan, research, cid, emails)
    except Exception as exc:
        write_dlq(plan["opportunity_id"], cid, "generate_route_b", exc, retry_count=1)
        raise


if __name__ == "__main__":
    sys.exit(main())
