# High Prio Leads — Multithreading

One HubSpot Opportunity per run → 3 call rounds across its contacts → deterministic call matrix → per-contact emails. Full design, the HubSpot setup checklist and the scenario tables are in `README.md`. Read it before changing behaviour.

## Rules

- **`scripts/build_matrix.py` is pure.** No IO, no HubSpot, no Claude. All matrix behaviour changes go there, with a test in `tests/test_build_matrix.py`.
- **The pipeline is the only writer of `hpl_call_round`**, and finalize writes it last, in the same update as `hpl_call_matrix`.
- **A contact's email slot is written only when empty.** Round 0 wipes 2 to 5 and finalize clears dropped contacts. Don't add a code path that overwrites a populated slot: HubSpot re-enrols on "`email_N` is known" and would re-send it.
- **Route B facts come from Python** (`scenario_instructions`). The model must never be asked to infer calls, referrals or KDM status itself.
- Opportunity custom object type: `p5402982_opportunities`.
- Disposition GUIDs in `build_matrix.py` are portal-specific. If HubSpot call outcomes change, update `DISPOSITION_LABELS` / `CONNECTED`. Any unknown outcome counts as MISSED.
- Model: `claude-sonnet-5-5` (`scripts/claude.py`).

## Test

`python -m pytest -q` runs the matrix unit tests, the Route B scenario tests, and an offline end-to-end run of rounds 0 to 3.
