# HubSpot workflows for High Prio Leads — Multithreading

The pipeline only **writes**: emails onto contacts, and the matrix, round and next task text onto the Opportunity. HubSpot does the rest: it starts the pipeline, creates the call tasks, and sends the emails. That takes 4 kinds of workflow, plus one custom code script (`hubspot/dispatch_round.js`) shared by W1 and W3.

```
  Opportunity becomes eligible
        │
  W1 ── custom code ──▶ GitHub: round 0 ──▶ writes hplmt_email_1 on contacts,
        │                                   hpl_call_round = 0, next task title/notes
        │                                         │
        │                     W4 (hplmt_email_1 is known) sends email 1
        ▼                                         │
  W2  hpl_call_round changed + title known ──▶ (delay) ──▶ create call task "Call N of 3 · …"
        │
  rep calls everyone in the task notes, logs each call (outcome + "KDM on this call?"),
  completes the task
        │
  W3  Call task completed ── custom code ──▶ GitHub: round N ──▶ writes email_(N+1) for
                                                               contacts still to reach,
                                                               clears dropped contacts,
                                                               hpl_call_round = N, next task
        │
  W5/W6/W7 (hplmt_email_N is known) send the email; W2 fires again for the next task ... until
  the title comes back empty (KDM reached, closed lost, nobody left, or round 3 done)
```

---

## W1: Start the sequence (round 0)

| Setting | Value |
|---|---|
| Object | **Opportunity** |
| Enrolment trigger | *Your eligibility criteria* (e.g. the existing `create_task_1 = yes` / high-priority rule) **AND** `HPL Call Round` **is unknown** **AND** `Status` = Open |
| Re-enrolment | Off |
| Action 1 | **Custom code** (Node 20). Secrets: `GITHUB_TOKEN`, `HUBSPOT_TOKEN`. Input field: `hs_object_id`. Code: `hubspot/dispatch_round.js`. |

The "`HPL Call Round` is unknown" condition makes W1 fire once per Opportunity, ever. Don't also let your existing workflow create "Task 1" for these Opportunities: W2 creates the call tasks here.

## W2: Create the next call task

| Setting | Value |
|---|---|
| Object | **Opportunity** |
| Enrolment trigger | `HPL Next Call Task Title` **is known** |
| Re-enrolment | **On**, when `HPL Call Round` changes |
| Branch on `HPL Call Round` | `0` → no delay. Task 1 goes out right after email 1 is written. `1`, `2` → **delay N business days** (your cadence, e.g. 2). |
| Action | **Create task**: type **Call**, title `{{HPL Next Call Task Title}}`, notes `{{HPL Next Call Task Notes}}`, assigned to the Opportunity owner, associated with this Opportunity, due a day after creation. |

When the pipeline stops the sequence, it writes an **empty** title. That makes the property unknown, so W2 never enrols and no further task is created.

## W3: Call task completed → next round

| Setting | Value |
|---|---|
| Object | **Task** |
| Enrolment trigger | `Task status` = Completed **AND** `Task type` = Call **AND** `Task title` contains `of 3 · ` **AND** the task is associated with an Opportunity |
| Re-enrolment | **Off**. Each call task fires once, so un-ticking and re-ticking a task can't double-advance a round. |
| Action 1 | **Custom code**: the same `hubspot/dispatch_round.js`, same secrets, input field `hs_object_id`. |

The title filter is what limits W3 to the pipeline's own call tasks. Any other task completed on the Opportunity, such as an email task or a follow-up, won't start a round.

## W4 to W8: Send the emails

There's one workflow per email: `hplmt_email_1` … `hplmt_email_5`.

| Setting | Value |
|---|---|
| Object | **Contact** |
| Enrolment trigger | `hplmt_email_N` **is known** |
| Re-enrolment | **On**, when `hplmt_email_N` changes. Round 0 wipes slots 2 to 5, so each new sequence goes from empty to known again. |
| Action | Send the email using the `hplmt_subject_N` / `hplmt_email_N` tokens, the same way the original High-Prio-Leads sends. |

There's no delay or check needed. The pipeline only writes `hplmt_email_N` for contacts who should get it. Dropped contacts are cleared and never enrol.

## "Call everyone" check (rep process)

A contact with **no call logged** in the task window gets **no email that round**, isn't mentioned in other people's emails, and is named in a Teams warning. The rep should log a call for every contact listed in the task notes before completing the task. The only exception is once a KDM is confirmed, when the rest don't need calling.

HubSpot can't block a task from being completed. The pipeline-side guard makes sure a skipped contact never gets an email that claims a call.

---

## Setup checklist

- [ ] GitHub fine-grained token, this repo only, **Contents: read & write** → HubSpot secret `GITHUB_TOKEN`
- [ ] HubSpot private app token with read on **tasks** and the **Opportunity custom object** → HubSpot secret `HUBSPOT_TOKEN`
- [ ] W1, W2, W3 and W4 to W8 built as above
- [ ] Your existing task-1 / sequence workflows exclude Opportunities that have `HPL Call Round` known
- [ ] Test on Opportunity `64510640916` (or a fresh test Opportunity), watching the GitHub Actions tab
