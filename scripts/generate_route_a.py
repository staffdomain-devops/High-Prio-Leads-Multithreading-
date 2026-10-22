"""
Route A — emails that never reference calls or other contacts.

Round 0: email_1 for one contact.  Round 3: email_4 + email_5.
The confirmed KDM gets their last call outcome as soft context only.
"""

import os
import sys

import build_matrix as bm
import emailgen
from claude import MODEL
from utils import write_dlq


def kdm_context(ctx):
    if not ctx.get("is_kdm"):
        return ""
    return (
        "## CONTEXT ONLY (do not mention it)\n\n"
        "You have already spoken with this person by phone and they are the decision maker for this role. "
        f"Their latest call outcome was: {ctx['last_outcome']}. "
        "Do not reference the calls, but do not write anything that implies you have never spoken.\n\n---\n"
    )


def main():
    cid = os.environ["CONTACT_ID"]
    plan, research = emailgen.load_inputs()
    round_no = plan["round"]
    write_dlq(plan["opportunity_id"], cid, "generate_route_a", "Script started", retry_count=0)

    if round_no not in (0, 3):
        raise RuntimeError(f"route A handles rounds 0 and 3, got {round_no}")
    if emailgen.already_written(cid, round_no):
        print(f"Contact {cid}: round {round_no} email already written — skipping (re-run)")
        return

    slots = bm.ROUND_SLOTS[round_no]
    tokens = emailgen.tokens_for(plan, research, cid)
    tokens["kdm_context"] = kdm_context(plan["contexts"][cid])
    tokens["email_specs"] = "\n\n".join(emailgen.render(f"specs/email_{n}.md", tokens) for n in slots)
    tokens["output_format"] = emailgen.output_format(slots)
    prompt = emailgen.render("route_a_prompt.md", tokens)

    print(f"Route A · round {round_no} · contact {cid} ({tokens['contact.first_name']}) · emails {slots}")
    print(f"Calling {MODEL}...")
    try:
        emails = emailgen.generate(prompt, slots, f"raw_{cid}.txt")
        emailgen.write_contact(plan, research, cid, emails)
    except Exception as exc:
        write_dlq(plan["opportunity_id"], cid, "generate_route_a", exc, retry_count=1)
        raise


if __name__ == "__main__":
    sys.exit(main())
