## QA run: {{batch}} ({{run_id}})

Build **{{build}}** · environment `{{environment}}` · {{mode}}
Time: {{minutes}} of {{timeout}} minutes · agent turns: {{turns}} of {{max_turns}} · [the run]({{run_url}})

| Status | Cases |
|---|---|
{{totals}}

### Proposed bugs

Each one is a proposal (`"proposed": true`, methodology 9.4). To approve it, remove `"proposed"` from its
`new_defect` in the run log; to reject it, delete its `new_defect` (or replace it with `"bug": "<id>"` of an existing
bug).

{{proposed}}

### Questions

Answer each one by setting the case's final status (and its `actual_result`) in the run log.

{{questions}}

### Checks before the commit

{{checks}}

### The agent's summary

{{summary}}

---

Review the run log `{{run_log}}` and the evidence in this pull request, edit the run log here, then merge: the merge
is the approval, and `qa-apply.yml` applies the run log with the project's adapter. A run log with `"proposed"` or
`Question` left is never applied.
