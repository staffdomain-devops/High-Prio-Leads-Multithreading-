# JP version

A second prompt set, selected with `PROMPT_VERSION=jp` (workflow input `prompt_version`). The default set in `prompts/` is untouched; the pipeline loads this directory instead when the version is set. Files not listed below are byte-for-byte copies of the defaults, so this directory is self-contained.

What's different:

| File | Change |
|---|---|
| `specs/email_1.md` | Under 150 words, casual Australian voice. Opener names the other people in the pool: "not sure if it's you, Ben or Cara I should be speaking to" (`{{contact.who_clause}}`, built by `who_clause` in `build_matrix.py`, capped at two names). Carries the "not a recruitment agency, not a BPO" line. No bullets. Never claims an earlier call. |
| `specs/email_2.md` | Drops the recruitment firm / BPO disclaimer, since email 1 already said it. Keeps the savings line. |
| `base.md` | Voice rules: casual Aussie, no sales-speak, pitch to the buyer frame, no "Hi Alice" in the body, disclaimer only when the spec asks. |
| `research_prompt.md` | Resource one-liners written like you'd say them to a mate, with a good and a bad example. No "case study", "offshore", "from us" or "achieved". |

Emails 3, 4 and 5 and the route prompts are unchanged for now.
