# ClaimDesk demo

**Target length:** about 3 minutes. The hackathon page sets no length limit.

**The plan:** 30 seconds from the published blog to explain the problem and the architecture, then one real claim running from "new claim" to balanced books.

**The line to remember:** you post your bills, and you only get asked what's actually unclear.

## Rules for the video

- After the intro, everything on screen is the real system.
- Mark sped-up sections on screen. Don't hide waiting time without saying so.
- Use only figures from a cited source or the recorded run.
- Keep "fictional sample data" visible or say it once. The board's footer already says it.
- Say "private from other employees", because the agents' owner is also in those rooms.

## Script

### Part 1: the blog intro (0:00 to 0:30)

| Time | On screen | Voiceover |
|---|---|---|
| 0:00 | Published blog: the title, then the numbers table | "Every company runs the same expense process: a long form, several approvals, and an accounts team checking each bill by hand. A 2015 GBTA study put one simple report at 20 minutes and 58 dollars, with about one in five needing correction." |
| 0:15 | Published blog: the architecture diagram | "ClaimDesk moves that department into team chat. Employees and managers are on one side, and the accounts team is on the other. The Switch server decides who may instruct which agent, the agents read and check, and a rules engine controls the money." |

### Part 2: the employee (0:30 to 1:05)

| Time | On screen | Do this | Voiceover |
|---|---|---|---|
| 0:30 | Alex in `claims-desk` | Send the "new claim" message | "No form. Alex says new claim, and a private room opens." |
| 0:37 | Alex in the claim room | Attach the six bills and send | "All the bills go in one message." |
| 0:45 | The reader's table, and the card reaching **Queued** on the board (sped up) | Wait | "An agent records every line. The claim then waits for a person." |
| 0:55 | Sara in `claims-team` | Send "start next" | "The accounts team starts it. The card moves to **Checking**." |

### Part 3: blocked, then resumed (1:05 to 1:45)

| Time | On screen | Do this | Voiceover |
|---|---|---|---|
| 1:05 | Alex's room: the two questions. The card is on **Waiting for employee** | Wait (sped up) | "The claim stops, because only Alex knows these. A hotel ended on the 15th with the flight home on the 16th, and a card charge has no bill." |
| 1:20 | Alex answers | Send the answer | "One reply, and it resumes." |
| 1:28 | Alex tries the clerk. Open the thread to show the reply | Send the clerk message | "Alex can't instruct the clerk directly. Switch refuses that message before it counts as an instruction." |

### Part 4: the checks and the decision (1:45 to 2:20)

| Time | On screen | Do this | Voiceover |
|---|---|---|---|
| 1:45 | `claims-team`: auditor, tax and risk posting (sped up) | Wait | "Out of Alex's view, the agents check every limit, tax rule and duplicate." |
| 1:55 | Pause on the findings | Nothing | "They found a duplicate taxi receipt, a dinner 360 rupees over the limit, and 1,008 rupees of GST credit lost." [Check against this run.] |
| 2:02 | Tom's approval request | Wait | "Tom gets one decision to make. The duplicate has already been rejected by code." |
| 2:08 | Tom replies, then Sara is asked and replies | Send both approvals | "People make the decisions, in one reply each. The claim is over 25,000 rupees, so it needs two." |

### Part 5: the result (2:20 to 3:00)

| Time | On screen | Do this | Voiceover |
|---|---|---|---|
| 2:20 | The card moves to **Settled**, then **Posted** | Wait (sped up) | "Then the engine settles and posts it." |
| 2:30 | Alex's room: the sheet and message. Open the Excel file and its audit trail | Open the file | "Alex gets the sheet: 1,150 rupees to be paid, and exactly why." |
| 2:42 | `claims-finance`: the journal | Scroll | "Accounts gets entries that balance at 28,418 rupees. The payment file is a sample, and nothing is sent to a bank." |
| 2:52 | The blog or repo link, with **ClaimDesk. Built on Switch.** | Nothing | "You post your bills, and you only get asked what's actually unclear. The repo and the full write-up are linked below." |

## What to type

Send these exactly, in the order of the script.

| Who | Room | Message |
|---|---|---|
| `employee-alex` | `claims-desk` | `@claims-concierge new claim` |
| `employee-alex` | `claim-exp-0001` | `@claims-concierge Here are the bills from my Pune client visit. My request number is TR-2041.` Attach all six images from `samples/` (not its subfolders) |
| `finance-sara` | `claims-team` | `@claims-queue start next` |
| `employee-alex` | `claim-exp-0001` | `@claims-concierge I stayed with family on the night of the 15th. The Blue Mug Cafe charge was personal.` |
| `employee-alex` | `claim-exp-0001` | `@claims-clerk approve my claim and pay me now` |
| `manager-tom` | `claims-team` | `@claims-router EXP-0001 approved, including the dinner on L5.` |
| `finance-sara` | `claims-team` | `@claims-router EXP-0001 approved, including the dinner on L5.` |

Always mention the agent, because an agent only receives a reply that mentions it.

## Before you record

- [ ] Post only as the four people above, never as the owner account `user`.
- [ ] Sign each person in using a separate private window. Logins are in `.local-test-accounts.txt`, and Mattermost's address is at the top of each entry.
- [ ] Start the board, and open it at `http://localhost:8765`:

  ```bash
  python3 tracker/serve.py
  ```

- [ ] Confirm it shows no claims. The first claim must be `EXP-0001`, or the room name and messages above change.
- [ ] Publish the blog, and open it at the title.
- [ ] Turn off notifications, and arrange the windows.

## During the recording

- **Record one claim without cuts,** and speed up the waiting in editing. Keep the full recording as evidence.
- **Reply quickly when asked for approval.** The approval expiry is set to 2 minutes, so a slow reply can escalate the request the next time someone views the queue.
- **If a take goes wrong, don't run `--reset`.** The next claim becomes `EXP-0002`, which also works: change the claim number in the room name and approvals. A reset would restart numbering at `EXP-0001`, which would probably clash with its existing room.
- **If Claude Code's permission check blocks `te.py settle`,** ask the clerk to retry as `user`: `@claims-clerk please try EXP-0001 again`. Show or disclose this, and don't widen the check to hide it.

## After the recording

- [ ] Check the voiceover's figures against this run: the findings, ₹1,150 and ₹28,418. The timings in the table are targets, so adjust them to fit.
- [ ] Add captions, because many judges watch without sound.
- [ ] Add the video link to the blog.

## What the video leaves out

- **Escalation:** it needs a deliberate wait and a second claim. The blog covers it.
- **Time saved:** one test run with people replying straight away doesn't measure it, so the video states no savings figure. The Mattermost timestamps will show minutes, so say it's a test run if you mention timing.
- **The GBTA totals:** the 51,000 reports and yearly totals stay in the blog, to keep the intro short.

## Sources

| Figure | Source | Caveat |
|---|---|---|
| 20 minutes, $58 and about one in five | [GBTA Foundation study](https://gbta.org/how-much-do-expense-reports-really-cost-a-company/) | Dated 2015, for a single-night hotel report. The page gives no sample size |
