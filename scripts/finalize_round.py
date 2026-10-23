"""
Finalize job — runs only after every contact's email job succeeded.

1. Clears remaining email slots for contacts who dropped out this round
2. Round 0: creates the research brief note on the Opportunity and pins it
3. Writes the Opportunity: call matrix, next task title/notes, research (round 0),
   the pin (round 0), and hpl_call_round LAST in the same update — so a failed run
   never advances the round.
"""

import json
import sys

import brief
import build_matrix as bm
import hs
from utils import is_dry_run, read_json, temp_path, write_dlq, write_json


def main():
    plan = read_json("plan.json")
    opp_id = plan["opportunity_id"]
    round_no = plan["round"]
    write_dlq(opp_id, None, "finalize_round", "Script started", retry_count=0)

    clears = {
        cid: {prop: "" for n in slots for prop in (f"hplmt_subject_{n}", f"hplmt_email_{n}")}
        for cid, slots in plan["clear_slots"].items()
    }
    # Contacts who dropped out (or the whole pool, once the sequence ends) can join another sequence
    for cid in plan["release"]:
        clears.setdefault(cid, {})["hplmt_active_opportunity_id"] = ""
    next_task = plan["next_task"]
    note_html = None
    if round_no == 0:
        research = read_json("research.json")
        # Plan ran before research existed: rebuild the task notes with the buyer frames
        next_task = {**next_task, "notes": bm.next_task_text(
            plan["matrix_after"], next_task["company_name"], next_task["job_title"],
            plan["contacts"], research["buyer_frames"])[1]}
        note_html = brief.pinned_note_html(plan["opportunity"], plan["company"], plan["contacts"],
                                           plan["matrix_after"]["order"], research)
    opp_props = {
        "hpl_call_matrix": json.dumps(plan["matrix_after"], ensure_ascii=False),
        "hpl_next_call_task_title": next_task["title"],
        "hpl_next_call_task_notes": next_task["notes"],
    }
    if round_no == 0:
        opp_props["hpl_research_payload"] = json.dumps(research, ensure_ascii=False)

    for cid, slots in plan["clear_slots"].items():
        print(f"Clear contact {cid}: slots {slots} ({plan['matrix_after']['contacts'][cid]['state']})")
    print(f"Opportunity {opp_id}: round -> {round_no}, status {plan['matrix_after']['status']}")
    print(f"Next task: {plan['next_task']['title'] or '(none)'}")

    write_json("finalize.json", {"opportunity_id": opp_id, "contact_clears": clears,
                                 "opportunity_properties": {**opp_props, "hpl_call_round": str(round_no)},
                                 "pinned_note_html": note_html, "written": not is_dry_run()})
    if is_dry_run():
        print(f"DRY RUN: nothing written. Review {temp_path('finalize.json')}")
        return

    try:
        if clears:
            hs.batch_update("contacts", clears)
        if note_html:
            note_id = hs.create_note(hs.OPPORTUNITY, opp_id, note_html)
            opp_props["hs_pinned_engagement_id"] = note_id
            print(f"Research brief note {note_id} created and pinned")
        opp_props["hpl_call_round"] = str(round_no)  # last, so a failed run never advances the round
        hs.update_object(hs.OPPORTUNITY, opp_id, opp_props)
        print("Finalize complete")
    except Exception as exc:
        write_dlq(opp_id, None, "finalize_round", exc, retry_count=1)
        raise


if __name__ == "__main__":
    sys.exit(main())
