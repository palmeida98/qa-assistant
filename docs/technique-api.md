# API technique

> **Layer 2 — Technique for testing APIs (HTTP/REST)**
> Version 1.1 · 2026-09-26 · Plan, task 3.1; in English since plan 8.11.
>
> Describes **how** the assistant tests an HTTP API. It is project agnostic: it contains no URLs, accounts, tokens nor rules of any client. The examples use the fictional project **Demo Shop**.
>
> Subordinate to `methodology.md`: the decision, recording, bug and checkpoint rules are there and are not repeated here.

---

## 0. Scope

- HTTP APIs (REST/JSON in most cases): contracts, status codes, authentication and authorisation per role, input validation, idempotency, persistence, pagination and limits.
- Serves the `api` module and is used by the `functional-web` module to prepare data or confirm persistence (`technique-browser.md` §14).
- It does **not** cover load tests nor active security tests (fuzzing, injection, brute force). See §11.
- The concrete values (base URL, credential variables, contract) come from the workspace: the `api:` section of `project.yaml`.

---

## 1. Execution levels

Always use **the lowest level that allows the expected result to be checked**.

| Level | Tool | Serves for | Does not serve for |
|---|---|---|---|
| **N0 · Single request** | `scripts/api_request.py` (or `curl`) | One request, one status code and one body to check; availability; the contract of an endpoint | Flows with state between requests |
| **N1 · Sequence by the agent** | `api_request.py` chained by the agent | Flows with state (create → read → change → delete), authentication, role matrix, persistence | Repeated regression in large volume |
| **N2 · Deterministic** | Playwright specs (`request`) in the workspace | Regression and smoke in CI | New or unstable endpoints |

`api_request.py` exists so that **no secret reaches the evidence**: it replaces `{{VARIABLE}}` with the value from `.env` only at the moment of the request and redacts the output (§9). Postman collections the project already has are used as a source of cases and data; they are not rewritten.

---

## 2. Preparing the session

1. Read the `api:` section of `project.yaml`: base URL of the test environment, type of authentication, credential variables per role, contract.
2. **Confirm the environment.** The base URL must be that of the declared test environment. If it looks like production, or does not match, **stop and ask**.
3. Confirm that the credential variables exist in `.env` (only that they exist; never show the value).
4. Identify the **contract's source of truth**, by the hierarchy of methodology §1.3: approved specification (OpenAPI, published documentation) → existing cases → observed behaviour (never a source of the expected result).
5. One availability request (health check, if there is one) before the batch. If it fails, the batch is `Blocked` (environment).

---

## 3. Authentication and authorisation per role

- Credentials and tokens come **only** from environment variables; they never appear in logs, notes, bugs or messages (§9).
- Get one token per role and per batch, and reuse it while it is valid.
- For each protected operation, the minimum matrix is:

| Request | Typical expected result (confirm in the specification) |
|---|---|
| no credentials | refused: 401 (or 403) |
| invalid credentials or expired token | refused: 401 |
| role without permission | refused: 403 (or 404, if the project hides the existence) |
| role with permission | accepted |
| **another** user's resource (IDOR) | refused, without revealing data |

- A refused request is also confirmed by its effect: the resource stays **unchanged** (read it again with an authorised role).

---

## 4. Contract and schemas

Check, per endpoint and per documented response code:

- **Status code** exactly the documented one (201 is not 200).
- The response's **`Content-Type`**.
- **Required fields** present, with the right **type** (a number is not text), format (dates, IDs) and valid enumeration values.
- **Echo of the data**: what was created or changed comes back equal to what was sent, except fields the server calculates.
- Extra fields are not Fail, unless the contract forbids them (record as an observation).

When the specification and the implementation diverge, the expected result is the specification's (methodology §5.3). When the specification **says nothing** (e.g. it does not document error responses), nothing is invented: the case only goes in if there is another objective source (HTTP standard, RFC 9110, a general requirement of the project) and that source is written in the case; otherwise it goes as a question (methodology §5.1).

---

## 5. Negative cases

Per endpoint that receives data, by the techniques of methodology §5.2:

- required fields missing, one by one (equivalence partitioning);
- wrong types (text in a number, a number in a boolean);
- boundary values (empty, maximum size, impossible dates, negatives);
- empty body, malformed JSON, wrong `Content-Type` (415), method not allowed (405), resource that does not exist (404).

Base rule of the HTTP standard: a client input error answers **4xx**; a **5xx** to an invalid input is Fail (the server did not validate), even if the specification does not document the error. The case cites the source (RFC 9110 §15.5 and §15.6).

---

## 6. Idempotency, effects and persistence

- `GET`, `HEAD`: no effects; repeating gives the same result.
- `PUT`, `DELETE`: idempotent; repeating neither creates nor breaks anything (the code of the repetition may differ, e.g. 404 on a second `DELETE`; confirm in the specification).
- `POST`: repeating creates another resource, unless the contract provides an idempotency key (test it when it exists).
- `PATCH`: changes only the fields sent; the others stay the same.
- **Persistence**: when the case claims something was created, changed or deleted, confirm it with a `GET` afterwards (and not only by the response's code).

---

## 7. Lists, pagination, filters and limits

- Filters: the result contains what it should (the resource created in the test) and does not contain what it should not.
- Pagination: first page, last, empty page beyond the end, maximum size; no item repeated or lost between pages.
- Sorting: check the order asked for, not the one that looks natural.
- Rate limits: confirm they exist only if the specification documents them, with the **smallest** number of requests that shows it. Never hammer the API (§11).

---

## 8. Verification and classification

The decision rules are those of methodology §8. API particulars:

| Situation | Status |
|---|---|
| Code, body and effect equal to the expected result | Pass |
| Code or body different from the documented one, with evidence | Fail |
| 5xx to an invalid input | Fail (§5) |
| Timeout, 502/503/504, DNS, TLS | Blocked (environment); one retry (`technique-browser.md` §9) |
| Endpoint not available to the role, by definition | N/A |
| Specification silent about the expected result | Question → ask |

The response time is only a criterion when the specification sets it; otherwise it stays as an observation.

---

## 9. Evidence (with secrets redacted)

A **Fail** records enough request and response to reproduce:

- method, URL (without tokens in the query string), relevant headers, body sent;
- code, relevant headers and body received (truncated if large);
- response time and date/time;
- expected → observed.

**Mandatory redaction**, done by `api_request.py` before anything is written:

- headers `Authorization`, `Proxy-Authorization`, `Cookie`, `Set-Cookie`, `X-Api-Key` and the like → `«redacted»`;
- JSON keys whose name indicates a credential (token, password, secret, API key; the exact list is `SECRET_KEY` in `scripts/common.py`) are redacted;
- any literal occurrence of the value of a `.env` variable → `«VARIABLE_NAME»`.

Never copy into the evidence the value of a token "just to show it existed": record that it exists and its length.

---

## 10. Test data

- Fictional data, with the session's identifiable prefix (`QA-AUTO-<date>-`), as in the browser (`technique-browser.md` §7).
- Record in the run log the resources created (type + ID) and delete them at the end of the batch when the project allows it; what is left is listed in `context.md`.
- Never change nor delete resources the test did not create.

---

## 11. Security and limits

- Only the base URL declared as the test environment, with the authorisation of the responsible team.
- No load, stress nor volume tests; no massive fuzzing; no brute force of credentials.
- Respect rate limits: pauses between requests when the API announces them.
- Authorisation tests (§3) are done only with the declared test accounts, never with real people's accounts.
- If a request could have effects outside the test environment (e-mails, payments, external integrations), **stop and ask**.

---

## 12. Promotion to regression (N1 → N2)

The same criteria as `technique-browser.md` §13. In the spec: Playwright's `request`, one file per resource, the case ID in the title, base URL and credentials from environment variables. The specs live in the workspace, not in `qa-assistant`.

---

## 13. Link to the methodology

| Methodology | In this technique |
|---|---|
| §5 case design | §4 contract, §5 negatives, §3 role matrix |
| §7 run log | one item per case, with `actual_result` = code + summary of the body |
| §8 decision rules | §8 |
| §9 bugs | evidence §9, always redacted |
| §1.2 privacy | §9 and §11 |

---

## 14. Checklist per batch

**Before**
- [ ] Base URL confirmed against `project.yaml` (test environment)
- [ ] Credential variables present for the batch's roles
- [ ] Contract's source of truth identified
- [ ] Health check OK

**During**
- [ ] Every request through `api_request.py` (redacted evidence)
- [ ] Persistence confirmed with a `GET` when the case claims it
- [ ] Resources created recorded
- [ ] The specification's omissions recorded as Question, without an invented expected result

**After**
- [ ] Test resources deleted or listed in `context.md`
- [ ] No secret in the run log, in the bugs or in `context.md`
- [ ] N2 candidates identified
