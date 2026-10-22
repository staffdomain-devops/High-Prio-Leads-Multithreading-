"""
Shared plumbing for the two email routes: inputs, prompt rendering, Claude call,
and the per-contact HubSpot write.

A contact's slots for this round are only written when empty (round 0 wipes 2-5,
finalize clears dropped contacts), so re-running a partly failed round never
rewrites — and re-sends — an email that already went out.
"""

import os
from datetime import datetime, timezone
from pathlib import Path

import build_matrix as bm
import hs
from claude import generate_json
from utils import clean_text, is_dry_run, read_json, safe_truncate, substitute_tokens, temp_path, write_json

PROMPTS = Path(__file__).parent.parent / "prompts"
MAX_TOKENS = 4096

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
        "opportunity.job_title": opp.get("job_title___proper") or opp.get("job_title", "") or "",
        "opportunity.job_post_link": opp.get("job_post_link", "") or "",
        "opportunity.job_description": opp.get("job_description", "") or "",
        "research.company_summary": research.get("company_summary", ""),
        "research.job_description_insights": research.get("job_description_insights", ""),
        "research.buyer_frame": (research.get("buyer_frames") or {}).get(cid, ""),
        "resource.case_study_one_line": resources.get("case_study", {}).get("one_line", ""),
        "resource.youtube_one_line": resources.get("youtube_video", {}).get("one_line", ""),
        "resource.wr2_one_line": resources.get("website_resource_email2", {}).get("one_line", ""),
        "resource.wr5_one_line": resources.get("website_resource_email5", {}).get("one_line", ""),
    }
    tokens["base"] = render("base.md", tokens)
    return tokens


def render(name, tokens):
    return substitute_tokens((PROMPTS / name).read_text(encoding="utf-8"), tokens)


def output_format(slots):
    lines = [f'  "email_{n}": {{"subject": "...", "body": "..."}}' for n in slots]
    return "{\n" + ",\n".join(lines) + "\n}"


def slot_props(n):
    return f"subject_{n}", f"email_{n}"


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
        properties["hpl_research_summary"] = safe_truncate("\n\n".join(summary), 65000)
    properties["hpl_generated_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    record = {"contact_id": cid, "round": round_no, "emails": emails, "properties": properties,
              "written": not is_dry_run()}
    write_json(f"email_{cid}.json", record)

    if is_dry_run():
        print(f"DRY RUN: would write {len(properties)} properties to contact {cid}")
        return
    hs.update_object("contacts", cid, properties)
    print(f"Contact {cid} updated: {sorted(properties)}")
