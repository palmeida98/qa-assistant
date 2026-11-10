# Procedure: find the announced build in Slack (read only)

Phase B, 2026-11-10. It identifies candidates for "latest available" in an authorised channel and compares them with the build
**observed** on the device. It does not install, update, downgrade, download APKs or write to Slack.

## Scope and permissions (the user's decision, per project)
- Only the channels and the date window the user authorised (for example one named channel and 30 days by default). Other channels, DMs and
  private channels that were not authorised are not read.
- The connector acts as the user and its OAuth scopes are neither visible nor changeable by the agent. The block is **local**
  (Claude Code permission rules) and does not limit the connector outside that configuration.
- Before use: `deny` the connector's write tools (messages, scheduling, drafts, reactions, channels, canvases, lists, file
  uploads) and set the read tools to `ask`. Check that the write tools are no longer available. Never test the `deny` by calling
  a write tool.
- Prefer `slack_read_channel` with `oldest` and the channel id. Avoid `search_public_and_private` and `read_file`.

## Flow
1. Confirm the connected account and the access to the channel (profile and channel search). If it fails, stop and ask.
2. Read the channel for the window, **every page** (the page order can be unexpected; follow the cursor to the end).
3. Re-read each candidate message in the `detailed` format to get its **exact `Message TS`** (the `concise` format has none). An
   approximate TS is never an identifier. Also read the threads of the candidate messages and the bots' notifications.
4. Save the text read to a scratchpad file and run:
   `python scripts/slack_build_messages.py raw.txt --channel <id> -o messages.json`
   (it drops names, e-mails and user ids; messages without an exact TS are skipped and listed).
5. `python scripts/build_candidates.py messages.json --observed "<release> (<build>)" --platform <android|ios> --deployments`.
6. Record the result under `analysis/` (or wherever the project decides) with: channel, exact TS, date, classification and
   limitations.

## Four things that are never mixed up
| What | Accepted source | Never |
|---|---|---|
| **Announced release / build** | a message that states the release and the build number | use the pipeline release id as the build number |
| **Pipeline deployment** (`Release-N`, "Deployment on stage … succeeded") | the bot notification: shows that the pipeline published to an environment | treat it as the app build, or tie it to a number without an explicit source |
| **App distribution** (APK, Firebase, TestFlight) | a message or link that announces it | assume a backend deployment is an app distribution |
| **Installed build** | `device.py check` on the device, in the session | infer it from a message |

Platform: a message that does not state the platform (or that talks about a fix for a single platform) keeps the platform "to
confirm". Production is "to confirm" by default.

## Limitations (always record them)
- A search in one channel does not prove that there are no announcements: images and files, threads that were not read, DMs,
  other channels, TestFlight/Firebase and e-mail are outside it.
- The connector returns neither permalinks nor the workspace domain: a message is located by channel + TS.
- Edited or deleted messages, and the content of bot attachments, may not come back complete.
- No message proves that a specific change (FE/BE) is included in a build.
- iOS through TestFlight cannot be queried by the agent.
