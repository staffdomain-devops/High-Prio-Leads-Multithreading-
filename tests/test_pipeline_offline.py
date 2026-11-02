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
import factcheck  # noqa: E402
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
            "companies": {"500": {"name": "City Beach", "industry": "Retail", "city": "Brisbane", "state": "QLD", "numberofemployees": "4"}},
            "contacts": {
                A: {"firstname": "Alice", "lastname": "Archer", "contact_icp_rating": "Prime (MM)", "kdm_rank": "1",
                    "hplmt_email_4": "stale email from an old campaign"},
                B: {"firstname": "Ben", "lastname": "Baker", "contact_icp_rating": "Prime (MM)", "kdm_rank": "2"},
                C: {"firstname": "Cara", "lastname": "Cole", "contact_icp_rating": "Core (MM)", "kdm_rank": "1"},
            },
            "tasks": {},
            "calls": {},
        }
        self.assoc = {(hs.OPPORTUNITY, OPP, "contacts"): [A, B, C], (hs.OPPORTUNITY, OPP, "companies"): ["500"],
                      (hs.OPPORTUNITY, OPP, "tasks"): []}
        self.notes = {}

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

    def create_note(self, t, i, body_html):
        note_id = f"n{len(self.notes)}"
        self.notes[note_id] = {"object": (t, str(i)), "body": body_html}
        return note_id

    def add_task(self, task_id, day, subject="Call 1 of 3 · City Beach · Assistant Buyer", task_type="CALL"):
        """Task created at 00:00 and completed at 08:00 on 2026-10-<day>."""
        self.objects["tasks"][task_id] = {"hs_createdate": f"2026-10-{day:02d}T00:00:00Z",
                                          "hs_task_completion_date": f"2026-10-{day:02d}T08:00:00Z",
                                          "hs_task_status": "COMPLETED", "hs_task_type": task_type,
                                          "hs_task_subject": subject}
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
        self.canned = {}  # contact id -> body to return, for tests that need the model to say something specific

    def __call__(self, system, prompt, max_tokens, raw_name):
        assert "{{" not in prompt, f"unreplaced token in prompt: {re.findall(r'{{[^}]+}}', prompt)}"
        if "RESOURCE LIBRARY" in prompt:
            res = {"id": "X", "url": "https://x", "one_line": "Proof point https://x"}
            cids = re.findall(r"Contact ID (\d+)", prompt)
            return {"company_summary": "Retailer.", "job_summary": ["Raise purchase orders"],
                    "job_description_insights": "POs.",
                    "buyer_frames": {cid: f"frame {cid}" for cid in cids},
                    "selling_points": {cid: {"section": "Risk reversal", "line": f"point {cid}"} for cid in cids},
                    "resources": {k: res for k in research_opportunity.REQUIRED_RESOURCES}}
        cid = raw_name.removeprefix("raw_").removesuffix(".txt")
        self.prompts[cid] = prompt
        slots = re.findall(r'"email_(\d)": \{"subject"', prompt.split("## OUTPUT FORMAT")[1])
        base_cid = cid.removesuffix("_retry")
        canned = self.canned.get(base_cid)
        if isinstance(canned, list):                      # one body per attempt
            canned = canned.pop(0) if canned else None
        # a JP email 1 opens with the fixed who-clause sentence; the fake does what a compliant model does
        m = re.search(r"Not sure if it's (you(?:, \w+)*(?: or [^\n.]+?)?) I should be asking, but I saw the \.\.\.", prompt)
        opener = f"Not sure if it's {m.group(1)} I should be asking, but I saw the role. " if m else ""
        return {f"email_{n}": {"subject": f"S{n}", "body": canned or f"{opener}Body {n} for {base_cid}"} for n in slots}


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake_hs, fake_claude = FakeHubSpot(), FakeClaude()
    for name in ("get_object", "batch_read", "associated_ids", "update_object", "batch_update", "create_note"):
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
    env[3].setenv("PROMPT_VERSION", "default")  # asserts the original set's wording
    fake_hs, fake_claude, tmp, _ = env
    opp = fake_hs.objects[hs.OPPORTUNITY][OPP]

    # Round 0 — email_1 for everyone, stale slots wiped, research stored
    plan = run_round(env)
    assert plan["route"] == "A" and plan["recipients"] == [A, B, C]
    assert opp["hpl_call_round"] == "0"
    assert json.loads(opp["hpl_research_payload"])["buyer_frames"][B] == f"frame {B}"
    assert contact(fake_hs, A)["hplmt_email_1"] == f"Body 1 for {A}"
    assert contact(fake_hs, A)["hplmt_email_4"] == ""
    assert opp["hpl_next_call_task_title"].endswith("Call in order: Alice A → Ben B → Cara C")
    # Research brief pinned on the Opportunity; buyer frames in the first task's notes
    assert list(fake_hs.notes) == [opp["hs_pinned_engagement_id"]]
    note = fake_hs.notes[opp["hs_pinned_engagement_id"]]
    assert note["object"] == (hs.OPPORTUNITY, OPP)
    assert "<li>Raise purchase orders</li>" in note["body"] and f"frame {B}" in note["body"]
    assert f"Angle: frame {A}" in opp["hpl_next_call_task_notes"]

    # Round 1 — nobody connects; each email references the other two
    t1 = fake_hs.add_task("t1", 21)
    for cid in (A, B, C):
        fake_hs.add_call(cid, t1, NO_ANSWER)
    plan = run_round(env, t1)
    assert plan["route"] == "B" and plan["recipients"] == [A, B, C]
    assert all(contact(fake_hs, cid)["hplmt_email_2"] for cid in (A, B, C))
    assert "tried calling Alice" in fake_claude.prompts[B] and "tried calling Cara" in fake_claude.prompts[B]
    assert "I just tried giving you a quick call. To be fair" in fake_claude.prompts[B]
    assert opp["hpl_call_round"] == "1"

    # Round 2 — A connects (not the KDM) and stops; B and C get email_3 mentioning A
    t2 = fake_hs.add_task("t2", 23)
    fake_hs.add_call(A, t2, CONNECTED, "No")
    fake_hs.add_call(B, t2, NO_ANSWER)
    fake_hs.add_call(C, t2, NO_ANSWER)
    plan = run_round(env, t2)
    assert plan["recipients"] == [B, C]
    assert A not in fake_claude.prompts
    assert "Alice mentioned this probably sits with someone else" in fake_claude.prompts[B]
    assert "barking up the wrong tree" in fake_claude.prompts[B]
    assert contact(fake_hs, B)["hplmt_email_3"] == f"Body 3 for {B}"
    assert contact(fake_hs, A)["hplmt_email_3"] == "" and contact(fake_hs, A)["hplmt_email_5"] == ""
    assert contact(fake_hs, A)["hplmt_email_2"]  # already-sent slots are left alone
    assert opp["hpl_next_call_task_title"] == "Call 3 of 3 · City Beach · Assistant Buyer · Call in order: Ben B → Cara C"
    assert f"Angle: frame {B}" in opp["hpl_next_call_task_notes"]  # later rounds reuse the stored frames
    assert f"frame {A}" not in opp["hpl_next_call_task_notes"]
    assert len(fake_hs.notes) == 1  # the brief is only written at round 0

    # Round 3 — C is reached and is the KDM: nobody gets emails 4 + 5
    t3 = fake_hs.add_task("t3", 25)
    fake_hs.add_call(B, t3, NO_ANSWER)
    fake_hs.add_call(C, t3, CALL_BACK, "Yes")
    plan = run_round(env, t3)
    assert plan["route"] == "none" and plan["recipients"] == []
    assert not fake_claude.prompts
    assert all(contact(fake_hs, cid).get("hplmt_email_4") in (None, "") for cid in (A, B, C))
    assert contact(fake_hs, B)["hplmt_email_3"]  # sent emails stay
    assert json.loads(opp["hpl_call_matrix"])["status"] == "kdm_found"
    assert opp["hpl_call_round"] == "3"
    assert opp["hpl_next_call_task_title"] == ""
    assert all(contact(fake_hs, cid)["hplmt_active_opportunity_id"] == "" for cid in (A, B, C))

    # Another completion afterwards is a no-op
    assert run_round(env, t3) is None


def test_kdm_in_round_one_stops_everything(env):
    fake_hs, fake_claude, _, _ = env
    run_round(env)
    t1 = fake_hs.add_task("t1", 21)
    fake_hs.add_call(A, t1, NO_ANSWER)
    fake_hs.add_call(B, t1, CONNECTED, "Yes")
    plan = run_round(env, t1)
    assert plan["recipients"] == [] and not fake_claude.prompts
    assert all(contact(fake_hs, cid)["hplmt_email_2"] == "" for cid in (A, B, C))
    opp = fake_hs.objects[hs.OPPORTUNITY][OPP]
    assert opp["hpl_call_round"] == "1" and opp["hpl_next_call_task_title"] == ""
    assert all(contact(fake_hs, cid)["hplmt_active_opportunity_id"] == "" for cid in (A, B, C))  # released
    t2 = fake_hs.add_task("t2", 23)
    assert run_round(env, t2) is None  # stopped sequence ignores later tasks


def test_round_0_excludes_unsubscribed_and_contacts_in_another_sequence(env):
    fake_hs, _, _, _ = env
    contact(fake_hs, A)["hplmt_active_opportunity_id"] = "777"
    contact(fake_hs, C)["hs_email_optout"] = "true"
    plan = run_round(env)
    assert plan["recipients"] == [B]
    assert plan["matrix_after"]["order"] == [B]
    assert contact(fake_hs, B)["hplmt_active_opportunity_id"] == OPP
    assert contact(fake_hs, A)["hplmt_active_opportunity_id"] == "777"
    assert "hplmt_email_1" not in contact(fake_hs, A) and "hplmt_email_1" not in contact(fake_hs, C)


def test_unsubscribe_stops_that_contact(env):
    env[3].setenv("PROMPT_VERSION", "default")  # asserts the original set's wording
    fake_hs, fake_claude, _, _ = env
    run_round(env)
    contact(fake_hs, C)["hs_email_optout"] = "true"
    t1 = fake_hs.add_task("t1", 21)
    for cid in (A, B, C):
        fake_hs.add_call(cid, t1, NO_ANSWER)
    plan = run_round(env, t1)
    states = {cid: plan["matrix_after"]["contacts"][cid]["state"] for cid in (A, B, C)}
    assert states == {A: "MISSED", B: "MISSED", C: "OPTED_OUT"}
    assert plan["recipients"] == [A, B]
    assert "Cara" not in fake_claude.prompts[A] and "Cara" not in fake_claude.prompts[B]  # C is never mentioned
    assert contact(fake_hs, C)["hplmt_email_2"] == "" and contact(fake_hs, C)["hplmt_active_opportunity_id"] == ""


def test_uncalled_contact_gets_no_email_and_is_not_referenced(env):
    env[3].setenv("PROMPT_VERSION", "default")  # asserts the original set's wording
    fake_hs, fake_claude, _, _ = env
    run_round(env)
    t1 = fake_hs.add_task("t1", 21)
    fake_hs.add_call(A, t1, NO_ANSWER)
    fake_hs.add_call(B, t1, NO_ANSWER)
    plan = run_round(env, t1)  # rep forgot Cara
    assert plan["recipients"] == [A, B] and plan["uncalled"] == [C]
    assert "Cara" not in fake_claude.prompts[A] and "tried calling Ben" in fake_claude.prompts[A]
    assert contact(fake_hs, C)["hplmt_email_2"] == ""
    assert "Cara C" in fake_hs.objects[hs.OPPORTUNITY][OPP]["hpl_next_call_task_title"]  # still on the next task
    assert contact(fake_hs, C)["hplmt_active_opportunity_id"] == OPP


def test_non_round_task_and_closed_opportunity_are_ignored(env):
    fake_hs, _, _, _ = env
    run_round(env)
    other = fake_hs.add_task("x1", 21, subject="Send follow-up email", task_type="EMAIL")
    assert run_round(env, other) is None
    assert fake_hs.objects[hs.OPPORTUNITY][OPP]["hpl_call_round"] == "0"
    fake_hs.objects[hs.OPPORTUNITY][OPP]["status"] = "Closed"
    t1 = fake_hs.add_task("t1", 21)
    assert run_round(env, t1) is None


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
    contact(fake_hs, A)["hplmt_email_2"] = "already sent"
    # Re-run the whole round
    run_round(env, t1)
    assert contact(fake_hs, A)["hplmt_email_2"] == "already sent"
    assert A not in fake_claude.prompts
    assert fake_hs.objects[hs.OPPORTUNITY][OPP]["hpl_call_round"] == "1"


def test_dry_run_writes_nothing(env):
    fake_hs, _, tmp, mp = env
    mp.setenv("DRY_RUN", "true")
    run_round(env)
    assert "hpl_call_round" not in fake_hs.objects[hs.OPPORTUNITY][OPP]
    assert "hplmt_email_1" not in contact(fake_hs, A)
    assert json.loads((tmp / f"email_{A}.json").read_text(encoding="utf-8"))["written"] is False
    assert not fake_hs.notes
    assert "THE ROLE" in json.loads((tmp / "finalize.json").read_text(encoding="utf-8"))["pinned_note_html"]


# --- prompt sets ----------------------------------------------------------------

def test_default_prompt_set_does_not_name_other_contacts(env):
    env[3].setenv("PROMPT_VERSION", "default")  # asserts the original set's wording
    fake_hs, fake_claude, tmp, mp = env
    run_round(env)
    assert "you should be asking" not in fake_claude.prompts[A]
    assert "HOW JP WRITES" not in fake_claude.prompts[A]
    assert json.loads((tmp / f"email_{A}.json").read_text())["prompt_version"] == "default"


def test_webhook_run_with_no_prompt_version_uses_jp(env):
    """The HubSpot webhook never sends prompt_version. That run must be the JP set, end to end."""
    fake_hs, fake_claude, tmp, mp = env
    mp.delenv("PROMPT_VERSION", raising=False)
    run_round(env)
    assert "## HOW JP WRITES" in fake_claude.prompts[A]
    assert "Not sure if it's you, Ben or Cara I should be asking" in fake_claude.prompts[A]
    assert "Selling point picked for this person" in fake_claude.prompts[A]  # research ran under JP too
    assert json.loads((tmp / f"email_{A}.json").read_text())["prompt_version"] == "jp"
    assert contact(fake_hs, A)["hplmt_email_1"].endswith(f"Body 1 for {A}")  # the fake prefixes the JP opener


def test_jp_prompt_set_names_other_contacts_in_email_1(env):
    fake_hs, fake_claude, tmp, mp = env
    mp.setenv("PROMPT_VERSION", "jp")
    run_round(env)
    assert "Not sure if it's you, Ben or Cara I should be asking" in fake_claude.prompts[A]
    assert "Not sure if it's you, Alice or Cara I should be asking" in fake_claude.prompts[B]
    assert "Not sure if it's you, Alice or Ben I should be asking" in fake_claude.prompts[C]
    assert "Where the role is based: Brisbane" in fake_claude.prompts[A]
    assert "Company size: 4 staff" in fake_claude.prompts[A]
    assert "Register scales with the size" in fake_claude.prompts[A]
    assert "under 90 words" in fake_claude.prompts[A]
    # the voice guide rides in with every JP prompt
    assert "## HOW JP WRITES" in fake_claude.prompts[A]
    assert "Lines JP rejected" in fake_claude.prompts[A]
    assert json.loads((tmp / f"email_{A}.json").read_text())["prompt_version"] == "jp"
    assert contact(fake_hs, A)["hplmt_email_1"].endswith(f"Body 1 for {A}")  # the fake prefixes the JP opener


def test_jp_prompt_set_is_self_contained():
    """Every default prompt file has a jp counterpart (jp may add files of its own)."""
    default = Path(__file__).parent.parent / "prompts"
    jp = default / "jp"
    expected = {p.relative_to(default) for p in default.rglob("*.md") if "jp" not in p.parts}
    assert expected <= {p.relative_to(jp) for p in jp.rglob("*.md")}


def test_jp_research_gets_selling_points_and_emails_get_the_chosen_line(env):
    fake_hs, fake_claude, tmp, mp = env
    mp.setenv("PROMPT_VERSION", "jp")
    run_round(env)
    research = json.loads(fake_hs.objects[hs.OPPORTUNITY][OPP]["hpl_research_payload"])
    assert research["selling_points"][A]["line"] == f"point {A}"
    assert f"Selling point picked for this person (copy it word for word, and only where the email spec asks for it):** point {A}" \
        in fake_claude.prompts[A]


def test_default_set_has_no_selling_points(env):
    env[3].setenv("PROMPT_VERSION", "default")  # asserts the original set's wording
    fake_hs, fake_claude, tmp, mp = env
    run_round(env)
    assert "SELLING POINTS LIBRARY" not in fake_claude.prompts[A]
    assert "Selling point picked" not in fake_claude.prompts[A]


def test_unknown_prompt_set_fails_loudly(env, monkeypatch):
    import utils
    monkeypatch.setenv("PROMPT_VERSION", "nope")
    with pytest.raises(RuntimeError, match="PROMPT_VERSION"):
        utils.prompts_dir()


def test_blank_prompt_version_is_jp_and_default_is_the_original(monkeypatch):
    """The HubSpot webhook sends no prompt_version, so blank must be the JP set (the live one).
    'default' selects prompts/ (the original)."""
    from utils import DEFAULT_PROMPT_VERSION, PROMPTS_ROOT, prompt_version, prompts_dir
    assert DEFAULT_PROMPT_VERSION == "jp"
    monkeypatch.delenv("PROMPT_VERSION", raising=False)
    assert prompt_version() == "jp" and prompts_dir() == PROMPTS_ROOT / "jp"
    monkeypatch.setenv("PROMPT_VERSION", "  ")
    assert prompts_dir() == PROMPTS_ROOT / "jp"
    for name in ("default", "Default", "original", "base"):
        monkeypatch.setenv("PROMPT_VERSION", name)
        assert prompt_version() == "" and prompts_dir() == PROMPTS_ROOT
    monkeypatch.setenv("PROMPT_VERSION", "jp")
    assert prompts_dir() == PROMPTS_ROOT / "jp"



def test_role_location_prefers_the_ad_then_the_crm_company():
    assert emailgen.role_location({"job_post_city": "Bankstown", "job_post_state": "NSW"}, {"city": "Brisbane"}) == "Bankstown"
    assert emailgen.role_location({"job_post_state": "NSW"}, {"city": "Brisbane"}) == "NSW"
    assert emailgen.role_location({}, {"city": "Brisbane", "state": "QLD"}) == "Brisbane"
    assert emailgen.role_location({}, {"state": "QLD"}) == "QLD"
    assert emailgen.role_location({}, {}) == ""


def test_jp_prompt_set_round_1_uses_jp_route_b_wording(env):
    fake_hs, fake_claude, tmp, mp = env
    mp.setenv("PROMPT_VERSION", "jp")
    run_round(env)
    t1 = fake_hs.add_task("t1", 21)
    for cid in (A, B, C):
        fake_hs.add_call(cid, t1, NO_ANSWER)
    plan = run_round(env, t1)
    assert plan["route"] == "B"
    prompt = fake_claude.prompts[A]
    assert "Just tried giving you a quick call and didn't catch you." in prompt
    assert "you tried Ben too and didn't get through" in prompt and "you tried Cara too" in prompt
    assert "just tried calling them and didn't catch them" in prompt
    assert "who you should be talking to" in prompt
    assert "under 140 words" in prompt and "## HOW JP WRITES" in prompt
    assert "To be fair" not in prompt and "Open with exactly" not in prompt
    assert contact(fake_hs, A)["hplmt_email_2"] == f"Body 2 for {A}"

    # Round 2: Ben connects (not the KDM); Alice and Cara get the short nudge
    t2 = fake_hs.add_task("t2", 23)
    fake_hs.add_call(A, t2, NO_ANSWER)
    fake_hs.add_call(B, t2, CONNECTED, "No")
    fake_hs.add_call(C, t2, NO_ANSWER)
    plan = run_round(env, t2)
    assert plan["recipients"] == [A, C]
    prompt = fake_claude.prompts[A]
    assert "tried them again and still had no luck" in prompt
    assert "you had a quick chat with Ben and Ben reckons it probably sits with someone else" in prompt
    assert "you tried Cara too" in prompt
    assert "300 to 500 applicants" in prompt and "under 80 words" in prompt
    assert "point you at them" in prompt
    assert "barking up the wrong tree here" not in prompt


# --- fact check -------------------------------------------------------------------

def test_fact_check_stops_an_email_that_misstates_a_call(env):
    import factcheck
    fake_hs, fake_claude, tmp, mp = env
    run_round(env)
    t1 = fake_hs.add_task("t1", 21)
    for cid in (A, B, C):
        fake_hs.add_call(cid, t1, NO_ANSWER)
    # The model claims a chat with Cara, who was missed. Alice's email must not be written.
    fake_claude.canned[A] = "Just tried you. Had a quick chat with Cara, who reckons this sits with someone else."
    mp.setenv("TASK_ID", t1)
    plan_round.main()
    mp.setenv("CONTACT_ID", A)
    with pytest.raises(factcheck.FactCheckError, match="says we reached Cara"):
        generate_route_b.main()
    assert contact(fake_hs, A).get("hplmt_email_2", "") == ""
    rejected = json.loads((tmp / f"rejected_{A}.json").read_text())
    assert rejected["problems"] and "Cara" in rejected["problems"][0]
    # The other contacts are unaffected
    for cid in (B, C):
        mp.setenv("CONTACT_ID", cid)
        generate_route_b.main()
        assert contact(fake_hs, cid)["hplmt_email_2"] == f"Body 2 for {cid}"


def test_jp_email_1_without_the_names_gets_one_revision_then_is_written(env):
    fake_hs, fake_claude, tmp, mp = env
    mp.delenv("PROMPT_VERSION", raising=False)
    fake_claude.canned[B] = ["Not sure if it's you I should be asking, but I saw the role. How's it going?",
                             "Not sure if it's you, Alice or Cara I should be asking, but I saw the role. How's it going?"]
    run_round(env)
    retry = fake_claude.prompts[f"{B}_retry"]
    assert "## REVISION REQUIRED" in retry
    assert "does not name Alice" in retry and "does not name Cara" in retry
    assert contact(fake_hs, B)["hplmt_email_1"].startswith("Not sure if it's you, Alice or Cara")
    assert not (tmp / f"rejected_{B}.json").exists()
    # the others passed first time
    assert f"{A}_retry" not in fake_claude.prompts and f"{C}_retry" not in fake_claude.prompts


def test_jp_email_1_still_nameless_after_revision_is_refused(env):
    fake_hs, fake_claude, tmp, mp = env
    mp.delenv("PROMPT_VERSION", raising=False)
    fake_claude.canned[B] = ["Not sure if it's you I should be asking. Body.", "Still not naming anyone. Body."]
    mp.setenv("TASK_ID", "")
    plan_round.main()
    research_opportunity.main()
    mp.setenv("CONTACT_ID", B)
    with pytest.raises(factcheck.FactCheckError, match="does not name Alice"):
        generate_route_a.main()
    rejected = json.loads((tmp / f"rejected_{B}.json").read_text())
    assert rejected["first_attempt_problems"] and any("Cara" in p for p in rejected["problems"])
    assert contact(fake_hs, B).get("hplmt_email_1", "") == ""


def test_default_set_email_1_has_no_required_names(env):
    """The original prompts/ email 1 never names anyone, so the rule must not apply to it."""
    fake_hs, fake_claude, tmp, mp = env
    mp.setenv("PROMPT_VERSION", "default")
    run_round(env)
    assert contact(fake_hs, A)["hplmt_email_1"] == f"Body 1 for {A}"
    assert f"{A}_retry" not in fake_claude.prompts
