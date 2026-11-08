#!/usr/bin/env bash
set -euo pipefail
# Run on the separate mail VPS, not on InstaBox.
# Usage: sudo ./provision-mailbox-docker-mailserver.sh alice@mail.example.net
MAILBOX="${1:?Usage: $0 user@domain}"
if [[ ! "$MAILBOX" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; then
  echo "Invalid email address" >&2; exit 2
fi
echo "This creates a mailbox on the mail host. You will be prompted for its password."
docker exec -it mailserver setup email add "$MAILBOX"
echo "Mailbox created. Now create the matching mailbox record in CustomMail admin."
