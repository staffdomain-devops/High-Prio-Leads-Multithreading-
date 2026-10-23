"""
Opportunity brief — pure HTML builder for the note pinned on the Opportunity at round 0.

Role first (what they're hiring, the ad, why it suits offshore), then the company,
then who to call in order with each contact's buyer frame and links. Empty fields are
left out. No IO (tests/test_brief.py).
"""

from html import escape


def _link(url, label):
    return f'<a href="{escape(url, quote=True)}">{escape(label)}</a>'


def _links(pairs):
    """' · '-joined anchors for the (url, label) pairs that have a URL."""
    return " · ".join(_link(url, label) for url, label in pairs if url)


def _join(parts, sep=" · "):
    return sep.join(escape(str(p)) for p in parts if p)


def address(company):
    return ", ".join(company[k] for k in ("address", "address2", "city", "state", "zip", "country")
                     if company.get(k))


def pinned_note_html(opp, company, contacts, order, research):
    """HubSpot note body (HTML) for the Opportunity's pinned research brief."""
    job_title = opp.get("job_title___proper") or opp.get("job_title") or "Open role"
    company_name = company.get("name") or opp.get("company_name") or ""
    parts = [
        f"<p><strong>📌 {escape(job_title)}{' — ' + escape(company_name) if company_name else ''}</strong></p>",
    ]
    meta = _join([opp.get("opportunity_icp_rating"),
                  f"Score {opp['final_score']}" if opp.get("final_score") else "",
                  (opp.get("job_type") or "").capitalize() if opp.get("job_type") not in (None, "", "undefined") else "",
                  f"Job board: {opp['job_board']}" if opp.get("job_board") else ""])
    if meta:
        parts.append(f"<p>{meta}</p>")

    # The role
    parts.append("<p><strong>THE ROLE</strong></p>")
    if opp.get("job_post_link"):
        parts.append(f"<p>🔗 {_link(opp['job_post_link'], 'View job ad')}</p>")
    summary = [s for s in research.get("job_summary") or [] if s]
    if summary:
        parts.append("<ul>" + "".join(f"<li>{escape(s)}</li>" for s in summary) + "</ul>")
    if research.get("job_description_insights"):
        parts.append(f"<p><strong>Why it fits:</strong> {escape(research['job_description_insights'])}</p>")

    # The company
    parts.append("<p><strong>THE COMPANY</strong></p>")
    facts = [company.get("industry"),
             f"{company['numberofemployees']} employees" if company.get("numberofemployees") else ""]
    loc = address(company)
    line = _join(facts)
    if loc:
        line += f"{' · ' if line else ''}📍 {escape(loc)}"
    if company.get("phone"):
        line += f"{' · ' if line else ''}☎ {escape(company['phone'])}"
    if company.get("website"):
        line += f"{' · ' if line else ''}🌐 {_link(company['website'], company['website'])}"
    if line:
        parts.append(f"<p>{line}</p>")
    if research.get("company_summary"):
        parts.append(f"<p>{escape(research['company_summary'])}</p>")
    company_links = _links([
        (company.get("linkedin_company_page"), "LinkedIn page"),
        (company.get("linkedin_job_search_url"), "Other open jobs"),
        (company.get("linkedin_employees_url"), "Employees"),
        (company.get("sales_navigator_employees_url"), "Employees (Sales Nav)"),
    ])
    if company_links:
        parts.append(f"<p>🔗 {company_links}</p>")

    # Who to call
    frames = research.get("buyer_frames") or {}
    parts.append("<p><strong>WHO TO CALL (in order)</strong></p>")
    for i, cid in enumerate(order, 1):
        c = contacts.get(cid, {})
        name = f"{c.get('firstname') or ''} {c.get('lastname') or ''}".strip() or f"Contact {cid}"
        details = _join([c.get("jobtitle"), c.get("contact_icp_rating"), c.get("phone") or c.get("mobilephone")])
        links = _links([(c.get("linkedin_url"), "LinkedIn"), (c.get("sales_navigator_url"), "Sales Nav"),
                        (f"mailto:{c['email']}" if c.get("email") else "", c.get("email") or "")])
        head = f"<strong>{i}. {escape(name)}</strong>"
        tail = " · ".join(p for p in (details, links) if p)
        parts.append(f"<p>{head}{', ' + tail if tail else ''}</p>")
        if frames.get(cid):
            parts.append(f"<p>↳ {escape(frames[cid])}</p>")

    # What the emails reference, so the rep isn't surprised on the call
    res = research.get("resources") or {}
    used = _links([((res.get("case_study") or {}).get("url"), "Case study"),
                   ((res.get("youtube_video") or {}).get("url"), "Video")])
    if used:
        parts.append(f"<p><strong>WHAT THE EMAILS USE</strong></p><p>{used}</p>")

    return "".join(parts)
