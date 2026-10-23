import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import brief  # noqa: E402

OPP = {"job_title___proper": "Assistant Buyer", "job_post_link": "https://example.com/jobs/1",
       "opportunity_icp_rating": "Prime (SMB)", "final_score": "80", "job_type": "full time", "job_board": "seek"}
COMPANY = {"name": "Coastal Threads", "industry": "RETAIL", "numberofemployees": "40",
           "address": "9222 Amber Dr", "city": "Brisbane", "state": "QLD", "country": "AU",
           "website": "https://coastalthreads.com", "linkedin_company_page": "https://linkedin.com/company/ct",
           "linkedin_job_search_url": "https://linkedin.com/jobs?f_C=1"}
CONTACTS = {
    "101": {"firstname": "Alice", "lastname": "Archer", "jobtitle": "CIO", "contact_icp_rating": "Prime (MM)",
            "phone": "+61 400 000 001", "email": "alice@ct.com", "linkedin_url": "https://linkedin.com/in/alice"},
    "102": {"firstname": "Ben", "lastname": "Baker"},
}
RESEARCH = {
    "company_summary": "A 40-store fashion retailer.",
    "job_summary": ["Raise purchase orders across 150+ suppliers", "1-2 years in retail buying"],
    "job_description_insights": "High-volume PO admin.",
    "buyer_frames": {"101": "Cares about data security.", "102": "Wants PO support."},
    "resources": {"case_study": {"url": "https://sd.com/cs"}, "youtube_video": {"url": "https://yt.com/v"}},
}


def note(**overrides):
    args = {"opp": OPP, "company": COMPANY, "contacts": CONTACTS, "order": ["101", "102"], "research": RESEARCH}
    args.update(overrides)
    return brief.pinned_note_html(**args)


def test_role_comes_first_then_company_then_contacts():
    html = note()
    assert html.index("THE ROLE") < html.index("THE COMPANY") < html.index("WHO TO CALL")
    assert "<strong>📌 Assistant Buyer — Coastal Threads</strong>" in html
    assert "Prime (SMB) · Score 80 · Full time · Job board: seek" in html


def test_role_section_has_ad_link_summary_and_insights():
    html = note()
    assert '<a href="https://example.com/jobs/1">View job ad</a>' in html
    assert "<li>Raise purchase orders across 150+ suppliers</li><li>1-2 years in retail buying</li>" in html
    assert "Why it fits:</strong> High-volume PO admin." in html


def test_company_section_has_address_summary_and_links():
    html = note()
    assert "📍 9222 Amber Dr, Brisbane, QLD, AU" in html
    assert "A 40-store fashion retailer." in html
    assert '<a href="https://linkedin.com/jobs?f_C=1">Other open jobs</a>' in html
    assert "Employees (Sales Nav)" not in html  # empty links are left out


def test_contacts_in_call_order_with_frames_and_links():
    html = note()
    assert html.index("1. Alice Archer") < html.index("Cares about data security.") < html.index("2. Ben Baker")
    assert "CIO · Prime (MM) · +61 400 000 001" in html
    assert '<a href="https://linkedin.com/in/alice">LinkedIn</a>' in html
    assert '<a href="mailto:alice@ct.com">alice@ct.com</a>' in html
    assert "<strong>2. Ben Baker</strong></p>" in html  # no details, no dangling separator


def test_values_are_html_escaped():
    html = note(company={**COMPANY, "name": "Smith & Sons <Pty>"})
    assert "Smith &amp; Sons &lt;Pty&gt;" in html


def test_missing_optional_fields_dont_break_the_note():
    html = note(opp={"job_title": "Bookkeeper", "job_type": "undefined"}, company={}, contacts={},
                research={"buyer_frames": {}})
    assert "<strong>📌 Bookkeeper</strong>" in html
    assert "undefined" not in html and "WHAT THE EMAILS USE" not in html
    assert "1. Contact 101" in html
