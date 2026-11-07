#!/bin/bash
# Create the local Mattermost accounts that play the people in the demo, before
# you create the rooms: two employees, a manager and a finance head. It works with the
# Mattermost bundled with Switch Console. Passwords go in a git-ignored file and
# aren't printed.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
secrets="$here/.local-test-accounts.txt"
container=$(docker ps --format '{{.Names}}' | grep -m1 'mattermost' || true)
if [ -z "$container" ]; then
  echo "Bundled Mattermost isn't running. Start Switch Console's local server first." >&2
  exit 1
fi
mm() { docker exec "$container" mmctl --local "$@"; }
port=$(docker port "$container" 8065/tcp | head -1 | sed 's/.*://')
team=$(mm team list 2>/dev/null | grep -v '^There' | head -1)

create() {
  username=$1; first=$2; last=$3
  if mm user search "$username" 2>/dev/null | grep -q "^username: $username\$"; then
    echo "$username already exists."
  else
    password="Te$(openssl rand -hex 9)x7"
    mm user create --username "$username" --email "$username@localhost.local" \
      --password "$password" --firstname "$first" --lastname "$last" >/dev/null
    mm user verify "$username" >/dev/null
    # Appended, so earlier accounts' logins are kept.
    (umask 077; printf '\nLocal Mattermost test login\nURL: http://localhost:%s\nUsername: %s\nPassword: %s\n' "$port" "$username" "$password" >> "$secrets")
    echo "Created $username. The password is in .local-test-accounts.txt, in the repo root."
  fi
  mm team users add "$team" "$username" >/dev/null 2>&1 || true
}

create employee-alex Alex Mehta
create employee-maya Maya Iyer
create manager-tom Tom Clark
create finance-sara Sara Khan
echo "All four accounts are on team $team."
