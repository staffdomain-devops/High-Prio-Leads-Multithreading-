"""
Plan job — read-only. Works out which round this is, applies the round's calls
to the call matrix, and writes plan.json for the route + finalize jobs.

Round = hpl_call_round + 1 (empty -> round 0). Round 1-3 calls are the latest
call per contact inside the task's created -> completed window.

Outputs (GITHUB_OUTPUT): skip, round, route (A|B|none), contacts (JSON list).
"""

import json
import os
import sys
import time
import traceback

import build_matrix as bm
import hs
from utils import parse_ts, set_output, write_dlq, write_json

OPP_PROPS = [
    "job_title", "job_title___proper", "job_description", "job_post_link", "job_board",
    "vertical", "company_name", "final_score",
    "hpl_call_round", "hpl_call_matrix", "hpl_research_payload",
]
CONTACT_PROPS = [
    "firstname", "lastname", "email", "jobtitle", "phone", "mobilephone",
    "contact_icp_rating", "kdm_rank",
]
COMPANY_PROPS = ["name", "industry", "numberofemployees", "city", "country", "website", "description"]
TASK_PROPS = ["hs_createdate", "hs_task_completion_date", "hs_task_status", "hs_task_subject"]
CALL_PROPS = ["hs_call_disposition", "hpl_call_kdm_status", "hs_timestamp", "hs_createdate"]


def skip(reason):
    print(f"SKIP: {reason}")
    set_output("skip", "true")
    set_output("route", "none")
    set_output("contacts", "[]")
    sys.exit(0)


def current_round(opp):
    value = opp.get("hpl_call_round")
    return 0 if value in (None, "") else int(float(value)) + 1


def resolve_task(opp_id, task_id):
    if task_id:
        return task_id, hs.get_object("tasks", task_id, TASK_PROPS)
    tasks = hs.batch_read("tasks", hs.associated_ids(hs.OPPORTUNITY, opp_id, "tasks"), TASK_PROPS)
    completed = {k: v for k, v in tasks.items() if v.get("hs_task_status") == "COMPLETED"}
    if not completed:
        raise RuntimeError(f"no task_id given and opportunity {opp_id} has no completed tasks")
    latest = max(completed, key=lambda k: parse_ts(completed[k].get("hs_task_completion_date")) or 0)
    print(f"No task_id given — using latest completed task {latest}")
    return latest, completed[latest]


def fetch_calls(contact_id):
    call_ids = hs.associated_ids("contacts", contact_id, "calls")
    calls = hs.batch_read("calls", call_ids, CALL_PROPS) if call_ids else {}
    return [
        {
            "id": cid,
            "timestamp": parse_ts(p.get("hs_timestamp")) or parse_ts(p.get("hs_createdate")) or 0,
            "disposition": p.get("hs_call_disposition"),
            "kdm_status": p.get("hpl_call_kdm_status"),
        }
        for cid, p in calls.items()
    ]


def main():
    opp_id = os.environ["OPPORTUNITY_ID"].strip()
    task_id = (os.environ.get("TASK_ID") or "").strip()
    if not opp_id.isdigit():
        print(f"ERROR: opportunity_id is invalid: {opp_id!r}", file=sys.stderr)
        sys.exit(1)

    write_dlq(opp_id, None, "plan_round", "Script started", retry_count=0)

    try:
        opp = hs.get_object(hs.OPPORTUNITY, opp_id, OPP_PROPS)
        before = json.loads(opp["hpl_call_matrix"]) if opp.get("hpl_call_matrix") else None
        round_no = current_round(opp)
        print(f"Opportunity {opp_id}: {opp.get('job_title___proper') or opp.get('job_title')} | round {round_no}")

        if before and before["status"] == bm.STATUS_CLOSED_LOST:
            skip("opportunity is closed_lost")
        if round_no > bm.TOTAL_ROUNDS:
            skip("sequence already complete (round 3 processed)")

        task = None
        if round_no == 0:
            contact_ids = hs.associated_ids(hs.OPPORTUNITY, opp_id, "contacts")
            if not contact_ids:
                raise RuntimeError(f"opportunity {opp_id} has no associated contacts")
            contacts = hs.batch_read("contacts", contact_ids, CONTACT_PROPS)
            after = bm.new_matrix(contacts)
        else:
            if not before:
                raise RuntimeError(f"hpl_call_round is set but hpl_call_matrix is empty on {opp_id}")
            task_id, task_props = resolve_task(opp_id, task_id)
            if task_props.get("hs_task_status") != "COMPLETED":
                skip(f"task {task_id} is not completed (status {task_props.get('hs_task_status')})")
            window = (
                parse_ts(task_props.get("hs_createdate")),
                parse_ts(task_props.get("hs_task_completion_date")) or int(time.time() * 1000),
            )
            task = {"id": task_id, "subject": task_props.get("hs_task_subject"),
                    "window_start": window[0], "window_end": window[1]}
            print(f"Task {task_id}: window {window[0]} -> {window[1]}")

            contacts = hs.batch_read("contacts", before["order"], CONTACT_PROPS)
            calls_by_contact = {cid: fetch_calls(cid) for cid in bm.recipients(before)}
            after = bm.advance(before, round_no, calls_by_contact, window)

        company_ids = hs.associated_ids(hs.OPPORTUNITY, opp_id, "companies")
        company = hs.get_object("companies", company_ids[0], COMPANY_PROPS) if company_ids else {}

        recipients = bm.recipients(after)
        route = bm.ROUND_ROUTE[round_no] if recipients else "none"
        context_fn = bm.route_b_context if route == "B" else bm.route_a_context
        company_name = company.get("name") or opp.get("company_name") or ""
        job_title = opp.get("job_title___proper") or opp.get("job_title") or ""
        title, notes = bm.next_task_text(after, company_name, job_title, contacts)

        plan = {
            "opportunity_id": opp_id,
            "round": round_no,
            "route": route,
            "task": task,
            "opportunity": opp,
            "company": company,
            "contacts": contacts,
            "matrix_before": before,
            "matrix_after": after,
            "recipients": recipients,
            "contexts": {cid: context_fn(after, cid) for cid in recipients},
            "clear_slots": bm.slots_to_clear(before, after) if before else {},
            "next_task": {"title": title, "notes": notes},
            "research": json.loads(opp["hpl_research_payload"]) if opp.get("hpl_research_payload") else None,
        }
        write_json("plan.json", plan)

        for cid in after["order"]:
            c = after["contacts"][cid]
            print(f"  {c['first_name']} {c['last_name']} ({cid}): {c['state']}")
        print(f"Status: {after['status']} | route {route} | recipients {recipients}")
        print(f"Next task: {title or '(none)'}")

        set_output("skip", "false")
        set_output("round", str(round_no))
        set_output("route", route)
        set_output("contacts", json.dumps(recipients))

    except SystemExit:
        raise
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        write_dlq(opp_id, None, "plan_round", exc, retry_count=1)
        raise


if __name__ == "__main__":
    main()
