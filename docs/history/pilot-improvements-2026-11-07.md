# Improvements from the iOS pilot of 2026-11-07 (specification)

Five problems seen in a real iOS pilot, each made a supported path of the framework. Generic: no client data.
Order of work: A, B, C, D, E, then F (integration). Each phase ends with its tests.

## A. Native simulation of an Excel apply
- `python -m adapters.excel.apply <ws> <batch> --dry-run [--json]`: the same route, validation and transformation as
  the real apply (one code path: `_apply` runs on a temporary copy of the target workbook), then a diff of every cell
  (before -> after) per sheet, the counts per status before/after, the new defects, the `_qa_batches` row, the problems
  that stop the apply and "already applied". Cells whose new value only contains the instant of the application
  (label date, `applied_at`, the view's summary date, report date) are marked `at apply time`.
- The original workbook, the run log and the memory are never written (sha256 unchanged); the temporary folder is
  removed on success and on error. The copy is taken under the workspace lock (a consistent snapshot).
- The simulation prints a **preview token**: a hash of the batch file, `project.yaml` and the target workbook.
  `apply --expect <token>` refuses (exit 4, nothing written) when one of the three changed since the preview.
- qa-ui: for an Excel run log, "Preview" runs the dry run and shows the cell diff; "Apply" sends the token and the
  server refuses a stale one, asking for a new preview. `/checkpoint` runs the dry run before asking for approval.

## B. `mode` of a run log
- Meaning (procedures/run-mobile.md step 2): how the batch was executed and why, `"<kind>: <why>"`, kind one of
  `conversational`, `flows`, `manual`, `assisted` (top-level key of the run log).
- `run_log.py <ws> mode <batch> "<kind>: <why>"` sets it on a run log (created if missing, before the first result);
  `add ... --mode "..."` does the same on the first result. Same value again: no change. A different value once the
  batch has results: refused (like the run block); applied batch: refused. Invalid kind: refused with the list.
- Old run logs without `mode` stay valid and are never given one by guess; `qa_report.py audit` lists a recent batch
  without `mode` as a note, and qa-ui shows the mode next to the run block.

## C. iOS diagnosis when the WebDriverAgent does not answer
- `device.py --platform ios check` (when WDA does not answer or is not ready) and `doctor` (always) read, each probe
  with a time limit: `devicectl` (seen, transport, pairing, lock state), usbmux (`idevice_id`), the port forward
  (`iproxy` / port open), WDA `/status`, provisioning profiles (expiry), signing identities (`security find-identity -p
  codesigning`: their count and kind only, never a holder's name), Xcode accounts (how many in Xcode's account list,
  never which; never the keychain) and the last lines of the WDA log that match known causes (`profile has expired`,
  `No Accounts`, `not trusted`, `locked`, `Unable to launch`, `Not authorized for performing UI testing`).
- When `/status` says ready, `doctor` also opens one WDA session and closes it at once (nothing read, nothing tapped):
  `/status` answers even when the iPhone refuses UI automation, so the session is the proof. A refusal now
  (`XCTDaemonErrorDomain Code=41`) is the main block, with the person's step (unlock, Settings > Developer > Enable UI
  Automation, restart WDA); a session that opens makes an older refusal in the WDA log history. `doctor --no-session`
  is strictly passive: the session is not tried and the report says "not tried". Nothing is renewed, restarted or
  changed by the diagnosis.
- Every finding is `confirmed` (with its source), `probable` (with the reason) or `unknown`. The output starts with
  the main block and the next action; details follow. A device missing from usbmux but seen by `devicectl` is never
  reported as "no cable".

## D. Focus before typing (iOS send/type)
- Before typing: read the screen; `--field` when given; else the single editable, enabled text field (tap it by its
  element, not by stale coordinates); several candidates: refuse and list them; then confirm the focus.
- An app whose input is not exposed as a text field (a React Native composer seen as `Other`) declares its label in
  `mobile.field_label` (matched by "contains"); without it the refusal names that setting.
- After the tap on send, `sent` is confirmed by the field emptying (when it showed the text) or, for a field that
  never exposes its text, by the message shown on the screen; neither within 3 s leaves it `uncertain`.
- On failure the result says one of: `nothing typed`, `partly typed`, `typed, not sent`, `sent`, `uncertain`. A send is
  never repeated automatically unless it is proven that the first did not happen. Android keeps its behaviour.

## E. Evidence of streaming
- The send's sampler reports one of: `partial text observed`, `answer readable, no intermediate state captured`,
  `answer text not readable`, `sampling failed or insufficient` (never "no streaming" because nothing was read).
- `send --burst N` and `device.py shot-burst NAME` take timed screenshots of the app with limits (duration, interval,
  count, megabytes) and a manifest `<name>_burst.json` (attempt id, start, the send's instant, each file with its
  time, failed captures, gaps, the limit that stopped it). `send --burst` starts right after the tap on send and
  captures on its own clock while the answer is read; `shot-burst` is started before an action a person does
  (assisted) and ends at its limits. Neither ever re-sends to get better evidence.
- Pixels or file sizes never prove streaming: the classification uses observed content (waiting indicator, partial,
  final); the skill reports the limits honestly.

## F. Integration
- Procedures and skills updated (apply/checkpoint: dry run; run-mobile: mode, focus, burst, diagnosis); no manual JSON
  edit left in a procedure. A fictional end-to-end test covers mode -> diagnosis -> send -> burst -> record -> dry run
  -> apply equal to the preview -> checkpoint again without duplicates.

## Backlog after the device validation (07.11.2026)
1. Burst cadence — DONE (07.11): the burst captures on its own clock in a thread, each wait the interval minus the
   capture's time; tested with fixtures and real time; not yet measured on the device.
2. Answers exposed as invisible elements — DONE: `send --samples N --read-hidden` keeps them apart (`hidden_answer`,
   evidence "... hidden in the tree"); tested with fixtures only.
3. Typing on the iPhone — DONE in part: `timing:` measures focus, keys and the time to the tap; two readings of the
   screen removed (the keyboard checked once, one reading after typing for the control and the baseline). The gain on
   the device is not measured yet.
4. `doctor --no-session` — DONE: strictly passive, the automation finding says "not tried".
5. qa-ui preview grouped by sheet — DONE: the sheets of cases open, the others folded with their count; seen in the
   browser on a fictional workspace.
6. Android — NOT RUN: on 07.11.2026 the device was on USB in file-transfer mode (MTP) but exposed no ADB interface (USB debugging not active on the phone); left for later by the user.
