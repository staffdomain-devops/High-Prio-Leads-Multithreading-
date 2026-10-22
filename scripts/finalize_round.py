"""
Finalize job — runs only after every contact's email job succeeded.

1. Clears remaining email slots for contacts who dropped out this round
2. Writes the Opportunity: call matrix, next task title/notes, research (round 0),
   and hpl_call_round LAST in the same update — so a failed run never advances the round.
"""

import json
import sys

import hs
from utils import is_dry_run, read_json, temp_path, write_dlq, write_json


def main():
    plan = read_json("plan.json")
    opp_id = plan["opportunity_id"]
    round_no = plan["round"]
    write_dlq(opp_id, None, "finalize_round", "Script started", retry_count=0)

    clears = {
        cid: {prop: "" for n in slots for prop in (f"subject_{n}", f"email_{n}")}
        for cid, slots in plan["clear_slots"].items()
    }
    opp_props = {
        "hpl_call_matrix": json.dumps(plan["matrix_after"], ensure_ascii=False),
        "hpl_next_call_task_title": plan["next_task"]["title"],
        "hpl_next_call_task_notes": plan["next_task"]["notes"],
    }
    if round_no == 0:
        research = read_json("research.json")
        opp_props["hpl_research_payload"] = json.dumps(research, ensure_ascii=False)
    opp_props["hpl_call_round"] = str(round_no)

    for cid, slots in plan["clear_slots"].items():
        print(f"Clear contact {cid}: slots {slots} ({plan['matrix_after']['contacts'][cid]['state']})")
    print(f"Opportunity {opp_id}: round -> {round_no}, status {plan['matrix_after']['status']}")
    print(f"Next task: {plan['next_task']['title'] or '(none)'}")

    write_json("finalize.json", {"opportunity_id": opp_id, "contact_clears": clears,
                                 "opportunity_properties": opp_props, "written": not is_dry_run()})
    if is_dry_run():
        print(f"DRY RUN: nothing written. Review {temp_path('finalize.json')}")
        return

    try:
        if clears:
            hs.batch_update("contacts", clears)
        hs.update_object(hs.OPPORTUNITY, opp_id, opp_props)
        print("Finalize complete")
    except Exception as exc:
        write_dlq(opp_id, None, "finalize_round", exc, retry_count=1)
        raise


if __name__ == "__main__":
    sys.exit(main())
