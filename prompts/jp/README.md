# JP version

A second prompt set, selected with `PROMPT_VERSION=jp` (workflow input `prompt_version`). The default set in `prompts/` is untouched; the pipeline loads this directory instead when the version is set. Files not listed below are byte-for-byte copies of the defaults, so this directory is self-contained.

What's different:

| File | Change |
|---|---|
| `specs/email_1.md` | One question, under 90 words. Opener names the other people in the pool ("not sure if it's you, Ben or Cara I should be asking", `{{contact.who_clause}}`) and where the role is based (`{{opportunity.location_clause}}`, from the ad's city/state or the CRM company city). One specific positive comment on something the ad says. Then "How are you going finding someone for it?" and a casual close. Nothing about Staff Domain: no company name, no link, no savings, no disclaimer. |
| `specs/email_2.md` | Under 140 words. "Just tried giving you a quick call and didn't catch you", who else you tried, "so I'm still not 100% sure who the right person is for this one". "Quick FYI, I'm not a recruiter, and we're not an agency or a BPO", what we do in one sentence with 1-2 pieces of work from the ad, "They just sit in one of our offices rather than yours", "Usually saves tens of thousands a year". "True story:" plus the case study one-liner as the only link. Close: "If I'm barking up the wrong tree and should be talking to someone else, just let me know and I'll follow them up." |
| `specs/email_3.md` | Under 80 words, no link. "Tried you again just now, still no luck." plus who else ("Cara too."). "Honest question: we put dedicated people on exactly this kind of [work from the ad], the same way [proof point company] run their ops, in weeks rather than months and for up to 70% less than hiring locally. Worth a ten-minute chat to see if it fits?" "Happy either way, just let me know. And if it's someone else's call, point me at them and I'll stop bugging you." |

The fixed Route B wording (openers, reference facts, closes) for emails 2 and 3 lives in `JP_TEXT` in `scripts/generate_route_b.py`.

| `base.md` | Voice rules: casual Aussie, no sales-speak, pitch to the buyer frame, no "Hi Alice" in the body, disclaimer only when the spec asks. Adds the role's location to the prospect details. |
| `selling_points.md` | JP's selling-points library (30 Oct 2026), model-facing copy: internal-only notes, competitor names and unverified claims removed. The research agent picks one line per contact for their buyer frame; it reaches the email agents as `{{research.selling_point}}`. House savings line is "up to 70%". |
| `research_prompt.md` | Gets the selling points library and returns one chosen line per contact. Resource one-liners written like you'd say them to a mate, with a good and a bad example. No "case study", "offshore", "from us" or "achieved". |

Emails 4 and 5 are unchanged for now.
