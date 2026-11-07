#!/bin/sh
# Install the rules engine for ClaimDesk's agents.
# Run this after creating the rooms from claimdesk.template.yaml.
#
#   ./install.sh [--team claims] [--employee employee-alex] [--approver manager-tom]
#                [--finance-head finance-sara] [--expiry-minutes N] [--solo] [--reset]
#
# --expiry-minutes  how long an approval waits before it escalates (default 1440)
# --solo            one person plays employee and approver, which allows self-approval
# --reset           deletes all cases and the ledger, and restores the sample data
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
team=claims
setup_args=""
while [ $# -gt 0 ]; do
  case "$1" in
    --team) team=$2; shift 2 ;;
    --employee|--approver|--finance-head|--expiry-minutes) setup_args="$setup_args $1 $2"; shift 2 ;;
    --solo|--reset) setup_args="$setup_args $1"; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

python=python3
"$python" -c 'import sys' >/dev/null 2>&1 || python=/usr/bin/python3

# shellcheck disable=SC2086
"$python" -B "$here/engine/te.py" setup $setup_args

installed=0
for server in "$HOME"/.switch/agents/*/; do
  for agent in concierge reader queue journey auditor tax risk router clerk accountant; do
    folder="$server$team-$agent"
    if [ -d "$folder" ]; then
      cp "$here/engine/te.py" "$here/engine/build_expense_sheet.py" "$folder/"
      # Machines using asdf have no default python3 until a version is set.
      echo "python system" > "$folder/.tool-versions"
      case "$agent" in
        # Claude Code's permission check can read "settle" and "post" as real-world
        # transactions. They only calculate and write local files, and the engine
        # refuses them until approvals are recorded. Allow exactly these commands
        # for the two agents that run them; everything else stays checked.
        clerk|accountant)
          mkdir -p "$folder/.claude"
          cat > "$folder/.claude/settings.json" <<'JSON'
{
  "permissions": {
    "allow": [
      "Bash(python3 te.py settle:*)",
      "Bash(python3 te.py sheet:*)",
      "Bash(python3 te.py post:*)",
      "Bash(/usr/bin/python3 te.py settle:*)",
      "Bash(/usr/bin/python3 te.py sheet:*)",
      "Bash(/usr/bin/python3 te.py post:*)"
    ]
  }
}
JSON
          ;;
        # The check can also block moving an overdue approval up, as a change to a
        # shared resource. The command only follows the policy's escalation rule,
        # and can't approve anything or move money.
        queue)
          mkdir -p "$folder/.claude"
          cat > "$folder/.claude/settings.json" <<'JSON'
{
  "permissions": {
    "allow": [
      "Bash(python3 te.py escalate:*)",
      "Bash(/usr/bin/python3 te.py escalate:*)"
    ]
  }
}
JSON
          ;;
        # The concierge is open to anyone, so it can't run the commands that change
        # a claim's money or approvals, however a message is worded.
        concierge)
          mkdir -p "$folder/.claude"
          cat > "$folder/.claude/settings.json" <<'JSON'
{
  "permissions": {
    "deny": [
      "Bash(python3 te.py decide:*)",
      "Bash(python3 te.py settle:*)",
      "Bash(python3 te.py post:*)",
      "Bash(python3 te.py next:*)",
      "Bash(python3 te.py add-bills:*)",
      "Bash(python3 te.py resolve-card:*)",
      "Bash(python3 te.py setup:*)",
      "Bash(/usr/bin/python3 te.py decide:*)",
      "Bash(/usr/bin/python3 te.py settle:*)",
      "Bash(/usr/bin/python3 te.py post:*)",
      "Bash(/usr/bin/python3 te.py next:*)",
      "Bash(/usr/bin/python3 te.py add-bills:*)",
      "Bash(/usr/bin/python3 te.py resolve-card:*)",
      "Bash(/usr/bin/python3 te.py setup:*)"
    ]
  }
}
JSON
          ;;
      esac
      installed=$((installed + 1))
    fi
  done
done

if [ "$installed" -ne 10 ]; then
  echo "Installed for $installed of 10 agents. Create the rooms first, or check --team." >&2
  exit 1
fi
echo "Installed the engine for all 10 $team agents."
