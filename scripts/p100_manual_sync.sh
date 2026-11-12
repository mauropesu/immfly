#!/bin/bash
#
# p100_manual_sync.sh — manual content/dump sync for a P100 (run on the HOST, as root)
#
# Flow: pause vm_restart cron -> autopilot off -> stop flight-controller ->
#       airsync sync --force-reset -> [filesync run] -> filesync clean ->
#       restore: flight-controller up, autopilot on, cron restored.
# Opens its own detached tmux session; reattach with: tmux attach -t <session>
# A summary is printed at the end and saved under /var/log/p100_manual_sync/.
#
# Usage:
#   sudo ./p100_manual_sync.sh [--session NAME] [--no-content] [--cron-pattern REGEX]
#
set -u

SESSION="p100sync"
DO_CONTENT=1
CRON_PATTERN="vm_restart"
SRV_DIR="/immfly/srv"
LOGROOT="/var/log/p100_manual_sync"
LOCKFILE="/var/lock/p100_manual_sync.lock"
MARK="#P100SYNC# "

ARGS=("$@")
while [ $# -gt 0 ]; do
    case "$1" in
        --session)      SESSION="${2:?--session needs a name}"; shift 2 ;;
        --no-content)   DO_CONTENT=0; shift ;;
        --cron-pattern) CRON_PATTERN="${2:?--cron-pattern needs a regex}"; shift 2 ;;
        -h|--help)      sed -n '2,15p' "$0"; exit 0 ;;
        *)              echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

[ "$(id -u)" -eq 0 ] || { echo "Run as root." >&2; exit 1; }
command -v tmux >/dev/null || { echo "tmux not installed." >&2; exit 1; }

# ---- Relaunch inside a detached tmux session ----
if [ -z "${P100_SYNC_INNER:-}" ]; then
    if tmux has-session -t "$SESSION" 2>/dev/null; then
        echo "tmux session '$SESSION' already exists. Attach: tmux attach -t $SESSION" >&2
        exit 1
    fi
    printf -v CMD '%q ' "$0" "${ARGS[@]}"
    tmux new-session -d -s "$SESSION" "P100_SYNC_INNER=1 bash $CMD"
    echo "Started in tmux session '$SESSION'."
    echo "Follow progress:  tmux attach -t $SESSION   (detach: Ctrl-b d)"
    exit 0
fi

# ---- Inner run ----
exec 9>"$LOCKFILE"
flock -n 9 || { echo "Another sync is already running."; read -r -p "Enter to close"; exit 1; }

TS="$(date +%Y%m%d_%H%M%S)"
LOGDIR="$LOGROOT/$TS"
mkdir -p "$LOGDIR"
MAINLOG="$LOGDIR/main.log"
SUMMARY="$LOGDIR/summary.txt"
STATE_CRON="$LOGDIR/cron_state"
START_EPOCH=$(date +%s)

declare -a STEP_NAMES=() STEP_RC=() STEP_DUR=() STEP_STATUS=()
FAILED=0
AUTOPILOT_OFF=0
CRON_PAUSED=0

log() { echo "$(date -Is) - $*" | tee -a "$MAINLOG"; }

run_in_vm() {
    # Runs a command inside aircraftvm by feeding `autopilot ssh` through stdin.
    # The remote exit status is printed as "__P100RC=<n>" (parsed by vm_rc).
    printf '%s\n' "(cd ${SRV_DIR} && $1) < /dev/null; echo \"__P100RC=\$?\"" "exit" | autopilot ssh 2>&1
}

vm_rc() {
    # $1 = file with run_in_vm output; prints the remote rc (255 if marker is missing)
    local r
    r=$(grep -a '__P100RC=' "$1" | tail -n 1 | sed 's/.*__P100RC=//' | tr -dc '0-9')
    echo "${r:-255}"
}

cron_pause() {
    : > "$STATE_CRON"
    if crontab -l 2>/dev/null | grep -Eq "^[^#].*${CRON_PATTERN}"; then
        local cur; cur="$(crontab -l | sed -E "/${CRON_PATTERN}/{/^[[:space:]]*#/!s/^/${MARK}/}")"
        printf '%s\n' "$cur" | crontab -
        echo "root-crontab" >> "$STATE_CRON"
    fi
    local f
    for f in $(grep -lE "^[^#].*${CRON_PATTERN}" /etc/cron.d/* 2>/dev/null); do
        sed -i -E "/${CRON_PATTERN}/{/^[[:space:]]*#/!s/^/${MARK}/}" "$f"
        echo "$f" >> "$STATE_CRON"
    done
    CRON_PAUSED=1
    log "cron paused (pattern '${CRON_PATTERN}'): $(tr '\n' ' ' < "$STATE_CRON")"
}

cron_restore() {
    [ "$CRON_PAUSED" -eq 1 ] || return 0
    local t
    while read -r t; do
        if [ "$t" = "root-crontab" ]; then
            local cur; cur="$(crontab -l | sed -E "s/^${MARK}//")"
            printf '%s\n' "$cur" | crontab -
        else
            sed -i -E "s/^${MARK}//" "$t"
        fi
    done < "$STATE_CRON"
    CRON_PAUSED=0
    log "cron restored"
}

run_step() {
    # run_step "<name>" "<command run inside the VM>"
    local name="$1" cmd="$2" t0 rc
    if [ "$FAILED" -eq 1 ]; then
        STEP_NAMES+=("$name"); STEP_RC+=("-"); STEP_DUR+=(0); STEP_STATUS+=("SKIPPED")
        return 0
    fi
    log "START: $name"
    t0=$(date +%s)
    local slog="$LOGDIR/$(echo "$name" | tr ' /' '__').log"
    run_in_vm "$cmd" 2>&1 | tee -a "$MAINLOG" "$slog"
    rc=$(vm_rc "$slog")
    STEP_NAMES+=("$name"); STEP_RC+=("$rc"); STEP_DUR+=($(( $(date +%s) - t0 )))
    if [ "$rc" -eq 0 ]; then
        STEP_STATUS+=("OK"); log "OK: $name"
    else
        STEP_STATUS+=("FAILED"); FAILED=1; log "FAILED ($rc): $name"
    fi
}

fmt_dur() { printf '%02dh%02dm%02ds' $(($1/3600)) $(($1%3600/60)) $(($1%60)); }

finish() {
    log "Restoring services..."
    run_in_vm "docker-compose up -d flight-controller" > "$LOGDIR/restore_up.log" 2>&1
    cat "$LOGDIR/restore_up.log" >> "$MAINLOG"
    if [ "$(vm_rc "$LOGDIR/restore_up.log")" -eq 0 ]; then log "flight-controller up"
    else log "WARNING: could not start flight-controller — check manually"; fi
    if [ "$AUTOPILOT_OFF" -eq 1 ]; then
        autopilot on >> "$MAINLOG" 2>&1 && log "autopilot on" || log "WARNING: autopilot on failed — run it manually"
    fi
    cron_restore

    local end total i
    end=$(date +%s); total=$((end - START_EPOCH))
    {
        echo "=================================================="
        echo " P100 MANUAL SYNC SUMMARY"
        echo " Host: $(hostname)   Started: $(date -d "@$START_EPOCH" '+%F %T')   Total: $(fmt_dur "$total")"
        echo "=================================================="
        printf ' %-34s %-8s %-4s %s\n' STEP STATUS RC DURATION
        for i in "${!STEP_NAMES[@]}"; do
            printf ' %-34s %-8s %-4s %s\n' "${STEP_NAMES[$i]}" "${STEP_STATUS[$i]}" "${STEP_RC[$i]}" "$(fmt_dur "${STEP_DUR[$i]}")"
        done
        echo "--------------------------------------------------"
        if [ "$FAILED" -eq 1 ]; then
            echo " RESULT: FAILED — last 15 lines of the failing step:"
            for i in "${!STEP_NAMES[@]}"; do
                [ "${STEP_STATUS[$i]}" = "FAILED" ] && tail -n 15 "$LOGDIR/$(echo "${STEP_NAMES[$i]}" | tr ' /' '__').log"
            done
        else
            echo " RESULT: SUCCESS"
        fi
        echo "--------------------------------------------------"
        echo " flight-controller:"
        run_in_vm "docker-compose ps flight-controller" 2>&1 | grep -av -e '__P100RC=' -e 'Pseudo-terminal' -e 'Welcome to' -e 'Documentation' | sed 's/^/   /'
        echo " Logs: $LOGDIR"
        echo "=================================================="
    } 2>&1 | tee "$SUMMARY"
    [ "$FAILED" -eq 0 ]
}
trap 'log "Interrupted"; FAILED=1; finish; exit 130' INT TERM

log "Starting manual sync (content=$DO_CONTENT, session=$SESSION)"
cron_pause

log "autopilot off"
if autopilot off >> "$MAINLOG" 2>&1; then
    AUTOPILOT_OFF=1
else
    log "FAILED: autopilot off"; FAILED=1
    STEP_NAMES+=("autopilot off"); STEP_RC+=("1"); STEP_DUR+=(0); STEP_STATUS+=("FAILED")
fi

run_step "stop flight-controller" "docker-compose stop flight-controller"
run_step "airsync sync --force-reset" "docker-compose exec -T aircraft ./manage.py airsync sync --force-reset"
if [ "$DO_CONTENT" -eq 1 ]; then
    run_step "filesync run" "docker-compose exec -T aircraft ./manage.py filesync run -ct 16 -st 16 -cv -sv"
fi
run_step "filesync clean" "docker-compose exec -T aircraft ./manage.py filesync clean"

finish; RC=$?
echo
read -r -p "Done (exit $RC). Press Enter to close this tmux session..."
exit "$RC"
