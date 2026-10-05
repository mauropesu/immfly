#!/bin/bash
#
# p100_manual_sync.sh
#
# Manual content/dump sync for a single P100 node, safe to run unattended.
# - Takes node ID as $1, or falls back to /etc/planename if run locally on a node.
# - Runs the entire sequence (stop flight-controller -> airsync -> filesync run ->
#   filesync clean -> start flight-controller) inside ONE autopilot ssh session.
# - Logs every step with timestamps and exit codes to /scripts/logs/.
# - Uses a trap so autopilot is always re-enabled on exit, whether the sync
#   succeeds, fails partway, or the script is interrupted (Ctrl+C).
#
# Usage:
#   ./p100_manual_sync.sh N133AV
#   ./p100_manual_sync.sh            (auto-detects node from /etc/planename)
#
# Recommended: run inside tmux/screen so it survives disconnects.

set -u

NODE="${1:-$(cat /etc/planename 2>/dev/null)}"

if [ -z "$NODE" ]; then
  echo "Usage: $0 <NODE_ID>  (or run on a host with /etc/planename set)"
  exit 1
fi

LOG_DIR="/scripts/logs"
mkdir -p "$LOG_DIR"
LOG="${LOG_DIR}/p100_sync_${NODE}_$(date +%Y%m%d_%H%M%S).log"

AUTOPILOT_RESTORED=0

restore_autopilot() {
  if [ "$AUTOPILOT_RESTORED" -eq 0 ]; then
    echo "[$(date)] Re-enabling autopilot on $NODE (trap/exit handler)" | tee -a "$LOG"
    autopilot on >>"$LOG" 2>&1
    echo "[$(date)] autopilot on exit code: $?" | tee -a "$LOG"
    AUTOPILOT_RESTORED=1
  fi
}
trap restore_autopilot EXIT INT TERM

echo "[$(date)] ===== Starting manual sync on $NODE =====" | tee -a "$LOG"
echo "[$(date)] Log file: $LOG" | tee -a "$LOG"

echo "[$(date)] Disabling autopilot on $NODE" | tee -a "$LOG"
autopilot off >>"$LOG" 2>&1
echo "[$(date)] autopilot off exit code: $?" | tee -a "$LOG"

echo "[$(date)] Connecting to $NODE to run sync sequence..." | tee -a "$LOG"

autopilot ssh >>"$LOG" 2>&1 <<'REMOTE'
set -u
cd /immfly/srv || { echo "[FATAL] cannot cd to /immfly/srv"; exit 1; }

echo "[STEP] docker-compose stop flight-controller"
docker-compose stop flight-controller
echo "[EXIT stop flight-controller] $?"

echo "[STEP] airsync sync --force-reset"
docker-compose exec -T aircraft ./manage.py airsync sync --force-reset
airsync_rc=$?
echo "[EXIT airsync] $airsync_rc"

if [ "$airsync_rc" -eq 0 ]; then
  echo "[STEP] filesync run"
  docker-compose exec -T aircraft ./manage.py filesync run -ct 16 -st 16 -cv -sv
  filesync_run_rc=$?
  echo "[EXIT filesync run] $filesync_run_rc"

  if [ "$filesync_run_rc" -eq 0 ]; then
    echo "[STEP] filesync clean"
    docker-compose exec -T aircraft ./manage.py filesync clean
    echo "[EXIT filesync clean] $?"
  else
    echo "[SKIP] filesync clean skipped due to filesync run failure"
  fi
else
  echo "[SKIP] filesync run and clean skipped due to airsync failure"
fi

echo "[STEP] docker-compose up -d flight-controller"
docker-compose up -d flight-controller
echo "[EXIT flight-controller up] $?"

echo "[STEP] docker-compose ps (health check)"
docker-compose ps
REMOTE

ssh_rc=$?
echo "[$(date)] autopilot ssh session exit code: $ssh_rc" | tee -a "$LOG"

echo "[$(date)] ===== Sequence finished for $NODE =====" | tee -a "$LOG"
echo "[$(date)] Review full log at: $LOG" | tee -a "$LOG"

# autopilot on runs automatically via the trap above, even if something failed.
