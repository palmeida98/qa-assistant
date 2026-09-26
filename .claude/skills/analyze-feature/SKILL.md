---
name: analyze-feature
description: Analyses a feature per role/profile and produces the list of behaviours to test, risks and specification gaps (methodology.md section 5.1).
---

1. Read CLAUDE.md and project.yaml of the current workspace (roles, environments, reference documentation).
   Do not ask again what is already there (methodology.md section 4).
2. Identify the feature's source of truth (requirements, specification, Figma, existing cases)
   and respect the hierarchy of section 1.3. If two sources contradict each other, stop and ask.
3. For each role/profile of the project, say whether it reaches the feature and what it can and cannot do.
4. List the inputs, the states (life cycle), the business rules and the integrations.
5. Assess the risk: probability of failure x impact for the user or for the business.
6. Record the specification gaps as questions or "missing specification" notes.
   Never fill them with assumptions (section 5.1).
7. Produce the list of behaviours to test, each one linked to the role, rule or state it comes from,
   ready for /generate-cases.
8. Present the open questions to the user all at once, at the end.
