#!/bin/bash
#
# post_restore_modem_check.sh
#
# Runs once after a fresh Clonezilla restore to ensure the modem is
# healthy on first boot. Uses a marker file so it only runs once per
# image restore, not on every subsequent boot.
#
# Deployed to: /scripts/post_restore_modem_check.sh
# Triggered by: modem-fix-postimage.service (systemd oneshot)

set -u

MARKER="/scripts/.post_restore_modem_check_done"
LOG="/var/log/post_restore_modem_check.log"

if [ -f "$MARKER" ]; then
    echo "$(date -Is) - Marker already present, skipping post-restore modem check." >> "$LOG"
    exit 0
fi

echo "$(date -Is) - First boot after restore detected. Waiting 30s for system to settle..." >> "$LOG"
sleep 30

echo "$(date -Is) - Running modem_fix.py --non-interactive..." >> "$LOG"
python3 /scripts/modem_fix.py --non-interactive >> "$LOG" 2>&1
RESULT=$?

echo "$(date -Is) - modem_fix.py exited with code $RESULT" >> "$LOG"

# Touch the marker regardless of result. If the modem has a hardware
# fault, retrying on every single boot won't fix it and will just spam
# the log / delay boot. Hardware failures need manual escalation
# (handled via the normal diagnostic/repair workflow), not automatic
# retries.
touch "$MARKER"
echo "$(date -Is) - Marker created. Post-restore modem check will not run again until marker is removed." >> "$LOG"

exit $RESULT
