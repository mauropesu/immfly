#!/usr/bin/env python3
"""
p100_doctor.py  –  Immfly P100 Field Diagnostic & Fix Tool
===========================================================
Covers:
  • System / VPN identity validation
  • Cellular modem (Sierra Wireless EM7565) checks: USB mode, firmware,
    ModemManager state, PPP link, signal quality
  • OpenVPN tunnel status
  • WiFi / WAP reachability
  • ADSB / internet-p100.py cron status
  • Battery LED hint (manual step reminder)
  • Post-restore validation one-liner

Usage:
  python3 p100_doctor.py [--fix] [--section SECTION] [--node NODE_ID]

  --fix          Attempt automatic remediation where safe to do so
  --section      Run only one section: system | modem | vpn | wifi | cron
  --node         Override node label shown in reports (e.g. N133AV)

Requirements on the P100:
  python3, mmcli, qmicli, pppd, ip, ping, systemctl, minicom/pyserial (for AT),
  libqmi-utils (for firmware), dmesg, dmidecode
"""

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime

# ──────────────────────────────────────────────────────────────────────────────
# Colour helpers
# ──────────────────────────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
RED    = "\033[31m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"

def ok(msg):    print(f"  {GREEN}✓{RESET} {msg}")
def warn(msg):  print(f"  {YELLOW}⚠{RESET} {msg}")
def err(msg):   print(f"  {RED}✗{RESET} {msg}")
def info(msg):  print(f"  {CYAN}ℹ{RESET} {msg}")
def head(msg):  print(f"\n{BOLD}{CYAN}{'─'*60}{RESET}\n{BOLD}{msg}{RESET}")
def subhead(m): print(f"\n  {BOLD}{m}{RESET}")

# ──────────────────────────────────────────────────────────────────────────────
# Shell helpers
# ──────────────────────────────────────────────────────────────────────────────
def run(cmd, timeout=30, input_text=None):
    """Run a shell command; return (stdout, stderr, returncode)."""
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, input=input_text
        )
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "TIMEOUT", 1
    except Exception as e:
        return "", str(e), 1

def run_ok(cmd, timeout=30):
    """Return stdout or None on failure."""
    out, _, rc = run(cmd, timeout=timeout)
    return out if rc == 0 else None

# ──────────────────────────────────────────────────────────────────────────────
# AT command via pyserial (non-blocking, no minicom conflict)
# ──────────────────────────────────────────────────────────────────────────────
def send_at(port, command, timeout=5):
    """
    Send a single AT command via pyserial and return the response lines.
    Falls back to a warning if pyserial is unavailable or port busy.
    """
    try:
        import serial
        with serial.Serial(port, baudrate=115200, timeout=timeout) as s:
            s.write(f"{command}\r\n".encode())
            time.sleep(0.5)
            raw = s.read(s.in_waiting or 512).decode(errors="replace")
            return raw.strip()
    except ImportError:
        return "PYSERIAL_MISSING"
    except Exception as e:
        return f"ERROR: {e}"

def find_at_port():
    """Return the first ttyUSB device that responds to AT."""
    out = run_ok("dmesg | grep 'Sierra USB modem converter'") or ""
    ports = re.findall(r"ttyUSB(\d+)", out)
    if not ports:
        return None
    # Try each; in P100 the AT port is usually the first (ttyUSB0)
    for p in sorted(set(ports)):
        dev = f"/dev/ttyUSB{p}"
        if os.path.exists(dev):
            resp = send_at(dev, "AT")
            if "OK" in resp:
                return dev
    return None

# ──────────────────────────────────────────────────────────────────────────────
# Results accumulator
# ──────────────────────────────────────────────────────────────────────────────
class Report:
    def __init__(self):
        self.checks = []   # (label, status, detail)  status: ok/warn/err/info

    def add(self, label, status, detail=""):
        self.checks.append((label, status, detail))

    def summary(self):
        head("SUMMARY")
        total = len(self.checks)
        issues = [c for c in self.checks if c[1] in ("warn", "err")]
        for label, status, detail in self.checks:
            sym = {"ok": f"{GREEN}✓{RESET}", "warn": f"{YELLOW}⚠{RESET}",
                   "err": f"{RED}✗{RESET}", "info": f"{CYAN}ℹ{RESET}"}.get(status, "?")
            tail = f"  — {detail}" if detail else ""
            print(f"  {sym} {label}{tail}")
        print()
        if not issues:
            print(f"  {GREEN}{BOLD}All {total} checks passed.{RESET}")
        else:
            print(f"  {RED}{BOLD}{len(issues)} issue(s) found out of {total} checks.{RESET}")

# ──────────────────────────────────────────────────────────────────────────────
# Section 1 – System / VPN identity
# ──────────────────────────────────────────────────────────────────────────────
def check_system(report, node_label, fix=False):
    head("1. SYSTEM & VPN IDENTITY")

    # Serial number
    serial = run_ok("dmidecode -s system-serial-number") or "UNKNOWN"
    info(f"Serial: {serial}")
    if serial.startswith("W"):
        ok("Serial prefix W (standard Kontron unit)")
        report.add("Serial prefix", "ok", serial)
    elif serial.startswith("K"):
        ok("Serial prefix K (KONUK unit) — remember KONUK-specific steps")
        report.add("Serial prefix", "ok", f"{serial} [KONUK]")
    else:
        warn(f"Unrecognised serial prefix: {serial}")
        report.add("Serial prefix", "warn", serial)

    # Node label from VPN config
    vpn_auth = run_ok("cat /etc/openvpn/auth.cfg")
    if vpn_auth:
        ok("VPN auth.cfg readable")
        info(f"VPN auth contents:\n    {vpn_auth[:120]}")
        report.add("VPN auth.cfg", "ok")
    else:
        err("Cannot read /etc/openvpn/auth.cfg")
        report.add("VPN auth.cfg", "err", "File missing or unreadable")

    # Hostname / node ID
    hostname = run_ok("hostname") or "unknown"
    info(f"Hostname: {hostname} | Node label: {node_label or 'not specified'}")
    report.add("Hostname", "info", hostname)

    # kern.log errors
    kern_errors = run_ok("grep -i error /var/log/kern.log | tail -10")
    if kern_errors:
        warn("Recent kernel errors found in /var/log/kern.log:")
        for line in kern_errors.splitlines():
            print(f"    {line}")
        report.add("Kernel errors", "warn", "See above")
    else:
        ok("No recent kernel errors in kern.log")
        report.add("Kernel errors", "ok")

    # Date / clock
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    info(f"System date: {now}")
    report.add("System date", "info", now)

# ──────────────────────────────────────────────────────────────────────────────
# Section 2 – Cellular modem
# ──────────────────────────────────────────────────────────────────────────────
GOOD_FIRMWARE  = "01.14.02.00"
MODEM_ENABLE_UNIT = "modem-enable.service"
MODEM_ENABLE_SVC = f"""[Unit]
Description=Enable ModemManager modem at boot
After=ModemManager.service
Requires=ModemManager.service

[Service]
Type=oneshot
ExecStartPre=/bin/sleep 15
ExecStart=/usr/bin/mmcli -m 0 --enable
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""

def check_modem(report, fix=False):
    head("2. CELLULAR MODEM (Sierra Wireless EM7565)")

    # ── 2.1 USB composition mode ──────────────────────────────────────────────
    subhead("2.1  USB composition mode")
    mbim_dev = run_ok("ls /dev/cdc-wdm* 2>/dev/null")
    qmi_check = run_ok("ls /dev/cdc-wdm0 2>/dev/null")
    dmesg_usb  = run_ok("dmesg | grep -i 'sierra\\|EM7565\\|mbim\\|qmi' | tail -20") or ""

    if "MBIM" in dmesg_usb.upper() and "QMI" not in dmesg_usb.upper():
        err("Modem appears to be in MBIM mode (no QMI)")
        report.add("USB mode", "err", "MBIM detected — QMI required")
        if fix:
            _fix_usb_composition(report)
    elif qmi_check:
        ok(f"QMI device present: {qmi_check}")
        report.add("USB mode", "ok", "QMI")
    else:
        warn("Could not determine USB composition mode from dmesg")
        report.add("USB mode", "warn", "Indeterminate")

    # ── 2.2 ModemManager ─────────────────────────────────────────────────────
    subhead("2.2  ModemManager")
    mm_status = run_ok("mmcli -L 2>/dev/null")
    if mm_status and "No modems" not in mm_status:
        ok(f"ModemManager sees a modem: {mm_status[:80]}")
        report.add("ModemManager", "ok")
    else:
        err("ModemManager reports no modem (or not running)")
        report.add("ModemManager", "err", "mmcli -L returned nothing")
        if fix:
            info("Attempting: systemctl restart ModemManager")
            run("systemctl restart ModemManager")
            time.sleep(5)
            mm_status = run_ok("mmcli -L 2>/dev/null")
            if mm_status and "No modems" not in mm_status:
                ok("ModemManager now sees a modem after restart")
                report.add("ModemManager restart", "ok")
            else:
                err("Still no modem after ModemManager restart")
                report.add("ModemManager restart", "err")

    # ── 2.3 modem-enable service ──────────────────────────────────────────────
    subhead("2.3  modem-enable.service (ensures modem enabled at boot)")
    svc_out = run_ok(f"systemctl is-enabled {MODEM_ENABLE_UNIT} 2>/dev/null")
    if svc_out and "enabled" in svc_out:
        ok(f"{MODEM_ENABLE_UNIT} is enabled")
        report.add("modem-enable.service", "ok")
    else:
        warn(f"{MODEM_ENABLE_UNIT} not found or not enabled")
        report.add("modem-enable.service", "warn", "Not installed")
        if fix:
            _install_modem_enable_service(report)

    # ── 2.4 Modem enabled state ───────────────────────────────────────────────
    subhead("2.4  Modem enabled state")
    mm_info = run_ok("mmcli -m 0 2>/dev/null") or ""
    if "enabled" in mm_info.lower():
        ok("Modem is in enabled state")
        report.add("Modem enabled", "ok")
    elif "disabled" in mm_info.lower():
        err("Modem is DISABLED — PPP will fail to register")
        report.add("Modem enabled", "err", "Run: mmcli -m 0 --enable")
        if fix:
            out, _, rc = run("mmcli -m 0 --enable")
            if rc == 0:
                ok("Modem enabled via mmcli")
                report.add("Modem enable fix", "ok")
            else:
                err(f"Failed to enable modem: {out}")
                report.add("Modem enable fix", "err")
    else:
        warn("Could not determine modem enabled state")
        report.add("Modem enabled", "warn", "mmcli -m 0 returned no clear state")

    # ── 2.5 Firmware version ──────────────────────────────────────────────────
    subhead("2.5  Firmware version")
    fw_out = run_ok("qmicli -d /dev/cdc-wdm0 --dms-get-revision 2>/dev/null") or ""
    fw_match = re.search(r"Revision:\s+'([^']+)'", fw_out)
    firmware = fw_match.group(1) if fw_match else None

    if not firmware:
        # Fallback: try AT!GSTATUS? via pyserial
        at_port = find_at_port()
        if at_port:
            gst = send_at(at_port, "AT!GSTATUS?")
            fw_re = re.search(r"Revision:\s+(\S+)", gst)
            firmware = fw_re.group(1) if fw_re else None

    if firmware:
        info(f"Firmware: {firmware}")
        if firmware >= GOOD_FIRMWARE:
            ok(f"Firmware is current ({firmware})")
            report.add("Firmware", "ok", firmware)
        else:
            err(f"OLD firmware {firmware} — target is {GOOD_FIRMWARE}")
            report.add("Firmware", "err", f"{firmware} → needs upgrade to {GOOD_FIRMWARE}")
            if fix:
                info("Firmware upgrade requires the SLQS archive on disk.")
                info("Run modem_fix.py --fix-firmware or see procedure doc §9.")
    else:
        warn("Could not read firmware version (QMI device may not be ready)")
        report.add("Firmware", "warn", "Unreadable")

    # ── 2.6 SIM / IMSI ────────────────────────────────────────────────────────
    subhead("2.6  SIM / IMSI")
    imsi_out = run_ok("qmicli -d /dev/cdc-wdm0 --dms-uim-get-imsi 2>/dev/null") or ""
    if "IMSI" in imsi_out and "NO IMSI" not in imsi_out:
        ok(f"SIM IMSI present: {imsi_out[:60]}")
        report.add("SIM IMSI", "ok")
    elif "NO IMSI" in imsi_out or not imsi_out:
        err("NO IMSI — SIM not detected or hardware SIM interface defect")
        report.add("SIM IMSI", "err", "Possible hardware failure — modem replacement may be needed")
    else:
        warn(f"Unexpected IMSI output: {imsi_out[:60]}")
        report.add("SIM IMSI", "warn")

    # AT SIM check via pyserial
    at_port = find_at_port()
    if at_port:
        subhead("2.7  AT command checks (pyserial)")
        info(f"Using AT port: {at_port}")

        cpin = send_at(at_port, "AT+CPIN?")
        if "READY" in cpin:
            ok("SIM PIN status: READY")
            report.add("SIM PIN", "ok")
        else:
            err(f"SIM PIN status unexpected: {cpin[:60]}")
            report.add("SIM PIN", "err", cpin[:60])

        creg = send_at(at_port, "AT+CREG?")
        info(f"Network registration (AT+CREG?): {creg[:80]}")
        if ",1" in creg or ",5" in creg:
            ok("Registered to home/roaming network")
            report.add("Network registration", "ok")
        else:
            warn(f"Not registered or unknown: {creg[:60]}")
            report.add("Network registration", "warn", creg[:60])

        csq = send_at(at_port, "AT+CSQ")
        csq_match = re.search(r"\+CSQ:\s*(\d+),", csq)
        if csq_match:
            rssi = int(csq_match.group(1))
            info(f"Signal quality (AT+CSQ): {rssi}/31")
            if rssi == 99:
                err("Signal: 99 — no signal / modem not registered")
                report.add("Signal quality", "err", "rssi=99 (no signal)")
            elif rssi < 10:
                warn(f"Signal: {rssi} — poor (check antenna / site interference)")
                report.add("Signal quality", "warn", f"rssi={rssi}")
            else:
                ok(f"Signal: {rssi} — acceptable")
                report.add("Signal quality", "ok", f"rssi={rssi}")
        else:
            warn(f"Could not parse AT+CSQ: {csq[:60]}")
            report.add("Signal quality", "warn")

        cops = send_at(at_port, "AT+COPS?")
        info(f"Operator (AT+COPS?): {cops[:80]}")
        report.add("Operator", "info", cops[:60])
    else:
        warn("No AT port found (pyserial or port busy) — skipping AT checks")
        report.add("AT port", "warn", "Could not find responding ttyUSB")

    # ── 2.8 PPP link ──────────────────────────────────────────────────────────
    subhead("2.8  PPP link (ppp0)")
    ppp = run_ok("ip addr show ppp0 2>/dev/null")
    if ppp and "inet" in ppp:
        ok("ppp0 is UP with IP address")
        report.add("PPP link", "ok")
    else:
        err("ppp0 not up or no IP")
        report.add("PPP link", "err", "Run: pon  or  check /var/log/internet_ppp.log")
        if fix:
            info("Attempting: killall pppd; sleep 2; pon")
            run("killall pppd 2>/dev/null; sleep 2; pon")
            time.sleep(10)
            ppp = run_ok("ip addr show ppp0 2>/dev/null")
            if ppp and "inet" in ppp:
                ok("ppp0 came up after pon")
                report.add("PPP fix", "ok")
            else:
                err("ppp0 still not up after pon attempt")
                report.add("PPP fix", "err", "Check /var/log/internet_ppp.log")

    # ── 2.9 PPP peer config ──────────────────────────────────────────────────
    subhead("2.9  PPP peer config (keepalive)")
    peer_file = run_ok("cat /etc/ppp/peers/movistar 2>/dev/null") or \
                run_ok("ls /etc/ppp/peers/ | head -5") or ""
    for kw in ("lcp-echo-interval", "persist", "maxfail"):
        if kw in peer_file:
            ok(f"PPP keepalive: '{kw}' present")
            report.add(f"PPP {kw}", "ok")
        else:
            warn(f"PPP keepalive: '{kw}' MISSING from peer config")
            report.add(f"PPP {kw}", "warn", "Add to /etc/ppp/peers/<provider>")

    # ── 2.10 udev symlinks ────────────────────────────────────────────────────
    subhead("2.10  udev modem symlinks")
    for link in ("modemAT", "modemPPP"):
        target = run_ok(f"readlink -f /dev/{link} 2>/dev/null")
        if target and os.path.exists(target):
            ok(f"/dev/{link} → {target}")
            report.add(f"udev {link}", "ok", target)
        else:
            err(f"/dev/{link} missing or broken")
            report.add(f"udev {link}", "err", "Check 20-modem-sierra-wireless.rules")

    # ── 2.11 Internet reachability ────────────────────────────────────────────
    subhead("2.11  Internet reachability")
    ping_out, _, ping_rc = run("ping -c 3 -W 5 8.8.8.8", timeout=20)
    if ping_rc == 0:
        ok("ping 8.8.8.8 success")
        report.add("Internet ping", "ok")
    else:
        err("Cannot reach 8.8.8.8 — no internet")
        report.add("Internet ping", "err")

    # ── 2.12 PPP log tail ─────────────────────────────────────────────────────
    subhead("2.12  internet_ppp.log (last 10 lines)")
    ppp_log = run_ok("tail -10 /var/log/internet_ppp.log 2>/dev/null")
    if ppp_log:
        for line in ppp_log.splitlines():
            print(f"    {line}")
        report.add("PPP log", "info", "Shown above")
    else:
        warn("/var/log/internet_ppp.log not found or empty")
        report.add("PPP log", "warn")

# ──────────────────────────────────────────────────────────────────────────────
# Section 3 – OpenVPN
# ──────────────────────────────────────────────────────────────────────────────
def check_vpn(report, fix=False):
    head("3. OPENVPN TUNNELS")

    for iface in ("tun0", "tun1"):
        tun = run_ok(f"ip addr show {iface} 2>/dev/null")
        if tun and "inet" in tun:
            ok(f"{iface} UP with IP")
            report.add(f"VPN {iface}", "ok")
        else:
            err(f"{iface} NOT up")
            report.add(f"VPN {iface}", "err", "Check openvpn@client_30 / client_31")

    # Dispatcher unit
    disp = run_ok("systemctl is-active openvpn.service 2>/dev/null")
    info(f"openvpn.service state: {disp}")
    if disp in ("active", "exited"):
        ok("openvpn.service OK (exited is normal for dispatcher)")
        report.add("openvpn.service", "ok", disp)
    else:
        warn(f"openvpn.service: {disp}")
        report.add("openvpn.service", "warn", disp)

    # Instance units
    for inst in ("openvpn@client_30", "openvpn@client_31"):
        state = run_ok(f"systemctl is-active {inst} 2>/dev/null") or "unknown"
        if state == "active":
            ok(f"{inst}: active")
            report.add(inst, "ok")
        else:
            err(f"{inst}: {state}")
            report.add(inst, "err", state)
            if fix:
                info(f"Starting {inst}…")
                run(f"systemctl start {inst}")
                time.sleep(5)
                state2 = run_ok(f"systemctl is-active {inst}") or "unknown"
                report.add(f"{inst} fix", "ok" if state2 == "active" else "err", state2)

    # OpenWireless AP reachability
    ssh_ap = run_ok("ssh -o ConnectTimeout=5 -o BatchMode=yes 192.168.150.203 exit 2>/dev/null")
    if ssh_ap is not None:
        ok("OpenWireless AP (192.168.150.203) reachable via SSH")
        report.add("WAP SSH", "ok")
    else:
        warn("Cannot reach WAP at 192.168.150.203 — may need network config fix (see procedure §12.3)")
        report.add("WAP SSH", "warn", "Try: ssh 192.168.1.1 and fix /etc/config/network")

# ──────────────────────────────────────────────────────────────────────────────
# Section 4 – WiFi / signal
# ──────────────────────────────────────────────────────────────────────────────
def check_wifi(report, fix=False):
    head("4. WIFI / WAP SIGNAL")

    # Find WiFi interface
    wif = run_ok("ip link show | grep -oP 'wl\\w+'") or \
          run_ok("iw dev | grep Interface | awk '{print $2}'")
    if not wif:
        warn("No WiFi interface found")
        report.add("WiFi interface", "warn")
        return
    wif = wif.splitlines()[0].strip()
    ok(f"WiFi interface: {wif}")
    report.add("WiFi interface", "ok", wif)

    # Bring it up
    run(f"ip link set {wif} up")
    time.sleep(1)

    # Scan for Avianca SSID
    scan = run_ok(f"iw dev {wif} scan 2>/dev/null | grep -B3 'SSID: avianca'")
    if scan:
        ok("Avianca SSID visible in scan")
        signal_match = re.search(r"signal:\s*([\-\d.]+)", scan)
        if signal_match:
            dbm = float(signal_match.group(1))
            info(f"Avianca signal: {dbm} dBm")
            if dbm >= -70:
                ok(f"Signal strength good ({dbm} dBm)")
                report.add("Avianca WiFi signal", "ok", f"{dbm} dBm")
            elif dbm >= -80:
                warn(f"Signal moderate ({dbm} dBm)")
                report.add("Avianca WiFi signal", "warn", f"{dbm} dBm")
            else:
                err(f"Signal weak ({dbm} dBm)")
                report.add("Avianca WiFi signal", "err", f"{dbm} dBm")
    else:
        warn("Avianca SSID not detected in WiFi scan")
        report.add("Avianca WiFi signal", "warn", "SSID not found")

    # SSID from upgrade_wap.bash
    ssid_hint = run_ok("grep -i avianca /root/upgrade_wap.bash 2>/dev/null | head -3")
    if ssid_hint:
        info(f"SSID config from upgrade_wap.bash: {ssid_hint[:80]}")

# ──────────────────────────────────────────────────────────────────────────────
# Section 5 – Cron / autopilot / internet-p100.py
# ──────────────────────────────────────────────────────────────────────────────
def check_cron(report, fix=False):
    head("5. CRON / AUTOPILOT / INTERNET-P100")

    # Crontab check for internet-p100.py
    cron = run_ok("crontab -l 2>/dev/null || cat /etc/crontab 2>/dev/null")
    if cron:
        if "internet-p100.py" in cron:
            commented = bool(re.search(r"^#.*internet-p100\.py", cron, re.MULTILINE))
            if commented:
                warn("internet-p100.py is COMMENTED OUT in crontab")
                report.add("internet-p100 cron", "warn", "Commented — re-enable when done troubleshooting")
            else:
                ok("internet-p100.py active in crontab")
                report.add("internet-p100 cron", "ok")
        else:
            warn("internet-p100.py not found in crontab")
            report.add("internet-p100 cron", "warn")
    else:
        warn("Could not read crontab")
        report.add("internet-p100 cron", "warn")

    # Autopilot
    autopilot_status = run_ok("autopilot status 2>/dev/null")
    if autopilot_status:
        ok(f"autopilot status: {autopilot_status[:80]}")
        report.add("autopilot", "ok")
    else:
        warn("autopilot command not found or returned nothing")
        report.add("autopilot", "warn")

    # ADSB reader
    adsb_out, _, adsb_rc = run(
        "timeout 5 /root/bin/python /root/python-ify/bin/read_adsb 2>&1 | head -5",
        timeout=10
    )
    if adsb_rc == 0 and adsb_out:
        ok(f"read_adsb responds: {adsb_out[:60]}")
        report.add("ADSB reader", "ok")
    else:
        warn(f"read_adsb did not respond (may be OK if modem active): {adsb_out[:60]}")
        report.add("ADSB reader", "warn")

# ──────────────────────────────────────────────────────────────────────────────
# Section 6 – Post-restore one-liner
# ──────────────────────────────────────────────────────────────────────────────
def post_restore_check(node_label):
    head("6. POST-RESTORE VALIDATION")
    serial = run_ok("dmidecode | grep 'Serial Number' | head -1") or "UNKNOWN"
    prefix = "W" if "W" in serial else ("K" if "K" in serial else "?")
    grep_pat = f'"Number: {prefix}"'
    cmd = (
        f"ip a | grep -E 'ppp0|tun' && "
        f"dmidecode | grep {grep_pat} && "
        f"ping 8.8.8.8 -c 3 && "
        f"autopilot status && "
        f'echo " " && echo "Date today: $(date)"'
    )
    info("Running post-restore one-liner…")
    print(f"  {CYAN}CMD:{RESET} {cmd}")
    out, err_s, rc = run(cmd, timeout=30)
    if out:
        for line in out.splitlines():
            print(f"    {line}")
    if rc == 0:
        ok("Post-restore check PASSED")
    else:
        warn(f"Post-restore check had issues (rc={rc})")

# ──────────────────────────────────────────────────────────────────────────────
# Fix helpers
# ──────────────────────────────────────────────────────────────────────────────
def _fix_usb_composition(report):
    """Switch modem from MBIM to QMI using AT command via pyserial."""
    info("Attempting USB composition switch MBIM → QMI…")
    at_port = find_at_port()
    if not at_port:
        err("No AT port available — cannot switch USB composition remotely")
        report.add("USB comp fix", "err", "No AT port")
        return
    # Standard QMI bitmask for EM7565
    resp = send_at(at_port, 'AT!USBCOMP=1,1,"0000100D"', timeout=10)
    info(f"AT!USBCOMP response: {resp[:80]}")
    resp2 = send_at(at_port, "AT!RESET", timeout=5)
    info(f"AT!RESET response: {resp2[:40]}")
    warn("Modem will reset — wait ~60s then re-run diagnostics")
    report.add("USB comp fix", "ok" if "OK" in resp else "warn", resp[:60])

def _install_modem_enable_service(report):
    """Deploy the modem-enable.service systemd unit."""
    svc_path = f"/etc/systemd/system/{MODEM_ENABLE_UNIT}"
    try:
        with open(svc_path, "w") as f:
            f.write(MODEM_ENABLE_SVC)
        run("systemctl daemon-reload")
        run(f"systemctl enable {MODEM_ENABLE_UNIT}")
        run(f"systemctl start {MODEM_ENABLE_UNIT}")
        ok(f"Installed and enabled {MODEM_ENABLE_UNIT}")
        report.add("modem-enable install", "ok")
    except Exception as e:
        err(f"Failed to install {MODEM_ENABLE_UNIT}: {e}")
        report.add("modem-enable install", "err", str(e))

# ──────────────────────────────────────────────────────────────────────────────
# Battery reminder (manual — cannot be automated)
# ──────────────────────────────────────────────────────────────────────────────
def battery_reminder():
    head("⚡ BATTERY CHECK REMINDER (manual step)")
    print("""
  Before powering on, verify the front-panel battery status LED:
    🔴 Red (persistent)  → Battery fault — replace before restore
    🟡 Amber             → Charging — wait before proceeding
    🟢 Green             → Fully charged — OK to continue

  Steps: unscrew front panel → remove battery → place on charger → observe LED.
""")

# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Immfly P100 Diagnostic & Fix Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 p100_doctor.py                     # full read-only scan
  python3 p100_doctor.py --fix               # scan + attempt fixes
  python3 p100_doctor.py --section modem     # only modem section
  python3 p100_doctor.py --node N133AV       # label the node in output
  python3 p100_doctor.py --post-restore      # post-restore validation
"""
    )
    parser.add_argument("--fix",   action="store_true", help="Attempt auto-remediation")
    parser.add_argument("--section", choices=["system","modem","vpn","wifi","cron"],
                        help="Run only one section")
    parser.add_argument("--node",  default="", help="Node label (e.g. N133AV)")
    parser.add_argument("--post-restore", action="store_true",
                        help="Run post-restore validation one-liner")
    parser.add_argument("--battery", action="store_true",
                        help="Show battery check reminder")
    args = parser.parse_args()

    print(f"\n{BOLD}{CYAN}╔══════════════════════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{CYAN}║   Immfly P100 Doctor  –  {datetime.now():%Y-%m-%d %H:%M:%S}   ║{RESET}")
    print(f"{BOLD}{CYAN}╚══════════════════════════════════════════════════════════╝{RESET}")
    if args.node:
        print(f"  Node: {BOLD}{args.node}{RESET}")
    if args.fix:
        print(f"  {YELLOW}Mode: DIAGNOSTIC + FIX{RESET}")
    else:
        print(f"  Mode: READ-ONLY DIAGNOSTIC  (pass --fix to attempt repairs)")

    report = Report()

    if args.battery:
        battery_reminder()
        return

    if args.post_restore:
        post_restore_check(args.node)
        return

    sec = args.section

    if not sec or sec == "system":
        check_system(report, args.node, fix=args.fix)

    if not sec or sec == "modem":
        check_modem(report, fix=args.fix)

    if not sec or sec == "vpn":
        check_vpn(report, fix=args.fix)

    if not sec or sec == "wifi":
        check_wifi(report, fix=args.fix)

    if not sec or sec == "cron":
        check_cron(report, fix=args.fix)

    report.summary()

    print(f"\n  {CYAN}Tip:{RESET} Re-run with {BOLD}--fix{RESET} to attempt automated remediation.")
    print(f"  {CYAN}Tip:{RESET} After any modem change, wait 60 s then run again to verify.\n")

if __name__ == "__main__":
    main()
