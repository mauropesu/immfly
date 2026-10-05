#!/bin/bash
#
# p100_content_update_invm.sh
#
# Run this INSIDE the aircraftvm session, after you've already done:
#   autopilot off
#   autopilot ssh
#   cd /immfly/srv
#
# It automates just the three long-running steps so you don't have to
# babysit each one:
#   1. docker-compose stop flight-controller
#   2. airsync sync --force-reset   (can take 30-40+ minutes)
#   3. filesync run -ct 16 -st 16 -cv -sv
#   4. filesync clean
#   5. docker-compose up -d flight-controller
#
# After this finishes, exit the VM and run 'autopilot on' manually as usual.
#
# Usage (from /immfly/srv inside aircraftvm):
#   bash p100_content_update_invm.sh
#
# Recommended: wrap the whole autopilot ssh session in tmux on the HOST
# (p100-ify) before running 'autopilot ssh', so it survives a dropped
# SSH/VPN connection to your laptop:
#   tmux new -s content_update
#   autopilot off
#   autopilot ssh
#   cd /immfly/srv
#   bash p100_content_update_invm.sh

set -u

LOG="/tmp/p100_content_update.log"

log() {
    echo "$(date -Is) - $1" | tee -a "$LOG"
}

log "=========================================="
log "Starting P100 content update (in-VM steps)"
log "=========================================="

log "Step 1/5: docker-compose stop flight-controller"
docker-compose stop flight-controller 2>&1 | tee -a "$LOG"
if [ ${PIPESTATUS[0]} -ne 0 ]; then
    log "FAILED: docker-compose stop flight-controller — aborting."
    exit 1
fi

log "Step 2/5: airsync sync --force-reset (this can take 30-40+ minutes, please wait)"
docker-compose exec -T aircraft ./manage.py airsync sync --force-reset 2>&1 | tee -a "$LOG"
if [ ${PIPESTATUS[0]} -ne 0 ]; then
    log "FAILED: airsync sync --force-reset — aborting."
    log "Attempting to bring flight-controller back up before exit..."
    docker-compose up -d flight-controller 2>&1 | tee -a "$LOG"
    exit 1
fi
log "airsync sync --force-reset completed successfully."

log "Step 3/5: filesync run -ct 16 -st 16 -cv -sv"
docker-compose exec -T aircraft ./manage.py filesync run -ct 16 -st 16 -cv -sv 2>&1 | tee -a "$LOG"
if [ ${PIPESTATUS[0]} -ne 0 ]; then
    log "FAILED: filesync run — aborting."
    docker-compose up -d flight-controller 2>&1 | tee -a "$LOG"
    exit 1
fi
log "filesync run completed successfully."

log "Step 4/5: filesync clean"
docker-compose exec -T aircraft ./manage.py filesync clean 2>&1 | tee -a "$LOG"
if [ ${PIPESTATUS[0]} -ne 0 ]; then
    log "FAILED: filesync clean — aborting."
    docker-compose up -d flight-controller 2>&1 | tee -a "$LOG"
    exit 1
fi
log "filesync clean completed successfully."

log "Step 5/5: docker-compose up -d flight-controller"
docker-compose up -d flight-controller 2>&1 | tee -a "$LOG"

log "=========================================="
log "All in-VM steps finished successfully!"
log "Now: exit this VM session, then run 'autopilot on' on the host."
log "Full log: $LOG"
log "=========================================="
