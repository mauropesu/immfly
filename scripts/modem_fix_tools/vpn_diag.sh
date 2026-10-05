#!/bin/bash
# vpn_diag.sh - VPN diagnostics and remediation for P100 nodes
# Usage: ./vpn_diag.sh [--fix]

# ─── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
YEL='\033[0;33m'
GRN='\033[0;32m'
CYN='\033[0;36m'
BLD='\033[1m'
DIM='\033[2m'
RST='\033[0m'

# ─── Helpers ──────────────────────────────────────────────────────────────────
ok()   { echo -e "  ${GRN}[OK]${RST}    $*"; }
warn() { echo -e "  ${YEL}[WARN]${RST}  $*"; }
fail() { echo -e "  ${RED}[FAIL]${RST}  $*"; }
info() { echo -e "  ${CYN}[INFO]${RST}  $*"; }
section() { echo -e "\n${BLD}${CYN}══ $* ${RST}"; }

FIX_MODE=false
[[ "$1" == "--fix" ]] && FIX_MODE=true

ISSUES=()
FIXES_APPLIED=()

# ─── Header ───────────────────────────────────────────────────────────────────
echo -e "${BLD}"
echo "╔══════════════════════════════════════════════╗"
echo "║         P100 VPN Diagnostics v1.0            ║"
echo "╚══════════════════════════════════════════════╝"
echo -e "${RST}${DIM}  Node: $(hostname)  |  $(date -u '+%Y-%m-%d %H:%M:%S UTC')${RST}"
echo -e "${DIM}  Mode: $([ "$FIX_MODE" = true ] && echo 'DIAGNOSE + FIX' || echo 'DIAGNOSE ONLY')${RST}"

# ══════════════════════════════════════════════════════════════════════════════
# CHECK 1 — ppp0 interface
# ══════════════════════════════════════════════════════════════════════════════
section "1/7  PPP Interface"

if ip link show ppp0 &>/dev/null; then
    PPP_IP=$(ip addr show ppp0 2>/dev/null | awk '/inet /{print $2}' | head -1)
    ok "ppp0 is up — IP: ${PPP_IP:-unknown}"

    # Connectivity check
    if ping -c 1 -W 3 -I ppp0 8.8.8.8 &>/dev/null; then
        ok "Internet reachable via ppp0"
    else
        warn "ppp0 is up but internet ping failed (transient or routing issue)"
        ISSUES+=("ppp0 up but no internet reachability")
    fi
else
    fail "ppp0 interface not found — VPN cannot connect without cellular"
    ISSUES+=("ppp0 interface missing")

    if $FIX_MODE; then
        warn "Cannot auto-fix PPP — run internet-p100.py manually or wait for cron"
        warn "Try: python /root/p100_tools/internet/internet-p100.py"
    fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# CHECK 2 — Multiple pppd processes
# ══════════════════════════════════════════════════════════════════════════════
section "2/7  PPP Daemon Instances"

PPPD_COUNT=$(pgrep -c pppd 2>/dev/null || echo 0)
if [ "$PPPD_COUNT" -eq 0 ]; then
    warn "No pppd process running"
    ISSUES+=("pppd not running")
elif [ "$PPPD_COUNT" -eq 1 ]; then
    ok "Single pppd process running (PID: $(pgrep pppd))"
elif [ "$PPPD_COUNT" -gt 1 ]; then
    warn "${PPPD_COUNT} pppd processes running — expected 1"
    info "PIDs: $(pgrep pppd | tr '\n' ' ')"
    ISSUES+=("Multiple pppd processes: $PPPD_COUNT")

    if $FIX_MODE; then
        info "Killing duplicate pppd processes (keeping newest)..."
        NEWEST=$(pgrep pppd | sort -n | tail -1)
        pgrep pppd | grep -v "$NEWEST" | xargs kill 2>/dev/null
        sleep 2
        NEW_COUNT=$(pgrep -c pppd 2>/dev/null || echo 0)
        ok "Reduced to ${NEW_COUNT} pppd process(es)"
        FIXES_APPLIED+=("Killed duplicate pppd processes")
    fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# CHECK 3 — modem-enable.service
# ══════════════════════════════════════════════════════════════════════════════
section "3/7  Modem Enable Service"

if systemctl list-units --all | grep -q 'modem-enable.service'; then
    MODEM_STATE=$(systemctl is-active modem-enable.service 2>/dev/null)
    MODEM_RESULT=$(systemctl show modem-enable.service --property=Result --value 2>/dev/null)

    if [ "$MODEM_STATE" = "active" ]; then
        ok "modem-enable.service: active"
    elif [ "$MODEM_RESULT" = "success" ] || [ "$MODEM_STATE" = "inactive" ]; then
        ok "modem-enable.service: completed successfully"
    else
        fail "modem-enable.service: ${MODEM_STATE} (result: ${MODEM_RESULT})"
        LAST_ERROR=$(journalctl -u modem-enable.service --no-pager -n 3 2>/dev/null | tail -1)
        info "Last log: ${LAST_ERROR}"
        ISSUES+=("modem-enable.service failed: $MODEM_RESULT")

        if $FIX_MODE; then
            info "Retrying modem-enable.service..."
            systemctl restart modem-enable.service
            sleep 5
            NEW_STATE=$(systemctl is-active modem-enable.service 2>/dev/null)
            NEW_RESULT=$(systemctl show modem-enable.service --property=Result --value 2>/dev/null)
            if [ "$NEW_RESULT" = "success" ] || [ "$NEW_STATE" = "active" ]; then
                ok "modem-enable.service now succeeded"
                FIXES_APPLIED+=("Restarted modem-enable.service successfully")
            else
                fail "modem-enable.service still failing after retry"
            fi
        fi
    fi
else
    warn "modem-enable.service not found on this node"
    ISSUES+=("modem-enable.service missing")
fi

# ══════════════════════════════════════════════════════════════════════════════
# CHECK 4 — OpenVPN instance status
# ══════════════════════════════════════════════════════════════════════════════
section "4/7  OpenVPN Instances"

CONFIGS=$(ls /etc/openvpn/client_*.conf 2>/dev/null)

if [ -z "$CONFIGS" ]; then
    warn "No client_*.conf files found in /etc/openvpn/"
    ISSUES+=("No OpenVPN config files found")
else
    for CONF in $CONFIGS; do
        NAME=$(basename "$CONF" .conf)
        INSTANCE="openvpn@${NAME}.service"
        STATE=$(systemctl is-active "$INSTANCE" 2>/dev/null)
        ENABLED=$(systemctl is-enabled "$INSTANCE" 2>/dev/null)

        if [ "$STATE" = "active" ]; then
            TUN=$(grep '^dev ' "$CONF" | awk '{print $2}')
            TUN_IP=$(ip addr show "$TUN" 2>/dev/null | awk '/inet /{print $2}' | head -1)
            ok "${NAME}: running — ${TUN} ${TUN_IP:-[no IP yet]}"
        else
            fail "${NAME}: ${STATE} (enabled: ${ENABLED})"
            ISSUES+=("$NAME is $STATE")

            if $FIX_MODE; then
                info "Starting ${INSTANCE}..."
                systemctl start "$INSTANCE"
                sleep 5
                NEW_STATE=$(systemctl is-active "$INSTANCE" 2>/dev/null)
                if [ "$NEW_STATE" = "active" ]; then
                    ok "${NAME}: now running"
                    FIXES_APPLIED+=("Started $INSTANCE")
                else
                    fail "${NAME}: still ${NEW_STATE} after start attempt"
                fi
            fi
        fi
    done
fi

# ══════════════════════════════════════════════════════════════════════════════
# CHECK 5 — File permissions
# ══════════════════════════════════════════════════════════════════════════════
section "5/7  File Permissions"

declare -A SENSITIVE_FILES

# Auth files
for F in /etc/openvpn/*_auth.ovpn; do
    [ -f "$F" ] && SENSITIVE_FILES["$F"]=600
done

# Cert/key files
for F in /etc/openvpn/vpn-AVA/certs/*.key \
         /etc/openvpn/client1.key \
         /etc/openvpn/*.key; do
    [ -f "$F" ] && SENSITIVE_FILES["$F"]=600
done

PERM_ISSUES=0
for FILE in "${!SENSITIVE_FILES[@]}"; do
    EXPECTED="${SENSITIVE_FILES[$FILE]}"
    ACTUAL=$(stat -c "%a" "$FILE" 2>/dev/null)

    if [ -z "$ACTUAL" ]; then
        warn "File not found: $FILE"
        continue
    fi

    if [ "$ACTUAL" = "$EXPECTED" ]; then
        ok "$(basename $FILE): ${ACTUAL} ✓"
    else
        fail "$(basename $FILE): ${ACTUAL} (expected ${EXPECTED})"
        ISSUES+=("Bad permissions on $(basename $FILE): $ACTUAL")
        PERM_ISSUES=$((PERM_ISSUES + 1))

        if $FIX_MODE; then
            chmod "$EXPECTED" "$FILE"
            ok "Fixed: chmod ${EXPECTED} ${FILE}"
            FIXES_APPLIED+=("Fixed permissions on $(basename $FILE)")
        fi
    fi
done

[ "$PERM_ISSUES" -eq 0 ] && [ ${#SENSITIVE_FILES[@]} -gt 0 ] && ok "All sensitive files have correct permissions"

# ══════════════════════════════════════════════════════════════════════════════
# CHECK 6 — DNS resolution for VPN hostnames
# ══════════════════════════════════════════════════════════════════════════════
section "6/7  DNS Resolution"

CONFIGS=$(ls /etc/openvpn/client_*.conf 2>/dev/null)
DNS_ISSUES=0

if [ -z "$CONFIGS" ]; then
    warn "No configs to extract VPN hostnames from"
else
    for CONF in $CONFIGS; do
        NAME=$(basename "$CONF" .conf)
        # Extract hostname and port from 'remote' directive
        REMOTE_LINE=$(grep '^remote ' "$CONF" | head -1)
        HOST=$(echo "$REMOTE_LINE" | awk '{print $2}')
        PORT=$(echo "$REMOTE_LINE" | awk '{print $3}')

        if [ -z "$HOST" ]; then
            warn "${NAME}: no 'remote' directive found in config"
            continue
        fi

        RESOLVED=$(getent hosts "$HOST" 2>/dev/null | awk '{print $1}' | head -1)
        if [ -n "$RESOLVED" ]; then
            ok "${NAME}: ${HOST} → ${RESOLVED}:${PORT}"
        else
            fail "${NAME}: cannot resolve ${HOST}"
            ISSUES+=("DNS resolution failed for $HOST")
            DNS_ISSUES=$((DNS_ISSUES + 1))
            info "Check: is ppp0 up? Is usepeerdns set in /etc/ppp/peers/provider?"
        fi
    done
fi

# ══════════════════════════════════════════════════════════════════════════════
# CHECK 7 — TLS/Auth errors in journal
# ══════════════════════════════════════════════════════════════════════════════
section "7/7  Recent Journal Errors"

for CONF in $(ls /etc/openvpn/client_*.conf 2>/dev/null); do
    NAME=$(basename "$CONF" .conf)
    INSTANCE="openvpn@${NAME}"

    TLS_ERRS=$(journalctl -u "${INSTANCE}" --no-pager --since "1 hour ago" 2>/dev/null \
        | grep -c "TLS Error\|AUTH_FAILED\|certificate\|VERIFY ERROR" || true)
    RESOLVE_ERRS=$(journalctl -u "${INSTANCE}" --no-pager --since "1 hour ago" 2>/dev/null \
        | grep -c "RESOLVE.*Cannot resolve\|Name or service not known" || true)

    if [ "$TLS_ERRS" -gt 0 ]; then
        fail "${NAME}: ${TLS_ERRS} TLS/auth error(s) in last hour"
        info "Last TLS error:"
        journalctl -u "${INSTANCE}" --no-pager --since "1 hour ago" 2>/dev/null \
            | grep "TLS Error\|AUTH_FAILED\|VERIFY ERROR" | tail -1 \
            | awk -F'] ' '{print "    "$NF}'
        ISSUES+=("$NAME has TLS/auth errors in journal")

        if $FIX_MODE; then
            info "Restarting ${INSTANCE} to clear TLS state..."
            systemctl restart "${INSTANCE}.service"
            sleep 3
            FIXES_APPLIED+=("Restarted $INSTANCE to clear TLS errors")
        fi
    elif [ "$RESOLVE_ERRS" -gt 0 ]; then
        warn "${NAME}: ${RESOLVE_ERRS} DNS resolution error(s) in last hour (self-healing if ppp0 is up)"
    else
        ok "${NAME}: no TLS/auth errors in last hour"
    fi
done

# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
echo -e "\n${BLD}${CYN}══ Summary ${RST}"

if [ ${#ISSUES[@]} -eq 0 ]; then
    echo -e "\n  ${GRN}${BLD}All checks passed — VPN stack looks healthy.${RST}\n"
else
    echo -e "\n  ${RED}${BLD}Issues found: ${#ISSUES[@]}${RST}"
    for ISSUE in "${ISSUES[@]}"; do
        echo -e "  ${RED}•${RST} $ISSUE"
    done

    if [ ${#FIXES_APPLIED[@]} -gt 0 ]; then
        echo -e "\n  ${GRN}${BLD}Fixes applied: ${#FIXES_APPLIED[@]}${RST}"
        for FIX in "${FIXES_APPLIED[@]}"; do
            echo -e "  ${GRN}✓${RST} $FIX"
        done
    fi

    if ! $FIX_MODE; then
        echo -e "\n  ${YEL}Run with ${BLD}--fix${RST}${YEL} to attempt automatic remediation:${RST}"
        echo -e "  ${DIM}./vpn_diag.sh --fix${RST}"
    fi
fi

echo ""
