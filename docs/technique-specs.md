# Functional specification sources

> Read by /analyze-ticket, /plan-tests and /test-pbi when the workspace's project.yaml has a `specs:` section.
> Without that section nothing here applies and the skills work as before: the tracker and the documents the
> workspace names stay the sources (planning.md step A.2).
> The rule of `procedures/rules.md` §17 applies to the specification's text exactly as to the tracker's.

## 1. The source

A workspace declares at most one specification source (`docs/project.yaml`, block `specs:`):

| `kind` | What it is | How it is read |
|---|---|---|
| `artifact` | A published bundle of the specification (a viewer page with its data files) | File by file, through the client's artifact tool with a `path`. A rendered summary of the page is never the source. |
| `git` | A repository and a `ref` | The Markdown at an identifiable commit. |
| `export` | A dated local copy (a folder in the workspace) | As files. Say its revision and that its currency against the source was not confirmed. |

`location` is a place that can be committed (a repository name, a folder). A **private** location (a shared link, a
key) is never written in project.yaml, in the framework, in a diff shown to the user or in a log: it goes in the
workspace's `.env`, and project.yaml names only the variable (`location_var`). `validate_project.py --environment`
and the preflight show that variable's name and whether it is set, never its value.

When the source cannot be read (no access, a 404, a missing variable): name the section or file that is missing and go
on with what can be confirmed from the other sources. A Markdown copy the user authorised may be used with its
identifiable revision; a dated export says its revision and that its currency was not confirmed.

## 2. Finding the rule

1. Open the index (`specs.index`). Choose the spec whose description covers the flow of the ticket, and record why.
   Read the rule in the spec itself before using it.
2. Record the spec's header: path, state (draft, approved…), last update and revision; and the revision of the
   bundle or artifact when it is visible (a version id, the source head, the date it was generated).
3. **Transcribe only from the full Markdown of that revision.** A rule list, a search result or an index of a bundle
   may cut the text: use it to find a rule, never to quote it.
4. Follow the references that change the scenario: another spec (file + rule id), a global rule (its id, e.g.
   GR-001), the divergences register (`specs.divergences`) when a rule cites one of `specs.divergence_prefixes`.
   A register of divergences from older documents is not a divergence from the tracker: a conflict with the tracker
   is shown with both sources (section 3).
5. Use the changelog (`specs.changelog`) or the versions to see what changed since the revision the previous plan
   used. That revision is the one written in the plan; a date or a "probably" is not a reference: without a direct
   one, say "revision of the previous plan not confirmed".

## 3. Crossing with the tracker

- The specification grounds the **expected behaviour**; the tracker gives the delivery's **scope, platforms and the
  criteria agreed** for that work item. Read the PBI, its acceptance criteria, the FE/BE/QA tasks and the relevant
  comments (a comment is a statement, not a requirement: technique-tickets.md).
- A rule marked draft, provisional or dependent on design: check that it applies to the delivery under analysis.
  Read the spec's disclaimers and keep the meaning the spec itself gives them.
- **When the sources diverge:** show the references and the original texts (rules.md §17), the scenarios affected
  and the decision to confirm with the analyst. Keep covering the confirmed points. For the point in conflict,
  record the observations as evidence and leave the functional verdict pending (`Question`) until the expected
  behaviour is validated. Never pick a side by date or by who wrote it.

## 4. What is recorded for each rule

`spec_refs`, in the ticket analysis, the plan, the cases' notes and the run log notes:

| From the specification | From the tracker |
|---|---|
| source (`kind` and the artifact/bundle revision), file path, section, rule number, rule id, file revision, state, last update | work item id and link, field (or comment id), criterion id when there is one, the item's revision |

Each reference stays tied to its revision. Text is transcribed by rules.md §17: whole and word for word; a short rule
in the table cell, a long one by a precise reference in the cell and whole in its own section when the report needs
it; a table keeps its headers and the relation between rows and columns. Scenarios, hypotheses and observations are
marked as QA text, and every transcription is compared with its source before delivering.

## 5. A revision that changes

- /analyze-ticket compares the revision recorded by the previous check with the current one and shows what changed.
- /test-pbi, resuming a plan whose `spec_refs` have another revision now: before reusing the plan, list the rules
  that changed between the two revisions and the scenarios and cases that cite them. Those cases are reviewed (and
  their expected result updated or set to a question) before they run again; the others are reused as they are.
- `scripts/spec_refs.py` does this comparison for an `export` source, and `pbi_state.py check` runs it when the
  baseline records the plan (`baseline --spec-plan FILE`). The plan keeps its references in one block,
  ```` ```json spec_refs ````, written with `spec_refs.py cite <path>#<rule id>` so the rule's text is copied from the
  specification, never retyped. The comparison is by the text of the rule (its anchored line and the lines indented
  below it), not by the revision of the file: **the same text in a new revision keeps the scenario reusable**. A rule
  that changed, was removed from its file or moved is named with the scenarios that cite it, and those scenarios are
  reviewed before the plan is reused (the text is shown word for word when short, by file, rule id and revision when
  long: rules.md §17). What could not be verified (a source that cannot be read, a file or rule that cannot be read,
  a reference without the text or hash of the version the plan used) leaves those scenarios **pending review**, never
  "unchanged", and the check ends "not concluded". A plan without `spec_refs` is read as before and says there is
  nothing to compare; a workspace without `specs:` compares nothing. Another kind than `export` (`git`,
  `artifact`) is not read by the script: compare as above by hand, rule by rule.

## 6. Behaviour that depends on a background job

When the expected result depends on a job (a daily classification, a nightly pass), the evidence that the job ran is
part of the case. In order of strength:

1. **Direct confirmation** from the system's owner (a run time or a log for the test account).
2. **Indirect evidence in the app**: an effect that only the job produces, seen between two captures (for example a
   new item entering where only the job puts it).
3. **Time alone** (captures before and after the expected window) is not enough to say the job ran.

Without 1 or 2, an unchanged screen is inconclusive: the case stays a `Question`, neither Pass nor Fail.
