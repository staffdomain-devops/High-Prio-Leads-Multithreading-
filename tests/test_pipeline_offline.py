"""
Offline end-to-end: runs plan -> research -> route A/B -> finalize for rounds 0-3
against an in-memory HubSpot and a fake Claude. Catches wiring and prompt-token bugs.
"""

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import build_matrix as bm  # noqa: E402
import emailgen  # noqa: E402
import finalize_round  # noqa: E402
import generate_route_a  # noqa: E402
import generate_route_b  # noqa: E402
import hs  # noqa: E402
import plan_round  # noqa: E402
import research_opportunity  # noqa: E402

NO_ANSWER = "73a0d17f-1163-4015-bdd5-ec830791da20"
CONNECTED = "f240bbac-87c9-4f6e-bf70-924b57d47db7"
CALL_BACK = "d4d8824d-3110-441b-9db1-fde9d44cb784"
OPP = "9001"
A, B, C = "101", "102", "103"


class FakeHubSpot:
    def __init__(self):
        self.objects = {
            hs.OPPORTUNITY: {OPP: {"job_title___proper": "Assistant Buyer", "company_name": "City Beach",
                                   "job_description": "Manage purchase orders."}},
            "companies": {"500": {"name": "City Beach", "industry": "Retail"}},
            "contacts": {
                A: {"firstname": "Alice", "lastname": "Archer", "contact_icp_rating": "Prime (MM)", "kdm_rank": "1",
                    "email_4": "stale email from an old campaign"},
                B: {"firstname": "Ben", "lastname": "Baker", "contact_icp_rating": "Prime (MM)", "kdm_rank": "2"},
                C: {"firstname": "Cara", "lastname": "Cole", "contact_icp_rating": "Core (MM)", "kdm_rank": "1"},
            },
            "tasks": {},
            "calls": {},
        }
        self.assoc = {(hs.OPPORTUNITY, OPP, "contacts"): [A, B, C], (hs.OPPORTUNITY, OPP, "companies"): ["500"],
                      (hs.OPPORTUNITY, OPP, "tasks"): []}

    def get_object(self, t, i, props):
        return {p: self.objects[t].get(str(i), {}).get(p) for p in props}

    def batch_read(self, t, ids, props):
        return {str(i): self.get_object(t, i, props) for i in ids if str(i) in self.objects[t]}

    def associated_ids(self, f, i, t):
        return list(self.assoc.get((f, str(i), t), []))

    def update_object(self, t, i, props):
        self.objects[t].setdefault(str(i), {}).update(props)

    def batch_update(self, t, updates):
        for i, props in updates.items():
            self.update_object(t, i, props)

    def add_task(self, task_id, day):
        """Task created at 00:00 and completed at 08:00 on 2026-10-<day>."""
        self.objects["tasks"][task_id] = {"hs_createdate": f"2026-10-{day:02d}T00:00:00Z",
                                          "hs_task_completion_date": f"2026-10-{day:02d}T08:00:00Z",
                                          "hs_task_status": "COMPLETED"}
        self.assoc[(hs.OPPORTUNITY, OPP, "tasks")].append(task_id)
        return task_id

    def add_call(self, contact_id, task_id, disposition, kdm_status=None):
        task = self.objects["tasks"][task_id]
        start = plan_round.parse_ts(task["hs_createdate"])
        call_id = f"c{len(self.objects['calls'])}"
        self.objects["calls"][call_id] = {"hs_call_disposition": disposition, "hpl_call_kdm_status": kdm_status,
                                          "hs_timestamp": str(start + 3600_000)}
        self.assoc.setdefault(("contacts", contact_id, "calls"), []).append(call_id)


class FakeClaude:
    def __init__(self):
        self.prompts = {}

    def __call__(self, system, prompt, max_tokens, raw_name):
        assert "{{" not in prompt, f"unreplaced token in prompt: {re.findall(r'{{[^}]+}}', prompt)}"
        if "RESOURCE LIBRARY" in prompt:
            res = {"id": "X", "url": "https://x", "one_line": "Proof point https://x"}
            return {"company_summary": "Retailer.", "job_description_insights": "POs.",
                    "buyer_frames": {cid: f"frame {cid}" for cid in re.findall(r"Contact ID (\d+)", prompt)},
                    "resources": {k: res for k in research_opportunity.REQUIRED_RESOURCES}}
        cid = raw_name.removeprefix("raw_").removesuffix(".txt")
        self.prompts[cid] = prompt
        slots = re.findall(r'"email_(\d)": \{"subject"', prompt.split("## OUTPUT FORMAT")[1])
        return {f"email_{n}": {"subject": f"S{n}", "body": f"Body {n} for {cid}"} for n in slots}


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake_hs, fake_claude = FakeHubSpot(), FakeClaude()
    for name in ("get_object", "batch_read", "associated_ids", "update_object", "batch_update"):
        monkeypatch.setattr(hs, name, getattr(fake_hs, name))
    monkeypatch.setattr(emailgen, "generate_json", fake_claude)
    monkeypatch.setattr(research_opportunity, "generate_json", fake_claude)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("OPPORTUNITY_ID", OPP)
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    return fake_hs, fake_claude, tmp_path, monkeypatch


def run_round(env, task_id=""):
    fake_hs, fake_claude, tmp, mp = env
    fake_claude.prompts.clear()
    for f in ("research.json",):
        (tmp / f).unlink(missing_ok=True)
    mp.setenv("TASK_ID", task_id)
    try:
        plan_round.main()
    except SystemExit as e:
        assert e.code in (0, None)
        return None
    plan = json.loads((tmp / "plan.json").read_text(encoding="utf-8"))
    if plan["round"] == 0:
        research_opportunity.main()
    script = generate_route_a if plan["route"] == "A" else generate_route_b
    for cid in plan["recipients"]:
        mp.setenv("CONTACT_ID", cid)
        script.main()
    finalize_round.main()
    return plan


def contact(fake_hs, cid):
    return fake_hs.objects["contacts"][cid]


def test_full_sequence_pool_then_kdm(env):
    fake_hs, fake_claude, tmp, _ = env
    opp = fake_hs.objects[hs.OPPORTUNITY][OPP]

    # Round 0 — email_1 for everyone, stale slots wiped, research stored
    plan = run_round(env)
    assert plan["route"] == "A" and plan["recipients"] == [A, B, C]
    assert opp["hpl_call_round"] == "0"
    assert json.loads(opp["hpl_research_payload"])["buyer_frames"][B] == f"frame {B}"
    assert contact(fake_hs, A)["email_1"] == f"Body 1 for {A}"
    assert contact(fake_hs, A)["email_4"] == ""
    assert opp["hpl_next_call_task_title"].endswith("Call in order: Alice A → Ben B → Cara C")

    # Round 1 — nobody connects; each email references the other two
    t1 = fake_hs.add_task("t1", 21)
    for cid in (A, B, C):
        fake_hs.add_call(cid, t1, NO_ANSWER)
    plan = run_round(env, t1)
    assert plan["route"] == "B" and plan["recipients"] == [A, B, C]
    assert all(contact(fake_hs, cid)["email_2"] for cid in (A, B, C))
    assert "tried calling Alice" in fake_claude.prompts[B] and "tried calling Cara" in fake_claude.prompts[B]
    assert "I just tried giving you a quick call. To be fair" in fake_claude.prompts[B]
    assert opp["hpl_call_round"] == "1"

    # Round 2 — A missed, B confirmed KDM, C never called: only B gets email_3
    t2 = fake_hs.add_task("t2", 23)
    fake_hs.add_call(A, t2, NO_ANSWER)
    fake_hs.add_call(B, t2, CONNECTED, "Yes")
    plan = run_round(env, t2)
    assert plan["recipients"] == [B]
    assert "Great speaking" not in fake_claude.prompts[B]  # email 3 opener differs
    assert "Good speaking with you again earlier" in fake_claude.prompts[B]
    assert "REFERENCE LINE: none" in fake_claude.prompts[B]
    assert contact(fake_hs, B)["email_3"] == f"Body 3 for {B}"
    assert contact(fake_hs, A)["email_3"] == "" and contact(fake_hs, C)["email_5"] == ""
    assert contact(fake_hs, A)["email_2"]  # already-sent slots are left alone
    assert opp["hpl_next_call_task_title"] == "Call 3 of 3 · City Beach · Assistant Buyer · KDM: Ben B"

    # Round 3 — KDM asks for a call back: emails 4 + 5 with soft context
    t3 = fake_hs.add_task("t3", 25)
    fake_hs.add_call(B, t3, CALL_BACK)
    plan = run_round(env, t3)
    assert plan["route"] == "A" and plan["recipients"] == [B]
    assert "Connected Call Back later" in fake_claude.prompts[B]
    assert contact(fake_hs, B)["email_4"] and contact(fake_hs, B)["email_5"]
    assert opp["hpl_call_round"] == "3"
    assert opp["hpl_next_call_task_title"] == ""

    # Another completion after round 3 is a no-op
    assert run_round(env, t3) is None


def test_rerun_does_not_rewrite_sent_email(env):
    fake_hs, fake_claude, _, mp = env
    run_round(env)
    t1 = fake_hs.add_task("t1", 21)
    for cid in (A, B, C):
        fake_hs.add_call(cid, t1, NO_ANSWER)
    # Simulate a round where A's job succeeded but finalize never ran
    mp.setenv("TASK_ID", t1)
    plan_round.main()
    mp.setenv("CONTACT_ID", A)
    generate_route_b.main()
    contact(fake_hs, A)["email_2"] = "already sent"
    # Re-run the whole round
    run_round(env, t1)
    assert contact(fake_hs, A)["email_2"] == "already sent"
    assert A not in fake_claude.prompts
    assert fake_hs.objects[hs.OPPORTUNITY][OPP]["hpl_call_round"] == "1"


def test_dry_run_writes_nothing(env):
    fake_hs, _, tmp, mp = env
    mp.setenv("DRY_RUN", "true")
    run_round(env)
    assert "hpl_call_round" not in fake_hs.objects[hs.OPPORTUNITY][OPP]
    assert "email_1" not in contact(fake_hs, A)
    assert json.loads((tmp / f"email_{A}.json").read_text(encoding="utf-8"))["written"] is False
