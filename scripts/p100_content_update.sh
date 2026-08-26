#!/bin/bash
#
# p100_content_update.sh
#
# Automates the manual content update workflow for a P100 unit:
#   autopilot off -> autopilot ssh -> docker-compose stop flight-controller ->
#   airsync sync --force-reset -> filesync run -> filesync clean ->
#   docker-compose up -d flight-controller -> autopilot on
#
# Run this ON the P100 unit itself (via SSH), not from your laptop.
#
# Usage:
#   sudo ./p100_content_update.sh
#
# Recommended: run inside tmux so it survives a dropped SSH/VPN session:
#   tmux new -s content_update
#   sudo ./p100_content_update.sh
#   (Ctrl-b d to detach, tmux attach -t content_update to reattach)

set -u

LOG="/var/log/p100_content_update.log"
SRV_DIR="/immfly/srv"

log() {
    echo "$(date -Is) - $1" | tee -a "$LOG"
}

run_in_vm() {
    # Runs a command inside the aircraft LXC container, non-interactively.
    # $1 = the shell command to run inside the container
    lxc-attach --clear-env --keep-var TERM --name autopilot -- /bin/bash -l -c "$1"
}

log "=========================================="
log "Starting P100 content update"
log "=========================================="

log "Step 1/8: autopilot off"
autopilot off >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    log "FAILED: autopilot off — aborting."
    exit 1
fi

log "Step 2/8: docker-compose stop flight-controller"
run_in_vm "cd $SRV_DIR && docker-compose stop flight-controller" >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    log "FAILED: docker-compose stop flight-controller — aborting."
    log "Attempting to restore autopilot before exit..."
    autopilot on >> "$LOG" 2>&1
    exit 1
fi

log "Step 3/8: airsync sync --force-reset (this can take 30-40+ minutes, please wait)"
run_in_vm "cd $SRV_DIR && docker-compose exec -T aircraft ./manage.py airsync sync --force-reset" >> "$LOG" 2>&1
AIRSYNC_RC=$?
if [ $AIRSYNC_RC -ne 0 ]; then
    log "FAILED: airsync sync --force-reset (exit code $AIRSYNC_RC) — aborting."
    log "Attempting to bring flight-controller back up before exit..."
    run_in_vm "cd $SRV_DIR && docker-compose up -d flight-controller" >> "$LOG" 2>&1
    autopilot on >> "$LOG" 2>&1
    exit 1
fi
log "airsync sync --force-reset completed successfully."

log "Step 4/8: filesync run -ct 16 -st 16 -cv -sv"
run_in_vm "cd $SRV_DIR && docker-compose exec -T aircraft ./manage.py filesync run -ct 16 -st 16 -cv -sv" >> "$LOG" 2>&1
FILESYNC_RUN_RC=$?
if [ $FILESYNC_RUN_RC -ne 0 ]; then
    log "FAILED: filesync run (exit code $FILESYNC_RUN_RC) — aborting."
    run_in_vm "cd $SRV_DIR && docker-compose up -d flight-controller" >> "$LOG" 2>&1
    autopilot on >> "$LOG" 2>&1
    exit 1
fi
log "filesync run completed successfully."

log "Step 5/8: filesync clean"
run_in_vm "cd $SRV_DIR && docker-compose exec -T aircraft ./manage.py filesync clean" >> "$LOG" 2>&1
FILESYNC_CLEAN_RC=$?
if [ $FILESYNC_CLEAN_RC -ne 0 ]; then
    log "FAILED: filesync clean (exit code $FILESYNC_CLEAN_RC) — aborting."
    run_in_vm "cd $SRV_DIR && docker-compose up -d flight-controller" >> "$LOG" 2>&1
    autopilot on >> "$LOG" 2>&1
    exit 1
fi
log "filesync clean completed successfully."

log "Step 6/8: docker-compose up -d flight-controller"
run_in_vm "cd $SRV_DIR && docker-compose up -d flight-controller" >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    log "WARNING: docker-compose up -d flight-controller reported an error — check manually."
fi

log "Step 7/8: autopilot on"
autopilot on >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    log "WARNING: autopilot on reported an error — check manually."
fi

log "Step 8/8: verifying flight-controller status"
run_in_vm "cd $SRV_DIR && docker-compose ps flight-controller" >> "$LOG" 2>&1

log "=========================================="
log "Content update finished successfully!"
log "Full log: $LOG"
log "=========================================="
