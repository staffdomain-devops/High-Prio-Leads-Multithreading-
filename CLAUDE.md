# High Prio Leads — Multithreading

One HubSpot Opportunity per run → 3 call rounds across its contacts → deterministic call matrix → per-contact emails. Full design, the HubSpot setup checklist and the scenario tables are in `README.md`. Read it before changing behaviour.

## Rules

- **`scripts/build_matrix.py` is pure.** No IO, no HubSpot, no Claude. All matrix behaviour changes go there, with a test in `tests/test_build_matrix.py`.
- **The pipeline is the only writer of `hpl_call_round`**, and finalize writes it last, in the same update as `hpl_call_matrix`.
- **A contact's email slot is written only when empty.** Round 0 wipes 2 to 5 and finalize clears dropped contacts. Don't add a code path that overwrites a populated slot: HubSpot re-enrols on "`hplmt_email_N` is known" and would re-send it.
- **Route B facts come from Python** (`scenario_instructions`). The model must never be asked to infer calls, referrals or KDM status itself.
- **Every email is fact-checked before it is written** (`scripts/factcheck.py`, pure, tested in `tests/test_factcheck.py`). A body that names someone it may not, says we reached someone we missed (or the reverse), or claims a call in a Route A email is refused, as is a `jp` email 1 that drops the names from the who-clause (`emailgen.required_names`). The model gets one revision with the reasons (`emailgen.generate_checked`); a second failure goes to `rejected_<contact>.json` and the run fails for that contact only. Keep the phrase lists there in step with the Route B wording.
- Opportunity custom object type: `p5402982_opportunities`.
- Disposition GUIDs in `build_matrix.py` are portal-specific. If HubSpot call outcomes change, update `DISPOSITION_LABELS` / `CONNECTED`. Any unknown outcome counts as MISSED.
- Model: `claude-sonnet-5-5` (`scripts/claude.py`).
- **Prompt sets.** `prompts/` is the original and stays as it is. A variant lives in `prompts/<name>/` as a complete copy (every `.md` file) and is selected with `PROMPT_VERSION=<name>` (workflow input `prompt_version`). Blank resolves to `jp` (`DEFAULT_PROMPT_VERSION` in `scripts/utils.py`), which is what the HubSpot webhook triggers, so **`prompts/jp/` is what runs live**; `PROMPT_VERSION=default` selects `prompts/`. Iterate on the variant, never on the original.

## Test

`python -m pytest -q` runs the matrix unit tests, the Route B scenario tests, and an offline end-to-end run of rounds 0 to 3.
