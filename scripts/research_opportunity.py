"""
Research Agent — round 0 only, once per Opportunity.

Reads plan.json, calls Claude once to summarise the company + job, select the 4
shared resources, and write a buyer frame per contact. Writes research.json;
finalize_round.py stores it on the Opportunity as hpl_research_payload so
rounds 1-3 reuse it.
"""

import os
import sys
from datetime import date
from pathlib import Path

from claude import generate_json, MODEL
from utils import read_json, substitute_tokens, write_dlq, write_json

RESEARCH_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "research_prompt.md"
MAX_TOKENS = 4096

SYSTEM = (
    "You are a research analyst for an offshore staffing company. "
    "Your job is to analyse a company and its job posting, then select the most relevant "
    "social proof assets from a fixed resource library. "
    "Return only the raw JSON object — no markdown, no code blocks, no explanation."
)

REQUIRED_RESOURCES = ["case_study", "youtube_video", "website_resource_email2", "website_resource_email5"]


def validate(payload, contact_ids):
    missing = [k for k in ["company_summary", "job_description_insights", "buyer_frames", "resources"]
               if k not in payload]
    if missing:
        return f"missing top-level keys: {missing}"
    for slot in REQUIRED_RESOURCES:
        res = payload["resources"].get(slot) or {}
        if not all(res.get(f) for f in ("id", "url", "one_line")):
            return f"resource '{slot}' incomplete"
    no_frame = [c for c in contact_ids if not payload["buyer_frames"].get(c)]
    if no_frame:
        return f"no buyer frame for contacts {no_frame}"
    return None


def contacts_list(plan):
    lines = []
    for cid in plan["matrix_after"]["order"]:
        p = plan["contacts"].get(cid, {})
        name = f"{p.get('firstname') or ''} {p.get('lastname') or ''}".strip() or "(no name)"
        lines.append(f"- Contact ID {cid}: {name}, {p.get('jobtitle') or 'title unknown'}")
    return "\n".join(lines)


def main():
    plan = read_json("plan.json")
    opp_id = plan["opportunity_id"]
    write_dlq(opp_id, None, "research_opportunity", "Script started", retry_count=0)

    opp = plan["opportunity"]
    company = plan["company"]
    tokens = {
        "contacts.list": contacts_list(plan),
        "opportunity.job_title": opp.get("job_title___proper") or opp.get("job_title", ""),
        "opportunity.job_post_link": opp.get("job_post_link", ""),
        "opportunity.job_description": opp.get("job_description", ""),
        "opportunity.job_board": opp.get("job_board", ""),
        "opportunity.vertical": opp.get("vertical", ""),
        "opportunity.final_score": opp.get("final_score", ""),
        "company.name": company.get("name", "") or opp.get("company_name", ""),
        "company.industry": company.get("industry", ""),
        "company.numberofemployees": company.get("numberofemployees", ""),
        "company.city": company.get("city", ""),
        "company.country": company.get("country", ""),
        "company.website": company.get("website", ""),
        "company.description": company.get("description", ""),
        "campaign.current_date": date.today().isoformat(),
    }
    prompt = substitute_tokens(RESEARCH_PROMPT_PATH.read_text(encoding="utf-8"), tokens)
    print(f"Research prompt: {len(prompt):,} chars | {len(plan['matrix_after']['order'])} contacts")
    print(f"Calling {MODEL} for research...")

    try:
        payload = generate_json(SYSTEM, prompt, MAX_TOKENS, "research_raw.txt")
        problem = validate(payload, plan["matrix_after"]["order"])
        if problem:
            raise RuntimeError(f"research payload invalid: {problem}")
        for slot in REQUIRED_RESOURCES:
            res = payload["resources"][slot]
            print(f"  {slot}: {res['id']} — {res['one_line'][:80]}")
        write_json("research.json", payload)
        print("research.json written")
    except Exception as exc:
        write_dlq(opp_id, None, "research_opportunity", exc, retry_count=1)
        raise


if __name__ == "__main__":
    sys.exit(main())
