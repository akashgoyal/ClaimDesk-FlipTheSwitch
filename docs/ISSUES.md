# Issues faced

Problems found while building ClaimDesk, on Switch Console 0.40.1 with core 0.31.0, macOS (Apple silicon), Claude Code and the bundled Mattermost.

## Still open

| Issue | Effect | Current handling |
|---|---|---|
| Claude Code's `auto` mode can block `te.py settle` as a real-world transaction, despite the allow rule | It passed on `EXP-0001` and was blocked on `EXP-0002`, so that claim wasn't settled | The clerk stops and reports it. Ask it to retry. Not fixed |

## Problems in Switch

None of these have been reported upstream yet, and they may already be fixed on `main`.

### 1. `{$creator}` gives different names in different places

**Seen:** in one template, `{$creator}` became `user` (the Mattermost username) in room instructions and `Admin` (the account name) in agent instructions.

**Effect:** agents told "the approver is `{$creator}`" and the room's own instructions named two different people. The auditor noticed and asked which one decides.

**Workaround:** use a string param for the name.

### 2. A `type: user` param never reaches agent instructions

**Seen:** a version 2 template with `approver: {type: user}` used in agent instructions is refused, even with the value supplied:

```text
its instructions needs approver, which has no value; give it in inputs. (input_missing)
```

**Reproduced:** with `plan_template` from the installed core, using `approver=user` as an input. Changing only the param to `type: string` makes it pass.

**Workaround:** use `type: string`.

### 3. Activity cards fail to post in Mattermost

**Seen:** the core log repeats this while agents work, though the agents carry on:

```text
Mattermost post /posts failed: Invalid RootId parameter.
Could not post the activity for turn ...
```

**Effect:** not confirmed. It probably only loses the live activity display.

### 4. Online docs describe newer code than the Console ships

**Seen:** `docs/TEMPLATES.md` on `main` documents a room `docs:` field, which the 0.31.0 template guide doesn't list.

**Workaround:** check the code installed with the Console, or the docs at the `switch-v0.31.0` tag.

### 5. The computer stays connected to a deleted server

**Seen:** after a server reset, the agent controller was still enrolled on the old server, so the create form offered no machine.

**Workaround:** remove Switch and reinstall, then choose **Run managed agents on this computer**.

### 6. No timer for agents

**Effect:** nothing wakes up when an approval goes overdue.

**Workaround:** overdue approvals are checked whenever someone views the queue.

### 7. Channels couldn't be deleted from the Console

**Effect:** old rooms stayed in the way, including the `claims` names a fresh create needed.

**Workaround:** reinstall Switch. Don't reset claim numbers on a setup you've already run, because `EXP-0001` would probably clash with its existing room.

## Problems in the build

### Setup

| Issue | Cause | Fix |
|---|---|---|
| `claude` failed outside a project | asdf had no global Node version | Set Node 26.5.0 globally |
| switch-buddy was never created | That failure, plus an interrupted sign-in | Created an agent by hand |
| `python3` failed for agents | asdf had no version in the agents' folders | `install.sh` adds a `.tool-versions` file to each one |
| `install.sh` failed from the home folder | It was run from the wrong place | Ran it from the project folder |
| The Console form suggested the invalid prefix `te 2` | The default prefix `te` was taken | Changed the default prefix |
| The app's preview runner couldn't read the Documents folder | A macOS restriction | Checked the board from a scratch copy |
| Shell mistakes printed false success lines twice | A command in a variable, and an unquoted `?` in a URL | Re-ran both properly, and checked results directly |

### Template

| Issue | Cause | Fix |
|---|---|---|
| The template was refused twice | Docs describe newer code, and the server needs format version 2 | Used format version 2 |
| Agents would have seen broken JSON | `{{ }}` isn't an escape in templates. The server's planner caught it | Single braces |
| Rooms reported as created didn't exist | The old template was used | Checked the logs, then kept one final template |
| `claims-team` and `claims-finance` were public, so employees could have joined | The default channel type | `channel_type: channel_private` in the template |
| An agent's reply from a private room was blocked | Its open access covered only the desk room | Opened the concierge's access to anyone |

### Engine and agents

| Issue | Cause | Fix |
|---|---|---|
| An approver could approve a duplicate with "approve the rest" | Duplicates were only flagged | Duplicates are rejected automatically |
| The budget figure was ₹1,638 over, not ₹1,218 | It included the duplicate | Excluded duplicates |
| The report's categories were ₹3,200 short. An agent noticed | Per diem was left out | Included per diem |
| Old cases would have flagged the sample bills as already paid | Duplicate checks look at past claims | Moved old case data aside before testing |
| The accountant re-ran `settle` after posting, adding a stray audit entry | Nothing stopped it | The engine refuses `settle` after posting |
| The permission check blocked `te.py settle` | It read it as a real-world transaction | With approval, allowed only `settle`, `sheet` and `post` for two agents. See "Still open" |
| The permission check blocked `te.py escalate` | It read it as a change to a shared resource | With approval, allowed only that command for that agent |
| The queue agent reported "blocked" without retrying | It relied on the earlier failure | The template tells it to retry |
| The kickoff could run commands before the engine existed | The engine is installed after the rooms | The kickoff no longer runs commands |

## Design changes

| Considered | Decision | Why |
|---|---|---|
| A CSV, email, form and expiring-link tool | Rejected | It would push Switch out of the project |
| One permanent room per employee | Rejected | A private room per claim is simpler, and needs no setup per person |
| True direct messages from agents | Not possible | Agents can't start them, but they can create a private room with one person |
