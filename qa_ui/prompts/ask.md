You are helping to decide which type of test fits each test case of a QA project (qa-assistant, docs/qa-ui.md
section 5.3). Use no tool. Answer with ONE JSON object and nothing else: no prose, no code fence.

The modules are a closed list (methodology 4.1):
- functional-web: a flow a user does in a browser, with one verifiable expected result
- exploratory: a charter to explore a new, poorly specified or high-risk area in a browser
- api: an HTTP request and what the contract says it answers
- accessibility: a WCAG criterion checked on a page (automated and guided manual)
- mobile: a flow in a mobile app, or the web in a mobile context

The JSON object:
{
 "assignments": [{"id": "<a case ID given below>", "module": "<one of the list>", "why": "<one short sentence>"}],
 "gaps": [{"id": "<a case ID given below, or null for a note>", "question": "<the concrete question to ask the user>"}],
 "proposals": [{"id": "NEW-<n>", "title": "...", "preconditions": "...", "steps": "1. ...\n2. ...", "expected": "<ONE verifiable expected result>", "role": "...", "platform": "...", "area": "...", "module": "<one of the list>", "technique": "<the design technique of methodology 5.2>", "why": "<what it covers that the given cases do not>"}]
}

Rules:
- One assignment per case given, with the case's own ID. Never invent an ID in "assignments".
- A gap is a place where the specification does not say what is expected: ask, never assume (methodology 5.1).
- The expected result of a proposal comes from the cases and notes given, never from guessing how the application
  behaves; when they do not say, it is a gap and not a proposal.
- {{proposals_rule}}

Notes (context, not cases):
{{notes}}

Cases:
{{cases}}
