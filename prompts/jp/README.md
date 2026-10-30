# JP version

A second prompt set, selected with `PROMPT_VERSION=jp` (workflow input `prompt_version`). The default set in `prompts/` is untouched; the pipeline loads this directory instead when the version is set. Files not listed below are byte-for-byte copies of the defaults, so this directory is self-contained.

What's different:

The JP set separates **facts** from **voice**. Facts are fixed: who was called and what happened (Python, `JP_TEXT` in `scripts/generate_route_b.py`), the proof one-liner, every number and claim (`selling_points.md`). Voice is a guide, not a script: `voice.md` carries how JP writes, the emails JP has signed off, and the lines JP rejected and why. Each spec states the email's intent and guardrails and leaves the wording to the model.

| File | What it is |
|---|---|
| `voice.md` | How JP writes: beliefs about these emails, register, two signed-off emails as examples, rejected lines with reasons. Pulled into every JP prompt via `{{voice}}` in `base.md`. |
| `specs/email_1.md` | Before any call. A question, not a pitch. Under 90 words. Knows who else is on the list (`{{contact.who_clause}}`), where the role is based, and the ad. Nothing about Staff Domain, no company name, no claim of an earlier call. |
| `specs/email_2.md` | After a missed call. Who else was tried (facts from the CALL SCENARIO), the recruiter line once, what we are grounded in their ad, the proof point word for word as the only link, ask who the right person is. Under 140 words. May use the person's selling point or "on average around 70%", one at most. |
| `specs/email_3.md` | After a second missed call. Short nudge, under 80 words, no link. The angle is how we recruit (300 to 500 applicants screened, best three or four sent, CVs in 3 to 5 days, tests before you see anyone). No cost figures. Easy out. |
| `selling_points.md` | JP's selling-points library (30 Oct 2026), model-facing copy. Research agent picks one line per contact (`{{research.selling_point}}`). House savings line "on average around 70%". Replacement guarantee: first three months, resignations included. |
| `research_prompt.md` | Gets the selling points library and returns one chosen line per contact. Resource one-liners written like you'd say them to a mate. |
| `base.md` | Pulls in `voice.md`, shows the role's location and the chosen selling point, hard rules. |

Email 4: after three missed calls, Route A, under 130 words. "I know this isn't for everyone, but having looked at the role, this is something we do day in, day out"; what they'd get (we take the whole hiring pain), the one cost aside in the sequence ("on average around 70%"), the YouTube proof point as the only link, and "Can I send a couple of CVs over? No obligation, just so you can get a feel for the talent I'm talking about." No call references, no commercial-reframe language.

Email 5: the last one, Route A, under 100 words. Says it's the last from us on the role and leaves it with them, one specific detail about them, the honest-advice note (we're the first to say when a role should stay local), the website resource as a leave-behind and the only link, door left open in one line. No calls, no cost, no CV offer, nothing hurt or hurried.

All five emails now have JP specs.

The fixed Route B material (call facts, and guidance for the opener and close) lives in `JP_TEXT` in `scripts/generate_route_b.py`.
