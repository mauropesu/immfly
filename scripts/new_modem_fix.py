#!/usr/bin/env python3
"""
Fleet Modem Auto-Fix Script
Sierra Wireless EM7565 / Movistar Colombia / Ubuntu 20.04
Version: 1.2

Automatically diagnoses and fixes:
  1. MBIM → QMI USB composition switch
  2. Firmware upgrade (01.08.04.00 → 01.14.02.00)
  3. modem-enable.service installation
  4. LCP keepalive configuration
  5. PPP restart with clean modem state

Usage:
  python3 modem_fix.py           # Full auto-fix
  python3 modem_fix.py --dry-run # Diagnose only, no changes
  python3 modem_fix.py --install-deps # Install dependencies (needs ethernet)
"""

import subprocess
import os
import sys
import time
import re
import glob
import argparse
from datetime import datetime

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):     print(f"  {GREEN}✓{RESET} {msg}")
def warn(msg):   print(f"  {YELLOW}⚠{RESET} {msg}")
def fail(msg):   print(f"  {RED}✗{RESET} {msg}")
def info(msg):   print(f"  {CYAN}→{RESET} {msg}")
def step(msg):   print(f"\n{BOLD}{CYAN}[STEP]{RESET} {BOLD}{msg}{RESET}")
def section(msg):
    print(f"\n{BOLD}{'═'*60}{RESET}")
    print(f"{BOLD}  {msg}{RESET}")
    print(f"{BOLD}{'═'*60}{RESET}")

def run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "timeout", 1
    except Exception as e:
        return "", str(e), 1

# ── Config ────────────────────────────────────────────────────────────────────
MOVISTAR_MCC      = "732123"
AT_BAUDRATE       = 115200
FW_TARGET         = "SWI9X50C_01.14.02.00"
FW_OLD            = "SWI9X50C_01.08.04.00"
MODEM_ENABLE_SVC  = "/etc/systemd/system/modem-enable.service"
PPP_PEER_FILE     = "/etc/ppp/peers/provider"
PING_HOST         = "8.8.8.8"
QMI_BITMASK_NEW   = "0000500D"   # newer modem variant
QMI_BITMASK_OLD   = "0000010D"   # older modem variant (N113AV style)
ETHERNET_CMD      = ("dhclient -v br0 && ip route delete default && "
                     "ip route add default via 10.201.52.1 dev br0")

# Firmware archive — must be uploaded to /scripts/ before running
FW_ARCHIVE_NAME   = "SLQS04.00.27-lite.bin.tar.xz"
FW_ARCHIVE_PATH   = f"/scripts/{FW_ARCHIVE_NAME}"
FW_EXTRACT_BASE   = "/scripts"
FW_EXTRACTED_DIR  = "/scripts/SLQS04.00.27-lite.bin"
FW_BIN_DIR        = ("SLQS04.00.27-lite.bin/SampleApps/"
                     "lite-fw-download/bin")
FW_SUBDIR         = "swi_fw0105"

# ── Dependency management ─────────────────────────────────────────────────────

def check_internet():
    _, _, rc = run("ping -c 1 -W 3 8.8.8.8", timeout=8)
    return rc == 0

def check_dependencies():
    try:
        import serial
        pyserial_ok = True
    except ImportError:
        pyserial_ok = False
    _, _, rc = run("which qmi-firmware-update")
    qmi_ok = rc == 0
    return pyserial_ok, qmi_ok

def install_dependencies():
    section("Installing Dependencies")
    pyserial_ok, qmi_ok = check_dependencies()
    if pyserial_ok and qmi_ok:
        ok("All dependencies already installed")
        return True

    if not check_internet():
        print(f"""
{RED}{BOLD}✗ No internet connection detected{RESET}

  Missing:
{'' if pyserial_ok else '    • pyserial'}
{'' if qmi_ok else '    • libqmi-utils (qmi-firmware-update)'}

  {BOLD}Connect ethernet first:{RESET}
  {CYAN}{ETHERNET_CMD}{RESET}

  Then re-run:
  {CYAN}python3 {sys.argv[0]} --install-deps{RESET}
""")
        return False

    ok("Internet available")
    if not pyserial_ok:
        info("Installing pyserial...")
        _, err, rc = run("pip install pyserial --break-system-packages -q",
                         timeout=60)
        if rc == 0:
            ok("pyserial installed")
        else:
            fail(f"pyserial install failed: {err}")
            return False

    if not qmi_ok:
        info("Installing libqmi-utils...")
        _, err, rc = run("apt-get install -y libqmi-utils", timeout=120)
        if rc == 0:
            ok("libqmi-utils installed")
        else:
            fail(f"libqmi-utils install failed: {err}")
            return False

    ok("All dependencies installed")
    return True

# ── Firmware archive handling ─────────────────────────────────────────────────

def find_fw_files():
    """
    Locate firmware CWE+NVU files.
    Priority:
      1. Already extracted directory
      2. Extract from archive if present
    Returns (fw_dir_path, error_message)
    """
    # Check if already extracted
    for fw_dir in glob.glob(
            f"{FW_EXTRACT_BASE}/**/{FW_SUBDIR}", recursive=True):
        cwe = glob.glob(os.path.join(fw_dir, "*.cwe"))
        nvu = glob.glob(os.path.join(fw_dir, "*.nvu"))
        if cwe and nvu:
            return fw_dir, None

    # Not extracted — check for archive
    if not os.path.exists(FW_ARCHIVE_PATH):
        return None, (
            f"Firmware archive not found: {FW_ARCHIVE_PATH}\n"
            f"    Upload the file '{FW_ARCHIVE_NAME}' to /scripts/ "
            f"before running this script."
        )

    # Archive exists — extract it
    return None, "NEEDS_EXTRACTION"


def extract_fw_archive(dry_run=False):
    """Extract the firmware archive. Returns fw_dir path or None."""
    step("Extracting firmware archive")

    if not os.path.exists(FW_ARCHIVE_PATH):
        fail(f"Archive not found: {FW_ARCHIVE_PATH}")
        fail(f"Upload '{FW_ARCHIVE_NAME}' to /scripts/ and re-run.")
        return None

    size_out, _, _ = run(f"du -sh {FW_ARCHIVE_PATH}")
    info(f"Archive: {FW_ARCHIVE_PATH} ({size_out.split()[0] if size_out else '?'})")

    if dry_run:
        info(f"[DRY RUN] Would extract {FW_ARCHIVE_PATH} to {FW_EXTRACT_BASE}/")
        # Return expected path for dry-run checks
        return os.path.join(FW_EXTRACT_BASE, FW_BIN_DIR, FW_SUBDIR)

    # Check if xz is available
    _, _, rc = run("which xz")
    if rc != 0:
        info("xz not found, installing...")
        run("apt-get install -y xz-utils", timeout=60)

    info(f"Extracting (this may take a minute)...")
    out, err, rc = run(
        f"tar -xf {FW_ARCHIVE_PATH} -C {FW_EXTRACT_BASE}",
        timeout=300
    )

    if rc != 0:
        fail(f"Extraction failed: {err}")
        return None

    # Find the fw directory after extraction
    for fw_dir in glob.glob(
            f"{FW_EXTRACT_BASE}/**/{FW_SUBDIR}", recursive=True):
        cwe = glob.glob(os.path.join(fw_dir, "*.cwe"))
        nvu = glob.glob(os.path.join(fw_dir, "*.nvu"))
        if cwe and nvu:
            ok(f"Firmware files extracted: {fw_dir}")
            info(f"  CWE: {os.path.basename(cwe[0])}")
            info(f"  NVU: {os.path.basename(nvu[0])}")
            return fw_dir

    fail("Extraction completed but firmware files not found inside archive")
    return None

# ── AT communication ──────────────────────────────────────────────────────────

def find_at_port():
    try:
        import serial
    except ImportError:
        return None

    for port in sorted(glob.glob("/dev/ttyUSB*"), reverse=True):
        held, _, _ = run(f"fuser {port} 2>/dev/null")
        if held.strip():
            continue
        try:
            s = serial.Serial(port, AT_BAUDRATE, timeout=3,
                              rtscts=False, dsrdtr=False)
            time.sleep(0.5)
            s.reset_input_buffer()
            s.write(b"AT\r")
            time.sleep(2)
            out = s.read(200).decode(errors="ignore")
            s.close()
            if "OK" in out and "BusyBox" not in out and "ash" not in out:
                return port
        except Exception:
            continue
    return None

def send_at(port, cmd, wait=5):
    try:
        import serial
        s = serial.Serial(port, AT_BAUDRATE, timeout=wait+2,
                          rtscts=False, dsrdtr=False)
        time.sleep(0.5)
        s.reset_input_buffer()
        s.write((cmd + "\r").encode())
        time.sleep(wait)
        out = b""
        deadline = time.time() + wait + 1
        while time.time() < deadline:
            if s.in_waiting:
                out += s.read(s.in_waiting)
            time.sleep(0.2)
        s.close()
        return out.decode(errors="ignore").strip()
    except Exception as e:
        return f"ERROR: {e}"

# ── Diagnosis ─────────────────────────────────────────────────────────────────

def diagnose():
    state = {
        "usb_mode":          None,
        "firmware":          None,
        "firmware_old":      False,
        "at_port":           None,
        "modem_enabled":     False,
        "registered":        False,
        "operator":          None,
        "ppp_running":       False,
        "ppp_rx_ok":         False,
        "ping_ok":           False,
        "modem_enable_svc":  False,
        "lcp_ok":            False,
        "fw_files":          None,     # path to swi_fw0105/
        "fw_archive_exists": False,    # archive file present
        "cdc_wdm":           None,
        "pyserial_ok":       False,
        "qmi_ok":            False,
    }

    state["pyserial_ok"], state["qmi_ok"] = check_dependencies()

    # USB composition
    out, _, _ = run("usb-devices 2>/dev/null")
    sierra = ""
    cap = False
    for line in out.splitlines():
        if "Sierra" in line:
            cap = True
            sierra = ""
        if cap:
            sierra += line + "\n"
            if len(sierra.splitlines()) > 15:
                break
    if "qmi_wwan" in sierra:
        state["usb_mode"] = "QMI"
    elif "cdc_mbim" in sierra:
        state["usb_mode"] = "MBIM"

    # cdc-wdm
    wdm = glob.glob("/dev/cdc-wdm*")
    state["cdc_wdm"] = wdm[0] if wdm else None

    # ModemManager / firmware
    mm_out, _, _ = run("mmcli -m 0 2>/dev/null")
    fw_m = re.search(r"firmware revision:\s+(\S+)", mm_out)
    if fw_m:
        state["firmware"] = fw_m.group(1)
        state["firmware_old"] = FW_OLD in fw_m.group(1)
    state_m = re.search(r"state:\s+(\S+)", mm_out)
    if state_m:
        state["modem_enabled"] = state_m.group(1) in (
            "registered", "enabled", "searching", "connecting")
        state["registered"] = state_m.group(1) == "registered"
    op_m = re.search(r"operator id:\s+(\S+)", mm_out)
    if op_m:
        state["operator"] = op_m.group(1)

    # pppd
    pppd_out, _, _ = run("ps aux | grep pppd | grep -v grep")
    state["ppp_running"] = bool(pppd_out.strip())

    # ppp0 RX
    rx_out, _, rc = run("ip -s link show ppp0 2>/dev/null")
    if rc == 0:
        lines = rx_out.splitlines()
        for i, line in enumerate(lines):
            if "RX:" in line and i+1 < len(lines):
                try:
                    rx = int(lines[i+1].split()[0])
                    state["ppp_rx_ok"] = rx > 62
                except:
                    pass

    # Ping
    _, _, rc = run(f"ping -c 2 -W 3 -I ppp0 {PING_HOST} 2>/dev/null",
                   timeout=15)
    state["ping_ok"] = rc == 0

    # modem-enable.service
    state["modem_enable_svc"] = os.path.exists(MODEM_ENABLE_SVC)

    # LCP keepalive
    if os.path.exists(PPP_PEER_FILE):
        with open(PPP_PEER_FILE) as f:
            content = f.read()
        state["lcp_ok"] = ("lcp-echo-interval" in content and
                           "persist" in content)

    # Firmware files / archive
    state["fw_archive_exists"] = os.path.exists(FW_ARCHIVE_PATH)
    fw_dir, err = find_fw_files()
    if fw_dir:
        state["fw_files"] = fw_dir
    # (extraction happens during fix phase)

    # AT port
    mm_inactive = run("systemctl is-active ModemManager")[2] != 0
    if state["pyserial_ok"] and (mm_inactive or state["usb_mode"] == "MBIM"):
        state["at_port"] = find_at_port()

    return state


def print_diagnosis(state):
    section("DIAGNOSIS")

    print(f"\n  {BOLD}Dependencies:{RESET}")
    ok("pyserial installed") if state["pyserial_ok"] else \
        fail("pyserial NOT installed — needed for AT commands")
    ok("qmi-firmware-update installed") if state["qmi_ok"] else \
        warn("qmi-firmware-update NOT installed — needed for firmware upgrade")

    print(f"\n  {BOLD}Firmware Files:{RESET}")
    if state["fw_files"]:
        ok(f"Firmware files ready: {state['fw_files']}")
    elif state["fw_archive_exists"]:
        ok(f"Archive found: {FW_ARCHIVE_PATH} (will be extracted automatically)")
    else:
        fail(f"Firmware archive NOT found: {FW_ARCHIVE_PATH}")
        fail(f"Upload '{FW_ARCHIVE_NAME}' to /scripts/ before running fixes")

    print(f"\n  {BOLD}Modem State:{RESET}")
    checks = [
        ("USB Mode",
         state["usb_mode"] or "unknown",
         state["usb_mode"] == "QMI",
         "MBIM — needs switch to QMI"),
        ("Firmware",
         state["firmware"] or "unknown",
         state["firmware"] and not state["firmware_old"],
         f"Old ({FW_OLD}) — upgrade needed to {FW_TARGET}"),
        ("Modem Enabled",
         "yes" if state["modem_enabled"] else "no",
         state["modem_enabled"],
         "Disabled — needs mmcli -m 0 --enable"),
        ("Registered (Movistar)",
         state["operator"] or "none",
         state["registered"] and state["operator"] == MOVISTAR_MCC,
         f"Not on Movistar ({state['operator']})"),
        ("pppd Running",
         "yes" if state["ppp_running"] else "no",
         state["ppp_running"],
         "pppd not running"),
        ("PPP RX Traffic",
         "ok" if state["ppp_rx_ok"] else "frozen at 62 bytes",
         state["ppp_rx_ok"],
         "RX frozen — dead session"),
        ("Ping via ppp0",
         "ok" if state["ping_ok"] else "failed",
         state["ping_ok"],
         "No internet via cellular"),
        ("modem-enable.service",
         "installed" if state["modem_enable_svc"] else "missing",
         state["modem_enable_svc"],
         "Not installed — modem won't auto-enable after boot"),
        ("LCP Keepalive",
         "ok" if state["lcp_ok"] else "missing",
         state["lcp_ok"],
         "Missing lcp-echo-interval/persist"),
    ]

    all_good = True
    for label, value, good, bad_msg in checks:
        if good:
            ok(f"{label}: {value}")
        else:
            fail(f"{label}: {bad_msg}")
            all_good = False

    return all_good

# ── Fix functions ─────────────────────────────────────────────────────────────

def fix_firmware(state, dry_run):
    step("Upgrading modem firmware")

    # Resolve firmware files — extract if needed
    fw_dir = state.get("fw_files")
    if not fw_dir:
        if state["fw_archive_exists"]:
            fw_dir = extract_fw_archive(dry_run)
            if not fw_dir:
                fail("Extraction failed — cannot upgrade firmware")
                return False
        else:
            fail(f"Firmware archive not found: {FW_ARCHIVE_PATH}")
            fail(f"Upload '{FW_ARCHIVE_NAME}' to /scripts/ and re-run.")
            return False

    if not state["qmi_ok"]:
        fail("qmi-firmware-update not installed")
        info(f"Connect ethernet and run: python3 {sys.argv[0]} --install-deps")
        return False

    cwe = glob.glob(os.path.join(fw_dir, "*.cwe"))
    nvu = glob.glob(os.path.join(fw_dir, "*.nvu"))
    if not cwe or not nvu:
        fail(f"CWE or NVU file missing in {fw_dir}")
        return False

    info(f"CWE: {os.path.basename(cwe[0])}")
    info(f"NVU: {os.path.basename(nvu[0])}")

    if dry_run:
        info("[DRY RUN] Would upgrade firmware")
        return True

    run("systemctl stop ModemManager")
    time.sleep(3)

    info("Flashing (2-3 minutes — DO NOT interrupt)...")
    cmd = (f"qmi-firmware-update -u -w {state['cdc_wdm']} "
           f"--device-open-auto --ignore-mm-runtime-check "
           f"{cwe[0]} {nvu[0]}")
    out, err, rc = run(cmd, timeout=300)

    if "finished successfully" in out:
        ok(f"Firmware upgraded to {FW_TARGET}")
        state["fw_files"] = fw_dir  # update state
        time.sleep(15)
        wdm = glob.glob("/dev/cdc-wdm*")
        state["cdc_wdm"] = wdm[0] if wdm else state["cdc_wdm"]
        return True
    else:
        fail("Firmware upgrade failed")
        info(out[-500:] if len(out) > 500 else out)
        return False


def fix_usb_composition(state, dry_run):
    step("Switching USB composition MBIM → QMI")

    if not state["pyserial_ok"]:
        fail("pyserial not installed")
        info(f"Connect ethernet and run: python3 {sys.argv[0]} --install-deps")
        return False

    run("systemctl stop ModemManager")
    run("pkill -9 pppd 2>/dev/null")
    time.sleep(5)

    at_port = find_at_port()
    if not at_port:
        fail("No AT port responding")
        return False

    info(f"AT port: {at_port}")

    if dry_run:
        info("[DRY RUN] Would switch USB composition to QMI")
        return True

    resp = send_at(at_port, 'AT!ENTERCND="A710"', 3)
    if "OK" not in resp:
        fail("Could not unlock modem for AT! commands")
        return False

    qmi_bitmask = None
    for bitmask in [QMI_BITMASK_NEW, QMI_BITMASK_OLD]:
        resp = send_at(at_port, f"AT!USBCOMP=1,1,{bitmask}", 5)
        if "OK" in resp and "ERROR" not in resp:
            qmi_bitmask = bitmask
            ok(f"USB composition accepted: {bitmask} (QMI)")
            break
        else:
            info(f"Bitmask {bitmask} not accepted, trying next...")

    if not qmi_bitmask:
        fail("Neither QMI bitmask accepted")
        resp = send_at(at_port, "AT!USBCOMP=?", 5)
        info(f"Valid options: {resp[:300]}")
        return False

    info("Resetting modem (35 seconds)...")
    send_at(at_port, "AT!RESET", 3)
    time.sleep(35)

    out, _, _ = run("usb-devices 2>/dev/null")
    if "qmi_wwan" in out:
        ok("Modem now in QMI mode ✓")
        return True
    elif "cdc_mbim" in out:
        fail("Modem still in MBIM mode after reset")
        return False
    else:
        warn("USB mode unclear — checking dmesg")
        dmesg, _, _ = run("dmesg | grep -iE 'qmi|mbim' | tail -5")
        info(dmesg)
        return True


def install_modem_enable_service(dry_run):
    step("Installing modem-enable.service")
    content = """[Unit]
Description=Enable Sierra Wireless modem via ModemManager
After=ModemManager.service
Requires=ModemManager.service

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'sleep 15 && mmcli -m 0 --enable'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""
    if dry_run:
        info(f"[DRY RUN] Would install {MODEM_ENABLE_SVC}")
        return True
    with open(MODEM_ENABLE_SVC, "w") as f:
        f.write(content)
    run("systemctl daemon-reload")
    run("systemctl enable modem-enable.service")
    ok("modem-enable.service installed and enabled")
    return True


def fix_lcp_keepalive(dry_run):
    step("Fixing PPP LCP keepalive config")
    if not os.path.exists(PPP_PEER_FILE):
        warn(f"{PPP_PEER_FILE} not found — skipping")
        return False

    with open(PPP_PEER_FILE) as f:
        content = f.read()

    changes = []
    if "lcp-echo-failure 2" in content and "lcp-echo-interval" not in content:
        content = content.replace("lcp-echo-failure 2",
                                  "lcp-echo-interval 10\nlcp-echo-failure 3")
        changes.append("lcp-echo-interval 10")
    if "lcp-echo-failure" not in content:
        content += "\nlcp-echo-interval 10\nlcp-echo-failure 3\n"
        changes.append("added LCP keepalive")
    if not re.search(r"^persist\s*$", content, re.MULTILINE):
        content += "persist\n"
        changes.append("persist")
    if "maxfail" not in content:
        content += "maxfail 0\n"
        changes.append("maxfail 0")
    if "holdoff" not in content:
        content += "holdoff 5\n"
        changes.append("holdoff 5")

    if changes:
        if not dry_run:
            with open(PPP_PEER_FILE, "w") as f:
                f.write(content)
            ok(f"PPP config updated: {', '.join(changes)}")
        else:
            info(f"[DRY RUN] Would update PPP config: {', '.join(changes)}")
    else:
        ok("PPP config already correct")
    return True


def fix_ppp_connection(dry_run):
    step("Restarting PPP connection cleanly")
    if dry_run:
        info("[DRY RUN] Would restart ModemManager and pppd")
        return True

    info("Starting ModemManager...")
    run("systemctl start ModemManager")
    time.sleep(20)

    info("Enabling modem...")
    run("mmcli -m 0 --enable")
    time.sleep(15)

    mm_out, _, _ = run("mmcli -m 0")
    reg_m = re.search(r"registration:\s+(\S+)", mm_out)
    op_m  = re.search(r"operator id:\s+(\S+)", mm_out)
    reg   = reg_m.group(1) if reg_m else "unknown"
    op    = op_m.group(1)  if op_m  else "unknown"

    if reg == "home" and op == MOVISTAR_MCC:
        ok("Registered on Movistar (home)")
    else:
        warn(f"Registration: {reg}, Operator: {op} — waiting 15s more...")
        time.sleep(15)

    info("Restarting pppd...")
    run("pkill -9 pppd 2>/dev/null")
    run("rm -f /var/lock/LCK..modemPPP /var/lock/LCK..ttyUSB* 2>/dev/null")
    time.sleep(3)
    run("pon &")
    time.sleep(20)

    rx_out, _, _ = run("ip -s link show ppp0 2>/dev/null")
    lines = rx_out.splitlines()
    rx_bytes = 0
    for i, line in enumerate(lines):
        if "RX:" in line and i+1 < len(lines):
            try:
                rx_bytes = int(lines[i+1].split()[0])
            except:
                pass

    if rx_bytes > 62:
        ok(f"ppp0 RX active: {rx_bytes:,} bytes")
    else:
        warn(f"ppp0 RX: {rx_bytes} bytes — may need more time")
    return True


def verify_connectivity():
    step("Verifying internet connectivity via ppp0")
    time.sleep(10)
    out, _, rc = run(f"ping -c 4 -W 3 -I ppp0 {PING_HOST}", timeout=25)
    if rc == 0:
        rtt_m = re.search(r"rtt .* = [\d.]+/([\d.]+)/", out)
        rtt   = rtt_m.group(1) if rtt_m else "?"
        ok(f"Internet working via ppp0 — avg RTT {rtt}ms")
        return True
    else:
        fail("Ping via ppp0 failed")
        diag = find_script("modem_diag.py")
        info(f"Run diagnostic: python3 {diag}")
        return False


def find_script(name):
    """Find a companion script in common locations."""
    search_dirs = [
        os.path.dirname(os.path.abspath(__file__)),
        "/usr/local/bin",
        "/scripts",
    ]
    for d in search_dirs:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return f"/usr/local/bin/{name}"


def self_install():
    """Copy this script to /usr/local/bin/ for convenient access."""
    src = os.path.abspath(__file__)
    dst = "/usr/local/bin/modem_fix.py"
    if src != dst:
        try:
            import shutil
            shutil.copy2(src, dst)
            os.chmod(dst, 0o755)
            info(f"Script installed to {dst} for future use")
        except Exception as e:
            warn(f"Could not self-install to {dst}: {e}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fleet Modem Auto-Fix Script v1.2")
    parser.add_argument("--dry-run", action="store_true",
                        help="Diagnose only — make no changes")
    parser.add_argument("--install-deps", action="store_true",
                        help="Install dependencies only (requires ethernet)")
    args = parser.parse_args()

    print(f"\n{BOLD}{'═'*60}{RESET}")
    print(f"{BOLD}  Fleet Modem Auto-Fix v1.2{RESET}")
    print(f"{BOLD}  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{RESET}")
    print(f"{BOLD}  Machine: {os.uname().nodename}{RESET}")
    if args.dry_run:
        print(f"  {YELLOW}{BOLD}MODE: DRY RUN (diagnosis only){RESET}")
    print(f"{BOLD}{'═'*60}{RESET}")

    if args.install_deps:
        success = install_dependencies()
        if success:
            self_install()
        sys.exit(0 if success else 1)

    # Install pyserial if missing (no internet check — just try pip)
    try:
        import serial
    except ImportError:
        warn("pyserial not found — attempting install...")
        _, _, rc = run("pip install pyserial --break-system-packages -q",
                       timeout=60)
        if rc != 0:
            warn("pyserial install failed — AT checks will be skipped")

    # Diagnose
    info("Running diagnosis...")
    state = diagnose()
    all_good = print_diagnosis(state)

    if all_good:
        print(f"\n{GREEN}{BOLD}✓ ALL CHECKS PASSED — No fixes needed!{RESET}\n")
        sys.exit(0)

    if args.dry_run:
        # Show what will be needed
        needs_archive   = state["firmware_old"] and not state["fw_files"]
        needs_internet  = (state["firmware_old"] and not state["qmi_ok"]) or \
                          (not state["pyserial_ok"])

        print(f"\n{YELLOW}{BOLD}DRY RUN complete.{RESET}")

        if needs_archive and not state["fw_archive_exists"]:
            print(f"\n{RED}  ✗ BLOCKING: Firmware archive required before fix can run:{RESET}")
            print(f"    Upload '{FW_ARCHIVE_NAME}' to /scripts/")
            print(f"    Then re-run this script.")

        if needs_internet:
            print(f"\n{YELLOW}  ⚠ Internet required for dependencies:{RESET}")
            if not state["qmi_ok"]:
                print(f"    • libqmi-utils")
            if not state["pyserial_ok"]:
                print(f"    • pyserial")
            print(f"\n  Connect ethernet:")
            print(f"  {CYAN}{ETHERNET_CMD}{RESET}")
            print(f"  then: {CYAN}python3 {sys.argv[0]} --install-deps{RESET}")
        print()
        sys.exit(0)

    # ── Apply fixes ───────────────────────────────────────────────────────────
    section("APPLYING FIXES")

    # BLOCKING CHECK 1: firmware archive required for old firmware
    if state["firmware_old"]:
        if not state["fw_files"] and not state["fw_archive_exists"]:
            print(f"""
{RED}{BOLD}✗ CANNOT PROCEED — Firmware archive missing{RESET}

  The modem firmware is outdated ({FW_OLD}) and must be upgraded,
  but the firmware archive was not found.

  {BOLD}Required file:{RESET} {FW_ARCHIVE_PATH}

  {BOLD}Steps to fix:{RESET}
  1. Upload '{FW_ARCHIVE_NAME}' to /scripts/ on this machine
  2. Re-run this script: {CYAN}python3 {sys.argv[0]}{RESET}
""")
            sys.exit(1)

    # BLOCKING CHECK 2: deps needed
    needs_qmi_install = state["firmware_old"] and not state["qmi_ok"]
    needs_serial      = state["usb_mode"] == "MBIM" and not state["pyserial_ok"]

    if needs_qmi_install or needs_serial:
        missing = []
        if needs_qmi_install: missing.append("libqmi-utils")
        if needs_serial:      missing.append("pyserial")
        print(f"\n{YELLOW}  Missing dependencies: {', '.join(missing)}{RESET}")
        print(f"  Connect ethernet and run first:")
        print(f"  {CYAN}{ETHERNET_CMD}{RESET}")
        print(f"  {CYAN}python3 {sys.argv[0]} --install-deps{RESET}")
        resp = input(f"\n  Continue anyway? [y/N]: ").strip().lower()
        if resp != "y":
            print("  Exiting.\n")
            sys.exit(0)

    # ── Fix order is critical ─────────────────────────────────────────────────
    # When modem is in MBIM mode with old firmware:
    #   Step A: Firmware upgrade FIRST (qmi-firmware-update works via MBIM)
    #   Step B: Composition switch AFTER (new firmware supports QMI bitmask)
    # When modem is already in QMI mode with old firmware:
    #   Step A: Firmware upgrade only
    # ─────────────────────────────────────────────────────────────────────────

    # Fix 1: Firmware upgrade — MUST happen before composition switch
    if state["firmware_old"]:
        if state["fw_files"] or state["fw_archive_exists"]:
            info("Old firmware detected — upgrading before composition switch")
            if not fix_firmware(state, False):
                fail("Firmware upgrade failed — cannot continue safely")
                fail("Do not attempt composition switch with old firmware")
                sys.exit(1)
        else:
            warn("Firmware is old but no archive available — skipping upgrade")

    # Fix 2: USB composition switch (now safe with new firmware)
    if state["usb_mode"] == "MBIM":
        # Refresh cdc-wdm path after potential firmware upgrade
        wdm = glob.glob("/dev/cdc-wdm*")
        if wdm:
            state["cdc_wdm"] = wdm[0]
        if not fix_usb_composition(state, False):
            fail("USB composition switch failed — cannot continue")
            sys.exit(1)

    # Fix 3: modem-enable.service
    if not state["modem_enable_svc"]:
        install_modem_enable_service(False)

    # Fix 4: LCP keepalive
    if not state["lcp_ok"]:
        fix_lcp_keepalive(False)

    # Fix 5: Restart PPP
    fix_ppp_connection(False)

    # Final verification
    success = verify_connectivity()

    print(f"\n{BOLD}{'═'*60}{RESET}")
    if success:
        print(f"{GREEN}{BOLD}  ✓ FIX COMPLETE — Internet restored on "
              f"{os.uname().nodename}!{RESET}")
    else:
        print(f"{YELLOW}{BOLD}  ⚠ PARTIAL — Some issues may remain.{RESET}")
        diag = find_script("modem_diag.py")
        print(f"  Run: {CYAN}python3 {diag}{RESET}")
    print(f"{BOLD}{'═'*60}{RESET}\n")
    # Install to /usr/local/bin for next time
    self_install()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    if os.geteuid() != 0:
        print(f"{RED}This script must be run as root.{RESET}")
        sys.exit(1)
    main()
