# JP version

A second prompt set, selected with `PROMPT_VERSION=jp` (workflow input `prompt_version`). The default set in `prompts/` is untouched; the pipeline loads this directory instead when the version is set. Files not listed below are byte-for-byte copies of the defaults, so this directory is self-contained.

What's different:

| File | Change |
|---|---|
| `specs/email_1.md` | One question, under 90 words. Opener names the other people in the pool ("not sure if it's you, Ben or Cara I should be asking", `{{contact.who_clause}}`) and where the role is based (`{{opportunity.location_clause}}`, from the ad's city/state or the CRM company city). One specific positive comment on something the ad says. Then "How are you going finding someone for it?" and a casual close. Nothing about Staff Domain: no company name, no link, no savings, no disclaimer. |
| `specs/email_2.md` | Carries what email 1 dropped: says what we are (with the "not a recruitment agency, not a BPO" line), the savings point, and the case study one-liner as its only link, in place of the website resource. |
| `base.md` | Voice rules: casual Aussie, no sales-speak, pitch to the buyer frame, no "Hi Alice" in the body, disclaimer only when the spec asks. Adds the role's location to the prospect details. |
| `research_prompt.md` | Resource one-liners written like you'd say them to a mate, with a good and a bad example. No "case study", "offshore", "from us" or "achieved". |

Emails 3, 4 and 5 and the route prompts are unchanged for now.
