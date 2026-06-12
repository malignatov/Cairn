---
name: digest
description: |
  Pull the user's interests and research what's genuinely new in each,
  then synthesize a readable digest. Exploratory, not actionable — this
  feeds curiosity and keeps the user current in the domains they care
  about. The counterpart to opportunity_research's practical hunting.

  Triggers: "/digest", "what's new in my interests", "catch me up",
  "digest my interests", "what's happening in [topic]", "give me a
  digest", "what's new lately in the things I follow", "anything new in
  [field]".

  Not for: finding actionable opportunities or things to do (use
  opportunity_research — convergent and practical, where this is divergent
  and exploratory); diagnosing neglect or problems across life areas (use
  scan_life_units — diagnostic, where this feeds curiosity); capturing a
  fresh idea (use capture_idea) or content from this chat (use capture).
  The digest produces awareness, not action.
---

# Digest

The user wants a digest of what's happening in the topics they care about.
Your job: pull their interests, research each for recent developments,
and synthesize — not dump links, but tell them what actually moved.

This is exploratory. You're not looking for things to do; you're keeping
the user current and stimulated. If something sparks an idea, great, but
the digest's job is awareness.

## Before you begin

If you haven't this session, read `constitution://main` once. The digest
is the system's lightest, most generous mode — where the scanners are
careful and the principle-checker is morally weighty, this is curious and
warm. Hold the tone of a well-read friend catching the user up over
coffee, not a news aggregator.

## 0. Begin the operation

Call state_begin_operation("digest") and hold the operation_id.

## 1. Pull interests

Call state_query("interest", {"status": "active"}). These are the topics
to research.

If the user has many interests (more than ~5-6), don't try to cover all
of them in one pass — the digest would be bloated and the research
shallow. Instead, ask which they'd like to focus on this time, or focus
on a rotating subset and tell the user which ones you covered (so they
know what's left for next time).

If the user named specific topics when invoking the skill, use those
instead of (or in addition to) their stored interests. They might want
a digest on something that isn't a formal interest.

If there are no interests and the user named no topics, say so and offer
to digest whatever they'd like to name.

## 2. Research each topic

For each topic in scope, run real web research. Look for what's
GENUINELY NEW or notable recently — not the evergreen state of the field.

"What's new in biomimicry this month" — not "what is biomimicry."

Rules:
- Search for recent developments: news, releases, research, shifts,
  notable events.
- Prefer primary and authoritative sources. Skip SEO filler and
  AI-generated listicles.
- Take notes per topic. Note the source for anything you'll mention.
- Time-box per topic. A few solid developments beat a survey of
  everything.

## 3. Apply the quiet bar

Most topics won't have meaningful developments most of the time. That's
normal and fine. For any topic where nothing notable happened, either
say so in one line or omit it. Do NOT manufacture content to fill out a
topic. A digest covering 3 topics well and skipping 4 quiet ones is
better than 7 topics of padding.

## 4. Synthesize

For each topic with real developments, write a few paragraphs of
synthesis — what moved, why it might matter to someone interested in
this area, with links for going deeper. This is the core of the skill:
digest, don't collect. The user should be able to read your synthesis
and feel current without clicking anything, but be able to click if a
thread pulls them.

Lead the whole digest with the most significant development across all
topics, so the most important thing is first.

Present grouped by topic, readable, prose. No raw link dumps.

## 5. Offer to spark

After the digest, offer:

  "Anything here worth keeping as an idea? I can capture it."

If the user wants to, route to the ideas layer — capture the spark via
state_write("idea", ...) (status = spark) with a note on what prompted
it. Don't auto-capture anything; the digest is a window, not a recorder.
Only capture what the user explicitly wants kept.

## 6. Close the operation

Call state_end_operation(operation_id, "completed",
  notes="digested N topics: <list>; M skipped as quiet").

## Notes on judgment

- Synthesis over links, always. If you find yourself producing a list of
  headlines, stop and digest them into prose instead.
- Recency is the point. If your "developments" are years old, you've
  summarized the field instead of finding news. Search again for what's
  actually recent.
- The quiet bar is real. Don't pad. Silence on a topic is honest output.
- This is exploratory, not actionable. Don't turn the digest into a
  to-do list. If a development genuinely suggests an opportunity, that's
  what opportunity_research is for — you can note "this might be worth a
  proper opportunity look" in one line, but don't do the opportunity
  analysis here.
- Don't over-cover. Better to deeply digest a handful of topics than to
  shallowly touch every interest. Depth per topic beats breadth.
- Keep the tone of a well-read friend catching you up over coffee, not a
  news aggregator. You read this stuff; tell the user what's worth knowing.
