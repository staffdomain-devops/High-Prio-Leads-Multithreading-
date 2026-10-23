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
| 0 | Call task 1 created | empty → `0` | A | Research (once per Opportunity) → `hplmt_email_1`. Wipes `hplmt_email_2..5`. |
| 1 | Call task 1 completed | `0` → `1` | B | `email_2` |
| 2 | Call task 2 completed | `1` → `2` | B | `email_3` |
| 3 | Call task 3 completed | `2` → `3` | A | `email_4` + `hplmt_email_5` |

- **The round comes from `hpl_call_round`** on the Opportunity: this run processes `hpl_call_round + 1`, with empty meaning round 0. The pipeline is the only writer of that property, and it writes it last. A failed run therefore never advances the round, and re-completing the task retries it.
- **A round's calls** are, for each contact, the latest call whose activity date falls inside that task's **created → completed** window.
- **HubSpot sends.** The pipeline only writes `hplmt_subject_N` / `hplmt_email_N`. HubSpot's send workflows enrol on "`hplmt_email_N` is known", with re-enrolment on. A dropped contact has their remaining slots cleared, so they never enrol.
- **"Call everyone" is the rep's job.** HubSpot can't block a task from being completed. A pool contact with no call in the window gets **no email that round**, and the run shows a warning naming them.

Each of these makes a run a no-op:
- the sequence has finished or stopped (round 3 done, KDM reached, closed lost, or nobody left)
- the Opportunity's `Status` isn't Open
- the task isn't completed
- the task isn't one of the pipeline's call tasks (type Call, title containing "of 3 · ")

---

## The call matrix

Logic lives in `scripts/build_matrix.py` (pure, unit-tested). State is stored as JSON in `hpl_call_matrix` on the Opportunity.

### Pool and call order

The pool is every contact associated with the Opportunity at round 0, **except** contacts who have unsubscribed from email and contacts already in another Opportunity's sequence (`hplmt_active_opportunity_id` set to a different Opportunity). It's locked at round 0, so contacts added later are ignored. The call order is set once at round 0:

1. `contact_icp_rating`: Prime → Core → Opportunistic → Non-ICP → blank. The MM/SMB suffix is ignored.
2. `kdm_rank` ascending (blank goes last).
3. Contact ID ascending, as a final tiebreak.

### Contact states (worked out per round from the latest call)

**Rule: anyone who connects stops getting emails.** Only contacts we haven't reached (`MISSED`) get the next email. If the person who connects is the KDM, the whole sequence stops and the rep takes it from there.

| Call outcome (`hs_call_disposition`) | KDM on this call? (`hpl_call_kdm_status`) | State | Effect |
|---|---|---|---|
| No answer, Busy, Left voicemail, Left live message, Gate Keeper, Wrong number, **or no call** | ignored | `MISSED` | Gets the next email. Referenced in others' emails. |
| Connected, Connected Interested, Connected Call Back later | Unclear / blank | `SPOKE_UNCLEAR` | **Stops.** Still referenced ("had a quick chat with Alice"). |
| Connected, Connected Interested, Connected Call Back later | No | `NOT_KDM` | **Stops.** Still referenced ("mentioned this probably sits with someone else"). |
| Connected, Connected Interested, Connected Call Back later | Yes | `KDM` | **The whole sequence stops** for everyone, including the KDM. |
| Connected Not Interested | No / Unclear / blank | `OPTED_OUT` | **Stops.** **Never referenced.** |
| Connected Not Interested | Yes | `CLOSED_LOST` | **The whole sequence stops.** |
| (the KDM was reached) | n/a | `FROZEN` | Stops. Never referenced. |

Besides calls, **an unsubscribe** (`hs_email_optout`) takes a contact out of the pool (`OPTED_OUT`), and they're never mentioned in other people's emails.

**Replies are handled in HubSpot, not here.** If a contact replies mid-sequence, the whole sequence exits on the HubSpot side, so the pipeline never reads email or meeting records (and its token doesn't need email scopes).

**No call logged:** a pool contact with no call in the round's task window stays in the pool but **gets no email that round**, because the email would falsely claim a call. They're also left out of everyone else's references, they stay on the next call task, and the run shows a warning naming them.

Rules:
- When the sequence stops, every remaining slot is cleared, the next task title is empty, and later webhooks are no-ops.
- KDM = Yes on a non-connected call is ignored. A KDM can't be confirmed over voicemail.
- If two contacts are marked KDM in the same round, **the first in call order wins**.
- If everyone has connected without a KDM being found, nobody is left to email. The next task title is empty, and later webhooks are no-ops.

### Opportunity status

`active` → `kdm_found` or `closed_lost`. Both are terminal: no further emails or tasks.

---

## Email routes

| Route | Emails | Workflow | Script | Prompt |
|---|---|---|---|---|
| A | 1, 4, 5 | `route-a.yml` | `generate_route_a.py` | `prompts/route_a_prompt.md` + `specs/email_{1,4,5}.md` |
| B | 2, 3 | `route-b.yml` | `generate_route_b.py` | `prompts/route_b_prompt.md` + `specs/email_{2,3}.md` |

Both routes share `prompts/base.md`: who we are, prospect details, research insights and writing rules.

**Route A** never references calls or other people.

**Route B** only ever writes to `MISSED` contacts. It gets a **call scenario** built in Python (`scenario_instructions`), so the model only writes prose around facts it's given:

- The email 2 opener is "I just tried giving you a quick call. To be fair, I'm not entirely sure you're the right person…"
- The email 3 opener is "I may be barking up the wrong tree here, but I just tried giving you a quick call…"
- Both include a reference line about the others and close with "point me in the right direction".

The reference line is one sentence and uses first names only, with no job titles and no gendered pronouns. Connected contacts are referenced by **their call outcome**:

| Their call outcome | Fact given to the model |
|---|---|
| No answer, voicemail, etc. (`MISSED`) | "you also tried calling Alice and didn't get through" |
| Connected | "you had a quick chat with Alice" |
| Connected Interested | "you spoke with Alice, who was keen to hear more" |
| Connected Call Back later | "you spoke briefly with Alice, who asked you to call back another time" |
| Connected Not Interested | never mentioned |

If KDM on this call? = No, the fact also gets "…and Alice mentioned this probably sits with someone else".

`OPTED_OUT` and `FROZEN` contacts are never mentioned. If there's nobody to reference, the email is the original email 2 or 3.

### Research

Research runs **once per Opportunity at round 0** (`research_opportunity.py`):
- It produces the company summary, the job insights and 4 shared resources (case study, YouTube video, and website resources for emails 2 and 5), plus a **buyer frame per contact**.
- The email-2 website resource is chosen from the *primary* contact's title, meaning the first contact in call order.
- The result is stored in `hpl_research_payload`, and rounds 1 to 3 reuse it.

Each contact's `hplmt_research_summary` is written at round 0.

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
| Contact | `hplmt_subject_1..5`, `hplmt_email_1..5` | Single-line / multi-line text | **This pipeline's own fields**, separate from High-Prio-Leads' `subject_N` / `email_N`, so the two never overwrite each other. Group: *HPL Multithreading*. |
| Contact | `hplmt_research_summary`, `hplmt_generated_date` | Multi-line text / Date | Research notes for the rep, and the last write date. |
| Contact | `hplmt_active_opportunity_id` | Single-line text | The Opportunity whose sequence this contact is in. Set at round 0, cleared when the contact drops out or the sequence ends. Pipeline-owned. |

### HubSpot workflows

1. **Webhook on call task 1 created**, then on **each call task completed**, for tasks associated with the Opportunity. This is the POST described above.
2. **Send workflows**, one per email: enrolment trigger "`hplmt_email_N` is known", **re-enrolment on**.
3. **"Call everyone" check:** the rep must log a call for every contact in the task notes unless a KDM is confirmed. If someone is skipped, the pipeline leaves them out of that round's emails and shows a warning on the run.
4. **Next call task:** create it with title `{{hpl_next_call_task_title}}` and notes `{{hpl_next_call_task_notes}}`. Skip creation when the title is empty (sequence finished, or closed lost).

### GitHub secrets

| Secret | Use |
|---|---|
| `HUBSPOT_API_KEY` | Private App token. Read: contacts, companies, calls, tasks, Opportunity custom object. Write: contacts, Opportunity custom object. |
| `ANTHROPIC_API_KEY` | Claude |

---

## Testing

```
pip install -r requirements.txt pytest
python -m pytest -q
```

- `tests/test_build_matrix.py` covers every matrix scenario: all missed, KDM reached (stops everything), any connect stops that contact, KDM not interested, opted out, not KDM, everyone connected, ordering, the call window, and task text.
- `tests/test_route_b_scenario.py` covers the Route B scenario instructions.
- `tests/test_pipeline_offline.py` runs a full round 0 → 3 sequence end to end against an in-memory HubSpot and a fake Claude. It also covers the re-run guard and dry run.
