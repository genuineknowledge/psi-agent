---
name: html-sanitization
description: "Building an HTML/XSS sanitizer: a filter that strips JavaScript/active content from HTML so no script executes in a browser while benign markup is preserved. Use when the deliverable is an HTML cleaner/XSS filter (NOT regex codegen, NOT an attack/exploit)."
---
# HTML sanitization / XSS filtering

You are building the DEFENDER (a sanitizer), not an attacker. The single most
common reason this task fails: people reach for regex `re.sub` "surgical
substring removal" to preserve formatting. That is the WRONG instinct here and
loses. Read this fully before writing code.

## Step 0 — read THIS task's own checker first (do NOT guess vectors)
- Find and READ the task's provided test/checker file and instructions, then
  extract ITS literal test cases and exactly how it scores. Do not invent your
  own vector list or assume a scoring contract — confirm it from the task's own
  files. The agents that fail all skip this and self-test against a handful of
  made-up samples that always "pass".
- Sanitizer graders generally enforce two independent properties, and you should
  expect to satisfy BOTH (verify the specifics against the checker you just read):
  1. **Safety / execution** — the cleaned HTML must not cause script to run.
     A serious grader will check actual execution semantics (e.g. rendering in a
     real browser), not just whether the literal string `<script>` is gone — so
     defend against execution, not just textual patterns.
  2. **Preservation** — benign HTML must be left intact. Note that "intact" is
     usually judged against a PARSER-NORMALIZED form, not the raw bytes; read the
     checker to see exactly what it compares your output to.

## The winning insight (this is the crux)
- Do NOT try to preserve raw bytes with brittle regex edits. A robust sanitizer
  PARSES the HTML with a real parser and RE-SERIALIZES it
  (`str(BeautifulSoup(html, "html.parser"))`). This lands your output on the
  parser's canonical normalized form (entity decoding, `/` on void elements,
  attribute ordering) — which is almost always what a preservation check expects,
  so a `parse -> sanitize the tree -> str(soup)` pipeline tends to satisfy
  preservation BY CONSTRUCTION instead of fighting it with regex. (Confirm the
  expected form against the task's checker per Step 0.)
- On the SAME parsed tree, removing dangerous nodes/attributes handles the
  safety property. One pipeline, both properties.

## Architecture: BeautifulSoup as the framework, regex only as a helper
- `soup = BeautifulSoup(open(path).read(), "html.parser")`, sanitize in place,
  then write `str(soup)`.
- BeautifulSoup does the structural work: walk the DOM, `.decompose()` dangerous
  elements, delete attributes, re-serialize.
- regex is a HELPER inside that, only for value-level checks (URL scheme /
  entity / obfuscation detection), never as the top-level filter.

## What to remove (layered defense — each layer catches what the prior misses)
1. **Dangerous elements**: `.decompose()` every `script`, `object`, `embed`,
   `applet`, and unknown executable tags. Handle case/whitespace via the parser
   (it already lowercases tag names and tolerates `< script >`, tabs, `<<script>`).
2. **Event-handler attributes**: delete any attribute whose name matches `^on`
   (onerror/onload/onclick/onfocus/onmouseover/...). Iterate over a COPY of
   `tag.attrs` keys.
3. **Dangerous URL schemes in href/src/action/formaction/xlink:href/data/etc.**:
   normalize the value first (decode HTML entities like `&#106;`/`&#x09;`,
   strip whitespace/null bytes), then reject if it resolves to `javascript:`,
   `vbscript:`, `data:text/html`, etc. Iteratively strip obfuscation keywords
   (`javajavascriptscript:` collapses to `javascript:`) and re-check.
4. **`<meta http-equiv="refresh">` with a javascript/active URL, and active
   `<iframe>` srcdoc/data: payloads** — remove the element or neutralize the URL.
5. **Comment-node smuggling**: BS4 parses `<!-->X<script>...` as a single
   `Comment` node, so element-walking misses it. Separately walk
   `soup.find_all(string=lambda s: isinstance(s, Comment))` and `.extract()` any
   comment whose text contains a dangerous tag/scheme/handler pattern. Pure-regex
   filters miss this entirely — it's a frequent decider.

## Self-test discipline — reproduce the checker, don't invent
- Self-test against the checker's OWN vectors (read in Step 0), not a list you
  made up. A self-built mini-harness that always "passes" is the classic
  false-positive trap that ends in reward=0.
- A local browser is NOT required to validate: a fast closed loop is (a) feed
  each known malicious vector through your filter and `grep -i` the output for
  any residual `javascript:`/`vbscript:`/`on...=`/`<script`/`<object`/`<embed`/
  `data:text/html`; plus (b) byte-diff your output for the clean samples against
  the parser-normalized form `str(BeautifulSoup(original, "html.parser"))`. If
  selenium+chromium ARE available, rendering the actual vectors is the strongest
  execution check.
- Do NOT call task_complete while ANY vector still fires or any clean sample
  differs. Iterate until both properties hold across the full set.

## Anti-patterns that cause reward=0 here
- Deciding early "a parser would reformat the HTML, so I must use regex" — this
  is usually BACKWARDS: the parser's normalized form is typically what a
  preservation check expects. If you catch yourself building an `re.sub` pipeline
  as the main filter, STOP and pivot to a real parser.
- Trusting `<[^>]+>`-style tag regexes: they leak on `<iframe////onload=...>`,
  backtick-delimited attrs, run-together attributes, and comment-hidden scripts.
- Declaring done after your own handful of samples pass without ever reading or
  reproducing the grader's actual test cases.
