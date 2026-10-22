# High Prio Leads — Multithreading

A GitHub Actions pipeline that runs a **multi-contact** cold sequence for one HubSpot **Opportunity** (job ad).
Every contact associated with the Opportunity is called over 3 rounds. After each round a deterministic
**call matrix** decides who gets the next email and what it may say. Emails 2 and 3 reference the other
people the rep has been trying to reach.

Sister project of [High-Prio-Leads](https://github.com/staffdomain-devops/High-Prio-Leads), which is
one contact per run. This repo is one Opportunity per run, fanning out to its contacts.

---

## How a sequence runs

| Round | HubSpot event | `hpl_call_round` | Route | Writes per live contact |
|---|---|---|---|---|
| 0 | Call task 1 created | empty → `0` | A | Research (once per Opportunity) → `email_1`. Wipes `email_2..5`. |
| 1 | Call task 1 completed | `0` → `1` | B | `email_2` |
| 2 | Call task 2 completed | `1` → `2` | B | `email_3` |
| 3 | Call task 3 completed | `2` → `3` | A | `email_4` + `email_5` |

- **The round comes from `hpl_call_round`** on the Opportunity: this run processes `hpl_call_round + 1`, with empty meaning round 0. The pipeline is the only writer of that property, and it writes it last. A failed run therefore never advances the round, and re-completing the task retries it.
- **A round's calls** are, for each contact, the latest call whose activity date falls inside that task's **created → completed** window.
- **HubSpot sends.** The pipeline only writes `subject_N` / `email_N`. HubSpot's send workflows enrol on "`email_N` is known", with re-enrolment on. A dropped contact has their remaining slots cleared, so they never enrol.
- **HubSpot enforces "call everyone".** The pipeline does no validation of its own. A contact with no call in the window counts as MISSED.

After round 3, or once the Opportunity is closed lost, further webhooks are no-ops. A webhook for a task that isn't completed is also a no-op (round ≥ 1).

---

## The call matrix

Logic lives in `scripts/build_matrix.py` (pure, unit-tested). State is stored as JSON in `hpl_call_matrix` on the Opportunity.

### Pool and call order

The pool is every contact associated with the Opportunity at round 0. It's locked at round 0, so contacts added later are ignored. The call order is set once at round 0:

1. `contact_icp_rating`: Prime → Core → Opportunistic → Non-ICP → blank. The MM/SMB suffix is ignored.
2. `kdm_rank` ascending (blank goes last).
3. Contact ID ascending, as a final tiebreak.

### Contact states (worked out per round from the latest call)

| Call outcome (`hs_call_disposition`) | KDM on this call? (`hpl_call_kdm_status`) | State | Effect |
|---|---|---|---|
| No answer, Busy, Left voicemail, Left live message, Gate Keeper, Wrong number, **or no call** | ignored | `MISSED` | Stays in pool. Referenced in others' emails. |
| Connected, Connected Interested, Connected Call Back later | Unclear / blank | `SPOKE_UNCLEAR` | Stays in pool. Referenced. |
| Connected, Connected Interested, Connected Call Back later | No | `NOT_KDM` | Leaves the pool. Still referenced ("mentioned this probably sits with someone else"). |
| Connected, Connected Interested, Connected Call Back later | Yes | `KDM` | **Everyone else is frozen.** The KDM is called alone in later rounds. |
| Connected Not Interested | No / Unclear / blank | `OPTED_OUT` | Leaves the pool. **Never referenced.** |
| Connected Not Interested | Yes | `CLOSED_LOST` | **The whole sequence stops** and every contact's remaining slots are cleared. |
| (someone else became KDM / closed lost) | n/a | `FROZEN` | No further emails. Never referenced. |

Rules:
- KDM = Yes on a non-connected call is ignored. A KDM can't be confirmed over voicemail.
- If two contacts are marked KDM in the same round, **the first in call order wins** and the other is frozen.
- **The KDM track:** once a KDM is found, only the KDM is processed. Any later "Connected Not Interested" from them means `CLOSED_LOST`, even if the KDM field is blank on that call. Any other outcome keeps them as the KDM.

### Opportunity status

`active` (pool mode) → `kdm_found` (KDM track) → `closed_lost`, a terminal state reachable from either.

---

## Email routes

| Route | Emails | Workflow | Script | Prompt |
|---|---|---|---|---|
| A | 1, 4, 5 | `route-a.yml` | `generate_route_a.py` | `prompts/route_a_prompt.md` + `specs/email_{1,4,5}.md` |
| B | 2, 3 | `route-b.yml` | `generate_route_b.py` | `prompts/route_b_prompt.md` + `specs/email_{2,3}.md` |

Both routes share `prompts/base.md`: who we are, prospect details, research insights and writing rules.

**Route A** never references calls or other people. The KDM's emails 4 and 5 also get their last call outcome as *soft context*, which the prompt tells the model not to mention, so the emails never read as though no conversation happened.

**Route B** gets a **call scenario** built in Python (`scenario_instructions`), so the model only writes prose around facts it's given:

| Recipient | Email 2 opener | Email 3 opener | Reference line | Close |
|---|---|---|---|---|
| `MISSED` | "I just tried giving you a quick call. To be fair, I'm not entirely sure you're the right person…" | "I may be barking up the wrong tree here, but I just tried giving you a quick call…" | Yes | Point me in the right direction |
| `SPOKE_UNCLEAR` | "Thanks for taking my call earlier." + a right-person check | "Thanks again for the chat earlier about the [role] role." | Yes | Point me in the right direction |
| `KDM`, connected this round | "Great speaking with you earlier." | "Good speaking with you again earlier about the [role] role." | **None** | Offer profiles / a short call |
| `KDM`, missed this round | "I just tried giving you a quick call about the [role] role…" | "I just tried giving you a quick call about the [role] role and didn't manage to catch you." | **None** | Offer profiles / a short call |

The reference line is one sentence and uses first names only, with no job titles and no gendered pronouns. It's built from these facts:
- `MISSED`: "you also tried calling Alice and didn't get through"
- `SPOKE_UNCLEAR`: "you had a quick chat with Alice"
- `NOT_KDM`: "Alice mentioned this probably sits with someone else"

`OPTED_OUT` and `FROZEN` contacts are never mentioned. If there's nobody to reference, the email is the original email 2 or 3.

### Research

Research runs **once per Opportunity at round 0** (`research_opportunity.py`):
- It produces the company summary, the job insights and 4 shared resources (case study, YouTube video, and website resources for emails 2 and 5), plus a **buyer frame per contact**.
- The email-2 website resource is chosen from the *primary* contact's title, meaning the first contact in call order.
- The result is stored in `hpl_research_payload`, and rounds 1 to 3 reuse it.

Each contact's `hpl_research_summary` is written at round 0.

---

## Pipeline layout

```
opp-round.yml  (repository_dispatch "opp-round" | workflow_dispatch)
  plan       plan_round.py      read-only: Opportunity, contacts, task window, calls
                                → call matrix → plan.json + outputs (round, route, contacts)
  route_a ─┐ route-a.yml        [round 0: research_opportunity.py] → generate_route_a.py × contact
  route_b ─┘ route-b.yml        generate_route_b.py × contact
  finalize   finalize_round.py  clear dropped contacts' slots, then write hpl_call_matrix,
                                next task title/notes, research (round 0), hpl_call_round (last)
  notify     Teams card on any failure
tests.yml    pytest on every push
```

- **One job per contact** runs in parallel (`strategy.matrix`, `fail-fast: false`). One contact failing doesn't block the others, but finalize only runs when **all** of them succeed.
- **Re-run safety:** a contact's slot is only written when it's empty, because round 0 wiped slots 2 to 5. A re-run after a partial failure skips contacts whose email already went out, so nobody gets an email twice.
- **Concurrency** is one run per Opportunity at a time (`concurrency: opp-<id>`).
- **Model:** `claude-sonnet-5-5` for all three agents (`scripts/claude.py`).

| Script | Role |
|---|---|
| `build_matrix.py` | Pure matrix logic: ordering, states, recipients, slots to clear, route contexts, task text |
| `plan_round.py` | Fetch + apply the matrix, write `plan.json` |
| `research_opportunity.py` | Research Agent (round 0) |
| `emailgen.py` | Shared prompt rendering, the Claude call, per-contact write and re-run guard |
| `generate_route_a.py` / `generate_route_b.py` | Per-contact email generation |
| `finalize_round.py` | Opportunity write-back |
| `hs.py`, `claude.py`, `utils.py` | HubSpot REST client, Claude client, helpers |

---

## Running it

### Automatically (HubSpot webhook)

HubSpot workflow action "Send a webhook":

- **POST** `https://api.github.com/repos/staffdomain-devops/High-Prio-Leads-Multithreading-/dispatches`
- Headers: `Authorization: Bearer <GitHub token with Actions/Contents write on this repo>`, `Accept: application/vnd.github+json`
- Body:
  ```json
  {"event_type": "opp-round", "client_payload": {"opportunity_id": "<Opportunity ID>", "task_id": "<Task ID>"}}
  ```

Fire it on **call task 1 created** (round 0) and on **every call task completed** (rounds 1 to 3). The same webhook works for all rounds, because the pipeline reads the round from `hpl_call_round`.

### Manually

Go to Actions → **Opportunity Call Round** → Run workflow:

| Input | Required | Behaviour |
|---|---|---|
| `opportunity_id` | yes | The Opportunity to process. Its next round runs. |
| `task_id` | no | Blank means the Opportunity's most recently completed task. |
| `dry_run` | no (off) | Runs everything, including the Claude calls, but **writes nothing to HubSpot**. Review the `round-output-<id>` artifact. |

Each run uploads `round-output-<opportunity_id>`, containing `plan.json`, `research.json`, `email_<contact>.json` and `finalize.json`.

---

## Setup checklist

### HubSpot properties

| Object | Property | Type | Notes |
|---|---|---|---|
| Call | `hpl_call_kdm_status`, labelled **"KDM on this call?"** | Dropdown: `Yes` / `No` / `Unclear` | Put it in the Call information group, next to Call outcome. Internal values must be exactly `Yes`, `No`, `Unclear`. |
| Opportunity | `hpl_call_round` | Number | Pipeline-owned. Never edit it in a workflow. |
| Opportunity | `hpl_call_matrix` | Multi-line text | JSON. Pipeline-owned. |
| Opportunity | `hpl_research_payload` | Multi-line text | JSON. Pipeline-owned. |
| Opportunity | `hpl_next_call_task_title` | Single-line text | Use as a token in the next task's title. |
| Opportunity | `hpl_next_call_task_notes` | Multi-line text | Use as a token in the next task's notes. |
| Contact | `subject_1..5`, `email_1..5` | Single-line / multi-line text | Same as High-Prio-Leads. |
| Contact | `hpl_research_summary`, `hpl_generated_date` | Multi-line text / Date | Same as High-Prio-Leads. |

### HubSpot workflows

1. **Webhook on call task 1 created**, then on **each call task completed**, for tasks associated with the Opportunity. This is the POST described above.
2. **Send workflows**, one per email: enrolment trigger "`email_N` is known", **re-enrolment on**.
3. **"Call everyone" check:** the rep must log a call for every contact in the task notes unless a KDM is confirmed. The pipeline does not check this.
4. **Next call task:** create it with title `{{hpl_next_call_task_title}}` and notes `{{hpl_next_call_task_notes}}`. Skip creation when the title is empty (sequence finished, or closed lost).

### GitHub secrets

| Secret | Use |
|---|---|
| `HUBSPOT_API_KEY` | Private App token. Read: contacts, companies, calls, tasks, Opportunity custom object. Write: contacts, Opportunity custom object. |
| `ANTHROPIC_API_KEY` | Claude |
| `TEAMS_WEBHOOK_URL` | Failure notifications |

---

## Testing

```
pip install -r requirements.txt pytest
python -m pytest -q
```

- `tests/test_build_matrix.py` covers every matrix scenario: all missed, KDM mid-round, the KDM track, KDM not interested, opted out, not KDM, ordering, the call window, and task text.
- `tests/test_route_b_scenario.py` covers the Route B scenario instructions.
- `tests/test_pipeline_offline.py` runs a full round 0 → 3 sequence end to end against an in-memory HubSpot and a fake Claude. It also covers the re-run guard and dry run.
