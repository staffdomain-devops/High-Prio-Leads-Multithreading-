"""
Shared plumbing for the two email routes: inputs, prompt rendering, Claude call,
and the per-contact HubSpot write.

A contact's slots for this round are only written when empty (round 0 wipes 2-5,
finalize clears dropped contacts), so re-running a partly failed round never
rewrites — and re-sends — an email that already went out.
"""

import os
from datetime import datetime, timezone

import build_matrix as bm
import factcheck
import hs
from claude import generate_json
from utils import (clean_text, is_dry_run, prompt_version, prompts_dir, read_json, safe_truncate,
                   substitute_tokens, temp_path, write_json)
MAX_TOKENS = 16000  # Sonnet 5.5 thinks by default and thinking counts against max_tokens; 4096 left too little room for the JSON

SYSTEM = (
    "You are writing personalised sales emails in strict Australian English. "
    "NEVER use em dashes (—), en dashes (–), or hyphens as sentence separators. "
    "Use commas, colons, or full stops instead. Compound adjectives (no-lock-in, all-in) are fine. "
    "Never use the words 'offshoring' or 'outsourcing' in emails 1, 2, or 3. "
    "Return only the raw JSON object — no markdown, no code blocks."
)


def load_inputs():
    plan = read_json("plan.json")
    research = read_json("research.json") if os.path.exists(temp_path("research.json")) else plan["research"]
    if not research:
        raise RuntimeError("no research payload (research.json missing and hpl_research_payload empty)")
    return plan, research


def role_location(opp, company):
    """Where the role is based: the ad's city/state first, else the CRM company's. '' when unknown."""
    for src in (opp, company):
        city = (src.get("job_post_city") or src.get("city") or "").strip()
        state = (src.get("job_post_state") or src.get("state") or "").strip()
        if city:
            return city
        if state:
            return state
    return ""


def tokens_for(plan, research, cid):
    contact = plan["contacts"].get(cid, {})
    opp = plan["opportunity"]
    company = plan["company"]
    resources = research.get("resources", {})
    tokens = {
        "contact.first_name": contact.get("firstname", "") or "",
        "contact.last_name": contact.get("lastname", "") or "",
        "contact.jobtitle": contact.get("jobtitle", "") or "",
        "contact.company": company.get("name", "") or opp.get("company_name", "") or "",
        "contact.industry": company.get("industry", "") or opp.get("vertical", "") or "",
        "contact.website": company.get("website", "") or "",
        "company.size": str(company.get("numberofemployees") or "").strip(),
        # Email 1 opener: "not sure if it's you, Ben or Cara I should be asking"
        "contact.who_clause": bm.who_clause((plan.get("contexts", {}).get(cid) or {}).get("others", [])),
        # "...the Assistant Buyer role you've got up in Brisbane" — empty clause when the location is unknown
        "opportunity.location": role_location(opp, company),
        "opportunity.location_clause": f" in {role_location(opp, company)}" if role_location(opp, company) else "",
        "opportunity.job_title": opp.get("job_title___proper") or opp.get("job_title", "") or "",
        "opportunity.job_post_link": opp.get("job_post_link", "") or "",
        "opportunity.job_description": opp.get("job_description", "") or "",
        "research.company_summary": research.get("company_summary", ""),
        "research.job_description_insights": research.get("job_description_insights", ""),
        "research.buyer_frame": (research.get("buyer_frames") or {}).get(cid, ""),
        # One approved line per contact, chosen by the research agent from the prompt set's selling points
        "research.selling_point": ((research.get("selling_points") or {}).get(cid) or {}).get("line", ""),
        "resource.case_study_one_line": resources.get("case_study", {}).get("one_line", ""),
        "resource.youtube_one_line": resources.get("youtube_video", {}).get("one_line", ""),
        "resource.wr2_one_line": resources.get("website_resource_email2", {}).get("one_line", ""),
        "resource.wr5_one_line": resources.get("website_resource_email5", {}).get("one_line", ""),
    }
    # A prompt set may carry a voice guide (prompts/<set>/voice.md); the base prompt pulls it in via {{voice}}
    voice_path = prompts_dir() / "voice.md"
    tokens["voice"] = voice_path.read_text(encoding="utf-8").strip() if voice_path.exists() else ""
    tokens["base"] = render("base.md", tokens)
    return tokens


def render(name, tokens):
    return substitute_tokens((prompts_dir() / name).read_text(encoding="utf-8"), tokens)


def output_format(slots):
    lines = [f'  "email_{n}": {{"subject": "...", "body": "..."}}' for n in slots]
    return "{\n" + ",\n".join(lines) + "\n}"


def slot_props(n):
    """This pipeline's own contact fields — never shared with High-Prio-Leads' subject_N/email_N."""
    return f"hplmt_subject_{n}", f"hplmt_email_{n}"


def already_written(cid, round_no):
    """True when this round's first slot already holds an email (a re-run)."""
    if round_no == 0:
        return False
    _, body_prop = slot_props(bm.ROUND_SLOTS[round_no][0])
    return bool(hs.get_object("contacts", cid, [body_prop]).get(body_prop))


def generate(prompt, slots, raw_name):
    output = generate_json(SYSTEM, prompt, MAX_TOKENS, raw_name)
    emails = {}
    for n in slots:
        email = output.get(f"email_{n}")
        if not isinstance(email, dict) or not email.get("body"):
            raise RuntimeError(f"Claude response missing email_{n}")
        emails[n] = {"subject": clean_text(email.get("subject", "")), "body": clean_text(email["body"])}
    return emails


def required_names(plan, cid):
    """First names email 1 must carry. Only when the prompt set's email 1 spec uses the who-clause
    (prompts/jp does, prompts/ doesn't), and only the names who_clause itself would print."""
    spec = prompts_dir() / "specs" / "email_1.md"
    if plan.get("round") != 0 or not spec.exists() or "{{contact.who_clause}}" not in spec.read_text(encoding="utf-8"):
        return []
    others = ((plan.get("contexts") or {}).get(cid) or {}).get("others") or []
    return [n for n in others if n and not isinstance(n, dict)][:bm.MAX_NAMED_OTHERS]


def fact_check(plan, cid, emails, first_attempt_problems=None):
    """Stop an email that misstates the call facts before it reaches HubSpot. The rejected text is kept
    in rejected_<cid>.json for review; the raised error lands in the dead-letter record."""
    try:
        factcheck.check_emails(plan, cid, emails, required_names(plan, cid))
    except factcheck.FactCheckError as exc:
        record = {"contact_id": cid, "round": plan["round"], "emails": emails, "problems": str(exc).split("\n")}
        if first_attempt_problems:
            record["first_attempt_problems"] = first_attempt_problems
        write_json(f"rejected_{cid}.json", record)
        print(f"FACT CHECK FAILED for contact {cid}:\n{exc}")
        raise


REVISION = (
    "\n\n---\n\n## REVISION REQUIRED\n\n"
    "Your previous draft was refused by a fact check for these reasons:\n{problems}\n\n"
    "Write it again and fix every one of them. Everything else above still applies. "
    "Return the same JSON structure, nothing else."
)


def generate_checked(plan, cid, prompt, slots, raw_name):
    """Generate, fact-check, and on a failure give the model one second attempt with the reasons.
    A second failure is refused for good (rejected_<cid>.json, raised)."""
    emails = generate(prompt, slots, raw_name)
    try:
        factcheck.check_emails(plan, cid, emails, required_names(plan, cid))
        return emails
    except factcheck.FactCheckError as exc:
        problems = str(exc).split("\n")
        print(f"Fact check failed for contact {cid}, asking for a revision:\n{exc}")
    retry_prompt = prompt + REVISION.format(problems="\n".join(f"- {p}" for p in problems))
    emails = generate(retry_prompt, slots, raw_name.replace(".txt", "_retry.txt"))
    fact_check(plan, cid, emails, first_attempt_problems=problems)
    return emails


def write_contact(plan, research, cid, emails):
    round_no = plan["round"]
    properties = {}
    for n, email in emails.items():
        subj_prop, body_prop = slot_props(n)
        properties[subj_prop] = safe_truncate(email["subject"], 1024)
        properties[body_prop] = safe_truncate(email["body"].replace("\n", "<br>"), 65000)

    if round_no == 0:
        # Wipe 2-5 so "email_N is known" always means "written by this sequence"
        for n in range(2, 6):
            subj_prop, body_prop = slot_props(n)
            properties[subj_prop] = ""
            properties[body_prop] = ""
        summary = [
            f"Company: {research.get('company_summary', '')}",
            f"Job insights: {research.get('job_description_insights', '')}",
            f"Buyer frame: {(research.get('buyer_frames') or {}).get(cid, '')}",
        ]
        properties["hplmt_research_summary"] = safe_truncate("\n\n".join(summary), 65000)
        # Lock the contact to this Opportunity until they drop out or the sequence ends
        properties["hplmt_active_opportunity_id"] = plan["opportunity_id"]
    properties["hplmt_generated_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    record = {"contact_id": cid, "round": round_no, "prompt_version": prompt_version() or "default",
              "emails": emails, "properties": properties, "written": not is_dry_run()}
    write_json(f"email_{cid}.json", record)

    if is_dry_run():
        print(f"DRY RUN: would write {len(properties)} properties to contact {cid}")
        return
    hs.update_object("contacts", cid, properties)
    print(f"Contact {cid} updated: {sorted(properties)}")
