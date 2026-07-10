---
name: clarify
description: "Ask the user a question when you need clarification, feedback, or a decision before proceeding. Two modes: (1) Multiple choice — up to 4 options; the user picks one or types their own via a 5th 'Other'. (2) Open-ended — a single free-text question. LOAD when the request is ambiguous, has forked into mutually exclusive approaches, or a hard-to-reverse action needs sign-off — NOT for questions you can resolve yourself from the workspace, files, or sensible defaults."
category: agent
---

# Clarify — ask the user before proceeding

Use this when you genuinely cannot proceed correctly without the user's input:
the request is ambiguous, the work forked into mutually exclusive approaches, or
a destructive / hard-to-reverse action needs sign-off. Ask, then **end the turn**
and wait — the user's next message is the answer.

## How the turn works (important)

This runtime has **no blocking-input tool**. A tool cannot pause mid-run and read
the user's reply — each turn is one request → stream → `finish_reason: stop`.
So "asking the user" means:

1. Emit the question as your **normal assistant reply**.
2. **Stop the turn** (do not call more tools, do not guess and continue).
3. The user's answer arrives as the **next message**; history is preserved, so
   you resume exactly where you paused.

Never fabricate an answer on the user's behalf and press on — that defeats the
purpose. Ask, stop, wait.

## When to ask vs. decide yourself

| Ask the user | Decide yourself (don't ask) |
|--------------|-----------------------------|
| Truly ambiguous goal / conflicting requirements | Answer is in the files, task, or git history — go read it |
| Mutually exclusive approaches with real trade-offs | Reversible detail with an obvious default (naming, formatting) |
| Destructive / irreversible / outward-facing action needs sign-off | Something you can safely try and undo |
| A required secret / path / credential you cannot discover | A value you can probe for with a tool |

One question at a time when possible. Ask the **minimum** needed to unblock — a
wall of questions is worse than one sharp one.

## Mode 1 — Multiple choice

Use when the useful answers are a small known set. Provide **up to 4** concrete
options, then always add a **5th "Other"** so the user can type their own answer.
Recommend the option you'd pick and put it first, labelled `(recommended)`.

```
<one-line question — what you need decided and why it blocks you>

  1. <option A> (recommended)
  2. <option B>
  3. <option C>
  4. <option D>
  5. Other — type your own answer

回复序号即可，或直接说你想要的。
```

Rules:
- Max 4 real choices + the "Other" line (5 lines total). If you need more than 4,
  the choice isn't framed tightly enough — regroup.
- Make options **mutually exclusive** and self-explanatory; add a short trade-off
  note only when it isn't obvious.
- After sending, **stop**. On reply: a bare number → that option; free text → treat
  as the "Other" answer.

## Mode 2 — Open-ended

Use when answers don't collapse to a short list (a name, a URL, a spec detail, an
opinion). Ask **one** focused free-text question, give a default if a sensible one
exists, then stop.

```
<single focused question>
（如果没有特别要求，我会默认 <sensible default> — 直接说“可以”即可。）
```

## Reply in the user's language

Default to Chinese for the question text unless the user is writing in another
language — then match theirs. See [[user-preferences-and-language]].

## Anti-patterns

| Wrong | Right |
|-------|-------|
| Ask, then immediately guess and keep working | Ask, **stop**, wait for the reply |
| Ask something the files/task already answer | Read first; only ask what you truly can't resolve |
| Dump 6 questions at once | One sharp question; follow up next turn if needed |
| Multiple choice with 8 options | ≤4 options + "Other"; regroup if you need more |
| Proceed with a destructive action "to save a round-trip" | Get explicit sign-off first (Mode 1) |
| Force a free-text question into fake A/B/C when any answer is valid | Use Mode 2 (open-ended) |

## Self-check

- [ ] Could I have answered this myself from files / defaults? If yes, don't ask.
- [ ] Mode 1: ≤4 options, mutually exclusive, "Other" present, recommendation first.
- [ ] Mode 2: exactly one focused question, default offered if sensible.
- [ ] I **stopped the turn** after asking — no guessing past the question.
- [ ] Question is in the user's language, states what it blocks.
