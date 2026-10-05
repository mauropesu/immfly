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
ETH_IFACE         = "br0"
DEFAULT_ROUTE_GW  = "10.201.52.1"
DHCLIENT_TIMEOUT  = 20  # seconds - never hang indefinitely waiting for a lease

# Firmware archive — must be uploaded to /scripts/ before running
FW_ARCHIVE_NAME   = "SLQS04.00.27-lite.bin.tar.xz"
FW_ARCHIVE_PATH   = f"/scripts/{FW_ARCHIVE_NAME}"
FW_EXTRACT_BASE   = "/scripts"
FW_EXTRACTED_DIR  = "/scripts/SLQS04.00.27-lite.bin"
FW_BIN_DIR        = ("SLQS04.00.27-lite.bin/SampleApps/"
                     "lite-fw-download/bin")
FW_SUBDIR         = "swi_fw0105"

# Offline dependency bundle — covers everything modem_fix.py needs to
# install (libqmi-utils, xz-utils, pyserial) without touching the network.
# Build once with internet on any P100 (Ubuntu 20.04/focal):
#
#   mkdir -p /tmp/modem_fix_offline/debs /tmp/modem_fix_offline/pip
#   cd /tmp/modem_fix_offline
#   apt-get install --download-only -y libqmi-utils xz-utils
#   cp /var/cache/apt/archives/*.deb debs/
#   pip download pyserial -d pip/ --no-binary :none:
#   tar -czf modem_fix_offline_packages_focal.tar.gz debs/ pip/
#
# then upload the resulting tar.gz to /scripts/ on each machine.
OFFLINE_BUNDLE_NAME = "modem_fix_offline_packages_focal.tar.gz"
OFFLINE_BUNDLE_PATH = f"/scripts/{OFFLINE_BUNDLE_NAME}"
OFFLINE_BUNDLE_DIR  = "/scripts/modem_fix_offline_packages_focal"
OFFLINE_DEBS_DIR    = f"{OFFLINE_BUNDLE_DIR}/debs"
OFFLINE_PIP_DIR      = f"{OFFLINE_BUNDLE_DIR}/pip"

# internet-p100.py cron job — must be paused during the fix, since it
# relaunches pppd every minute and competes for the AT port (ttyUSB port
# used by modemPPP) while this script is flashing firmware or switching
# USB composition.
CRON_ANSIBLE_FILE = "/etc/cron.d/ansible"
CRON_INTERNET_MARKER = "#Ansible: internet"

# ── Cron management (internet-p100.py) ────────────────────────────────────────

def is_internet_cron_active():
    """True if the internet-p100.py cron line is present and NOT commented out."""
    out, _, rc = run(f"grep -A1 '{CRON_INTERNET_MARKER}' {CRON_ANSIBLE_FILE}")
    if rc != 0 or not out:
        return None  # marker not found — file may differ from expected format
    lines = out.splitlines()
    if len(lines) < 2:
        return None
    job_line = lines[1].strip()
    return not job_line.startswith("#")

def pause_internet_cron():
    """Comment out the internet-p100.py cron line. Returns True if changed."""
    if not os.path.exists(CRON_ANSIBLE_FILE):
        warn(f"{CRON_ANSIBLE_FILE} not found — skipping cron pause")
        return False
    active = is_internet_cron_active()
    if active is None:
        warn("Could not locate internet-p100.py cron entry — skipping cron pause")
        return False
    if active is False:
        info("internet-p100.py cron already paused")
        return False
    _, err, rc = run(
        f"sed -i '/{CRON_INTERNET_MARKER}/{{n;s/^\\* \\* \\* \\* \\* root flock/#& /}}' "
        f"{CRON_ANSIBLE_FILE}"
    )
    if rc == 0 and is_internet_cron_active() is False:
        ok("Paused internet-p100.py cron job (will restore on exit)")
        return True
    else:
        warn(f"Failed to pause internet-p100.py cron job: {err}")
        return False

def resume_internet_cron():
    """Uncomment the internet-p100.py cron line if we paused it."""
    if not os.path.exists(CRON_ANSIBLE_FILE):
        return
    _, err, rc = run(
        f"sed -i '/{CRON_INTERNET_MARKER}/{{n;"
        f"s/^#\\* \\* \\* \\* \\* root flock/* * * * * root flock/}}' "
        f"{CRON_ANSIBLE_FILE}"
    )
    if rc == 0 and is_internet_cron_active() is True:
        ok("Restored internet-p100.py cron job")
    else:
        fail(f"Could not confirm internet-p100.py cron job was restored — "
             f"check {CRON_ANSIBLE_FILE} manually")

# ── Dependency management ─────────────────────────────────────────────────────

def check_internet():
    _, _, rc = run("ping -c 1 -W 3 8.8.8.8", timeout=8)
    return rc == 0

_bundle_extracted = False  # module-level cache so we only extract once per run

def ensure_offline_bundle_extracted():
    """Extract the offline bundle to /scripts/ once, if present. Returns True if usable."""
    global _bundle_extracted
    if _bundle_extracted:
        return True
    if not os.path.exists(OFFLINE_BUNDLE_PATH):
        return False

    info(f"Found offline package bundle: {OFFLINE_BUNDLE_NAME} — extracting...")
    run(f"mkdir -p {OFFLINE_BUNDLE_DIR}")
    _, err, rc = run(f"tar -xzf {OFFLINE_BUNDLE_PATH} -C {OFFLINE_BUNDLE_DIR}", timeout=60)
    if rc != 0:
        warn(f"Failed to extract {OFFLINE_BUNDLE_NAME}: {err}")
        return False

    _bundle_extracted = True
    return True

def install_libqmi_offline():
    """Install libqmi-utils from the offline bundle. Returns True if qmi-firmware-update works."""
    if not ensure_offline_bundle_extracted():
        return False
    debs = glob.glob(os.path.join(OFFLINE_DEBS_DIR, "*libqmi*.deb")) or \
           glob.glob(os.path.join(OFFLINE_DEBS_DIR, "*.deb"))
    if not debs:
        return False
    # dpkg -i first (no network); apt-get install -f only resolves from
    # the local .debs already present unless the bundle is incomplete.
    run(f"dpkg -i {OFFLINE_DEBS_DIR}/*.deb", timeout=60)
    run("apt-get install -f -y", timeout=60)
    _, _, rc = run("which qmi-firmware-update")
    if rc == 0:
        ok("libqmi-utils installed from offline bundle (no internet used)")
        return True
    return False

def install_xz_offline():
    """Install xz-utils from the offline bundle. Returns True if xz is available."""
    _, _, rc = run("which xz")
    if rc == 0:
        return True
    if not ensure_offline_bundle_extracted():
        return False
    debs = glob.glob(os.path.join(OFFLINE_DEBS_DIR, "*xz-utils*.deb"))
    if not debs:
        return False
    run(f"dpkg -i {' '.join(debs)}", timeout=30)
    run("apt-get install -f -y", timeout=60)
    _, _, rc = run("which xz")
    if rc == 0:
        ok("xz-utils installed from offline bundle (no internet used)")
        return True
    return False

def install_pyserial_offline():
    """Install pyserial from the offline bundle's local pip package. Returns True if importable."""
    if not ensure_offline_bundle_extracted():
        return False
    if not os.path.isdir(OFFLINE_PIP_DIR) or not glob.glob(os.path.join(OFFLINE_PIP_DIR, "*")):
        return False
    _, err, rc = run(
        f"pip install pyserial --no-index --find-links={OFFLINE_PIP_DIR} "
        f"--break-system-packages -q",
        timeout=60
    )
    if rc == 0:
        try:
            import serial  # noqa: F401
            ok("pyserial installed from offline bundle (no internet used)")
            return True
        except ImportError:
            pass
    warn(f"Offline pyserial install failed: {err}")
    return False

def try_ethernet_fallback():
    """
    Attempt to bring up internet via the physical ethernet port (br0), for
    use when cellular/PPP has no connectivity and a package install is
    needed. Checks for physical link before attempting DHCP, so it never
    hangs when no cable is plugged in. Returns True if internet became
    available.
    """
    _, _, rc = run(f"ip link show {ETH_IFACE}")
    if rc != 0:
        return False

    carrier, _, _ = run(f"cat /sys/class/net/{ETH_IFACE}/carrier")
    if carrier.strip() != "1":
        info(f"{ETH_IFACE} has no physical link — plug in an ethernet cable to use this fallback")
        return False

    info(f"Ethernet cable detected on {ETH_IFACE} — requesting DHCP (timeout {DHCLIENT_TIMEOUT}s)...")
    _, _, rc = run(f"timeout {DHCLIENT_TIMEOUT} dhclient -v {ETH_IFACE}",
                    timeout=DHCLIENT_TIMEOUT + 5)
    if rc != 0:
        warn(f"Cable connected on {ETH_IFACE} but no DHCP lease obtained in "
             f"{DHCLIENT_TIMEOUT}s — possible hangar wiring issue, not this machine")
        return False

    run("ip route delete default")
    run(f"ip route add default via {DEFAULT_ROUTE_GW} dev {ETH_IFACE}")

    if check_internet():
        ok(f"Ethernet fallback successful — internet available via {ETH_IFACE}")
        return True

    warn(f"Got a DHCP lease on {ETH_IFACE} but internet still unreachable")
    return False

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

    # Priority 1: offline bundle in /scripts/ — no network needed at all
    if not qmi_ok:
        if install_libqmi_offline():
            qmi_ok = True
    if not pyserial_ok:
        if install_pyserial_offline():
            pyserial_ok = True

    if pyserial_ok and qmi_ok:
        ok("All dependencies installed (offline)")
        return True

    # Priority 2: network (cellular or ethernet fallback)
    if not check_internet():
        info("No internet via current connection — checking for ethernet cable fallback...")
        if not try_ethernet_fallback():
            print(f"""
{RED}{BOLD}✗ No internet connection detected{RESET}

  Missing:
{'' if pyserial_ok else '    • pyserial'}
{'' if qmi_ok else '    • libqmi-utils (qmi-firmware-update)'}

  {BOLD}Options:{RESET}
  1. Upload '{OFFLINE_BUNDLE_NAME}' to /scripts/ for fully offline install
     (build it once with internet — see comment near OFFLINE_BUNDLE_NAME
     in this script's config section)
  2. Connect an ethernet cable to this unit and re-run:
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
    if not install_xz_offline():
        warn("xz not available offline — attempting network install as last resort...")
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
    run("pkill -9 pppd 2>/dev/null")
    time.sleep(3)

    info("Flashing (2-3 minutes — DO NOT interrupt)...")
    cmd = (f"qmi-firmware-update -u -w {state['cdc_wdm']} "
           f"--device-open-auto --ignore-mm-runtime-check "
           f"{cwe[0]} {nvu[0]}")
    out, err, rc = run(cmd, timeout=300)

    if "finished successfully" in out:
        ok(f"Firmware upgraded to {FW_TARGET}")
        state["fw_files"] = fw_dir
        info("Waiting 30 seconds for modem to fully boot after flash...")
        time.sleep(30)
        wdm = glob.glob("/dev/cdc-wdm*")
        state["cdc_wdm"] = wdm[0] if wdm else state["cdc_wdm"]
        return True
    else:
        fail("Firmware upgrade failed")
        info(out[-500:] if len(out) > 500 else out)
        return False


def free_modem_ports():
    """Stop all processes holding modem ports."""
    info("Freeing modem ports...")
    run("systemctl stop ModemManager 2>/dev/null")
    run("pkill -9 pppd 2>/dev/null")
    run("pkill -9 qmi-proxy 2>/dev/null")
    run("pkill -9 mbim-proxy 2>/dev/null")
    time.sleep(3)
    for port in glob.glob("/dev/ttyUSB*"):
        run(f"fuser -k {port} 2>/dev/null")
    run("fuser -k /dev/cdc-wdm0 2>/dev/null")
    time.sleep(3)


def fix_usb_composition(state, dry_run):
    step("Switching USB composition MBIM → QMI")

    if not state["pyserial_ok"]:
        fail("pyserial not installed")
        info(f"Connect ethernet and run: python3 {sys.argv[0]} --install-deps")
        return False

    free_modem_ports()

    # Retry finding AT port up to 3 times
    at_port = None
    for attempt in range(3):
        at_port = find_at_port()
        if at_port:
            break
        info(f"No AT port yet (attempt {attempt+1}/3) — waiting 5s...")
        time.sleep(5)

    if not at_port:
        fail("No AT port responding after 3 attempts")
        info("Port status:")
        for port in sorted(glob.glob("/dev/ttyUSB*")):
            held, _, _ = run(f"fuser {port} 2>/dev/null")
            status = f"held by PID {held.strip()}" if held.strip() else "free"
            info(f"  {port}: {status}")
        return False

    info(f"AT port: {at_port}")

    if dry_run:
        info("[DRY RUN] Would switch USB composition to QMI")
        return True

    resp = send_at(at_port, 'AT!ENTERCND="A710"', 3)
    if "OK" not in resp:
        fail("Could not unlock modem for AT! commands")
        return False

    # Query valid compositions first to understand what this modem supports
    comp_query = send_at(at_port, "AT!USBCOMP=?", 6)
    if comp_query:
        info(f"Supported compositions: {comp_query[:200]}")
    else:
        warn("AT!USBCOMP=? returned no data — modem may still be booting")
        info("Waiting 15 more seconds...")
        time.sleep(15)
        comp_query = send_at(at_port, "AT!USBCOMP=?", 6)
        if comp_query:
            info(f"Supported compositions: {comp_query[:200]}")

    # Determine QMI bitmask based on what the modem reports
    # Check if RMNET0 is in the valid options
    bitmasks_to_try = []
    if "RMNET0" in comp_query or "rmnet" in comp_query.lower():
        bitmasks_to_try = [QMI_BITMASK_OLD, QMI_BITMASK_NEW]  # 0000010D first
    else:
        bitmasks_to_try = [QMI_BITMASK_NEW, QMI_BITMASK_OLD]  # 0000500D first

    qmi_bitmask = None
    for bitmask in bitmasks_to_try:
        resp = send_at(at_port, f"AT!USBCOMP=1,1,{bitmask}", 5)
        if "OK" in resp and "ERROR" not in resp:
            qmi_bitmask = bitmask
            ok(f"USB composition accepted: {bitmask} (QMI)")
            break
        else:
            info(f"Bitmask {bitmask} not accepted ({repr(resp[:50])}), trying next...")

    if not qmi_bitmask:
        fail("Neither QMI bitmask accepted")
        info(f"Full AT!USBCOMP=? response: {comp_query}")
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
        sys.exit(0 if success else 1)

    # Install pyserial if missing — try offline bundle first, then network
    try:
        import serial
    except ImportError:
        warn("pyserial not found — attempting install...")
        if not install_pyserial_offline():
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
        info("Attempting automatic install (will use ethernet fallback if needed)...")
        if install_dependencies():
            # Re-check state after install
            pyserial_ok, qmi_ok = check_dependencies()
            state["pyserial_ok"], state["qmi_ok"] = pyserial_ok, qmi_ok
        else:
            print(f"  {CYAN}Manual fallback: connect ethernet, then run{RESET}")
            print(f"  {CYAN}python3 {sys.argv[0]} --install-deps{RESET}")
            resp = input(f"\n  Continue anyway? [y/N]: ").strip().lower()
            if resp != "y":
                print("  Exiting.\n")
                sys.exit(0)

    # ── Fix order ─────────────────────────────────────────────────────────────
    # MBIM mode + old firmware → upgrade firmware first, then switch to QMI
    # MBIM mode + new firmware → switch to QMI directly
    # QMI mode  + old firmware → upgrade firmware only
    # ─────────────────────────────────────────────────────────────────────────

    # Pause the internet-p100.py cron job for the duration of the fix — it
    # relaunches pppd every minute and will fight the firmware/composition
    # steps for the AT port (ttyUSB port used by modemPPP). Always resumed
    # in the finally block below, even on failure/exception.
    cron_was_paused = pause_internet_cron()

    try:
        fw_available = bool(state["fw_files"] or state["fw_archive_exists"])

        # Fix 1: Firmware upgrade
        # Required when: (a) MBIM mode (old firmware won't accept QMI bitmask)
        #                (b) old firmware regardless of USB mode
        if state["usb_mode"] == "MBIM" and fw_available:
            info("MBIM mode detected — upgrading firmware first (required for QMI switch)")
            if not fix_firmware(state, False):
                fail("Firmware upgrade failed — cannot switch to QMI safely")
                sys.exit(1)
            # Refresh cdc-wdm after flash
            wdm = glob.glob("/dev/cdc-wdm*")
            if wdm:
                state["cdc_wdm"] = wdm[0]
        elif state["firmware_old"] and fw_available:
            info("Old firmware detected (QMI mode) — upgrading firmware")
            if not fix_firmware(state, False):
                warn("Firmware upgrade failed — continuing with other fixes")
        elif state["firmware_old"] and not fw_available:
            warn("Old firmware but no firmware files/archive available — skipping upgrade")
        else:
            info("Firmware is up to date — skipping upgrade")

        # Fix 2: USB composition switch
        if state["usb_mode"] == "MBIM":
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
    finally:
        # Always restore the cron job, even if a fix step raised or called
        # sys.exit() above — leaving it paused would silently kill internet
        # recovery on this unit going forward.
        if cron_was_paused:
            resume_internet_cron()

    print(f"\n{BOLD}{'═'*60}{RESET}")
    if success:
        print(f"{GREEN}{BOLD}  ✓ FIX COMPLETE — Internet restored on "
              f"{os.uname().nodename}!{RESET}")
    else:
        print(f"{YELLOW}{BOLD}  ⚠ PARTIAL — Some issues may remain.{RESET}")
        diag = find_script("modem_diag.py")
        print(f"  Run: {CYAN}python3 {diag}{RESET}")
    print(f"{BOLD}{'═'*60}{RESET}\n")
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    if os.geteuid() != 0:
        print(f"{RED}This script must be run as root.{RESET}")
        sys.exit(1)
    main()
