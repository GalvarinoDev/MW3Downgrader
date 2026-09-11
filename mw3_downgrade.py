"""
mw3_downgrade.py — MW3 32-bit Depot Downgrader for Windows

Detects 64-bit MW3 installs broken by Activision's update, downloads
the 32-bit depot files via DepotDownloader (bundled), and merges them
over the game directory so Plutonium works again.

Supports Windows 10/11. No Python install required when built with
PyInstaller (--onefile --add-binary DepotDownloader.exe).

DepotDownloader by SteamRE — https://github.com/SteamRE/DepotDownloader
Licensed under the MIT License. Bundled with permission per license terms.
"""

import ctypes
import os
import re
import shutil
import subprocess
import sys
import winreg

# ── Version ──────────────────────────────────────────────────────────────────

VERSION = "1.0.1"

# ── ANSI colors (Windows 10+ Terminal) ───────────────────────────────────────

# Enable ANSI escape sequences on Windows 10+
def _enable_ansi():
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass

_enable_ansi()

class C:
    """ANSI color codes."""
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    DIM    = "\033[2m"


# ── Constants ────────────────────────────────────────────────────────────────

# Appids to search for (SP+MP base game, MP, and Dedicated Server)
IW5_APPIDS = {
    "42690": "Multiplayer",
    "42750": "Dedicated Server",
    "42680": "Call of Duty: Modern Warfare 3",
}

# Depot plans per ownership type.
# Each entry: (app_id_for_download, depot_id, manifest_id)
#
# Shared depots 42682/42683 exist under both 42690 and 42750 on Steam
# (confirmed via SteamDB). Using the detected app as the -app flag
# ensures DepotDownloader resolves the license correctly for users
# who only own one of the two.
#
# 42681 = SP binaries (in 42680; MW3 owners get both SP and MP)
# 42691 = MP binaries (only in 42690)
# 42751 = DS binaries (only in 42750)

_DEPOTS_MP = (
    {"app": 42690, "depot": 42682, "manifest": "2661317971072643596"},
    {"app": 42690, "depot": 42683, "manifest": "1595601894688570808"},  # language slot
    {"app": 42680, "depot": 42681, "manifest": "5651167211650965131"},
    {"app": 42690, "depot": 42691, "manifest": "4104640605720756125"},
)

_DEPOTS_DS = (
    {"app": 42750, "depot": 42682, "manifest": "2661317971072643596"},
    {"app": 42750, "depot": 42683, "manifest": "1595601894688570808"},  # language slot
    {"app": 42750, "depot": 42751, "manifest": "9089183337461621316"},
)

# Fallback when the install path is entered manually and we don't
# know which app the user owns. Uses 42690 (MP) as the broadest bet.
_DEPOTS_MANUAL = (
    {"app": 42690, "depot": 42682, "manifest": "2661317971072643596"},
    {"app": 42690, "depot": 42683, "manifest": "1595601894688570808"},  # language slot
)

# Language depots. The depot ID and manifest change per language;
# the app ID is set at runtime based on ownership (42690 or 42750).
# English (42683) is the default and is already in the base plans.
IW5_LANGUAGES = {
    "1": {"name": "English",  "depot": 42683, "manifest": "1595601894688570808"},
    "2": {"name": "Spanish",  "depot": 42684, "manifest": "2897345196099821819"},
    "3": {"name": "German",   "depot": 42685, "manifest": "1934757878507421371"},
    "4": {"name": "French",   "depot": 42686, "manifest": "7270424907870526698"},
    "5": {"name": "Italian",  "depot": 42687, "manifest": "7337464168521481857"},
    "6": {"name": "Russian",  "depot": 42688, "manifest": "5872940112953203418"},
    "7": {"name": "Japanese", "depot": 42689, "manifest": "2918816197530142361"},
    "8": {"name": "Polish",   "depot": 42692, "manifest": "7706916996646584447"},
}

_LANG_DEPOT_DEFAULT = 42683  # English depot ID, used to find the slot to swap


def ask_language() -> dict:
    """Prompt the user to select a language. Returns the language dict."""
    print()
    info("Select language for game files:")
    print()
    for key, lang in IW5_LANGUAGES.items():
        default = " (default)" if key == "1" else ""
        print(f"    {C.CYAN}{key}{C.RESET}  {lang['name']}{default}")
    print()

    while True:
        choice = input(f"  {C.YELLOW}  ?   Language [1]:{C.RESET} ").strip()
        if choice == "":
            return IW5_LANGUAGES["1"]
        if choice in IW5_LANGUAGES:
            return IW5_LANGUAGES[choice]


def get_depot_plan(appid: str, language: dict | None = None) -> list[dict]:
    """
    Return the depot download plan for the detected appid.
    If a language is provided, swaps the English language depot
    for the selected one.
    """
    if appid == "42750":
        plan = list(_DEPOTS_DS)
    elif appid in ("42690", "42680"):
        # 42680 (base game) includes 42690 (MP), so both get the
        # full SP+MP depot set.
        plan = list(_DEPOTS_MP)
    else:
        plan = list(_DEPOTS_MANUAL)

    # Swap language depot if not English
    if language and language["depot"] != _LANG_DEPOT_DEFAULT:
        for i, entry in enumerate(plan):
            if entry["depot"] == _LANG_DEPOT_DEFAULT:
                plan[i] = {
                    "app": entry["app"],
                    "depot": language["depot"],
                    "manifest": language["manifest"],
                }
                break

    return plan


# Detection: main/iw_00.iwd size threshold
#   32-bit: ~314 MB    64-bit: ~420 MB    threshold: 380 MB
_IW5_MARKER_FILE          = os.path.join("main", "iw_00.iwd")
_IW5_64BIT_SIZE_THRESHOLD = 380 * 1024 * 1024

# Minimum free space (GB)
REQUIRED_FREE_SPACE_GB = 15

# DepotDownloader username capture regex
_USERNAME_RE = re.compile(
    r"Success!.*-username\s+(\S+)\s+-remember-password"
)

# QR block characters
_QR_CHARS = frozenset("█▀▄▐▌░▒▓ ")


# ── Helpers ──────────────────────────────────────────────────────────────────

def banner():
    print(f"""
{C.CYAN}{C.BOLD}╔══════════════════════════════════════════════════════════╗
║         MW3 32-bit Downgrader for Plutonium  v{VERSION}     ║
║                                                          ║
║  DepotDownloader by SteamRE (MIT License)                ║
║  https://github.com/SteamRE/DepotDownloader              ║
╚══════════════════════════════════════════════════════════╝{C.RESET}
""")


def info(msg):
    print(f"  {C.CYAN}[INFO]{C.RESET}  {msg}")

def ok(msg):
    print(f"  {C.GREEN}[ OK ]{C.RESET}  {msg}")

def warn(msg):
    print(f"  {C.YELLOW}[WARN]{C.RESET}  {msg}")

def fail(msg):
    print(f"  {C.RED}[FAIL]{C.RESET}  {msg}")

def progress(msg):
    print(f"  {C.DIM}  ...  {msg}{C.RESET}")

def ask_yes_no(prompt, default=True):
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        resp = input(f"  {C.YELLOW}  ?   {prompt} {hint}:{C.RESET} ").strip().lower()
        if resp == "":
            return default
        if resp in ("y", "yes"):
            return True
        if resp in ("n", "no"):
            return False

def press_enter(msg="Press Enter to continue..."):
    input(f"\n  {C.DIM}{msg}{C.RESET}")


def progress_bar(percent: float, width: int = 40, label: str = ""):
    """
    Draw a progress bar on the current line, overwriting previous output.
    percent: 0.0 to 100.0
    """
    percent = max(0.0, min(100.0, percent))
    filled = int(width * percent / 100.0)
    empty = width - filled
    bar = f"{C.GREEN}{'█' * filled}{C.DIM}{'░' * empty}{C.RESET}"
    pct = f"{percent:5.1f}%"
    line = f"\r  {bar}  {pct}"
    if label:
        line += f"  {C.DIM}{label}{C.RESET}"
    # Pad with spaces to clear any leftover characters from longer lines
    print(f"{line:<100}", end="", flush=True)


def progress_bar_done(msg: str = ""):
    """Finish the progress bar line and print a completion message."""
    print()  # newline after the bar
    if msg:
        ok(msg)


# ── DepotDownloader path ─────────────────────────────────────────────────────

def get_depot_downloader_path() -> str:
    """
    Return the path to the bundled DepotDownloader.exe.

    When running as a PyInstaller --onefile bundle, files added via
    --add-binary are extracted to sys._MEIPASS at runtime.
    When running as a plain .py script, look next to the script.
    """
    if getattr(sys, "_MEIPASS", None):
        path = os.path.join(sys._MEIPASS, "DepotDownloader.exe")
    else:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "DepotDownloader.exe")
    if not os.path.isfile(path):
        fail("DepotDownloader.exe not found.")
        fail(f"Expected at: {path}")
        fail("If running from source, place DepotDownloader.exe next to this script.")
        return None
    return path


# ── Windows Steam detection ──────────────────────────────────────────────────

def find_steam_root() -> str | None:
    """
    Find the Steam install directory on Windows.
    Checks the registry first, then falls back to the default path.
    """
    # Try registry
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Valve\Steam",
        )
        steam_path, _ = winreg.QueryValueEx(key, "SteamPath")
        winreg.CloseKey(key)
        steam_path = os.path.normpath(steam_path)
        if os.path.isdir(steam_path):
            return steam_path
    except (OSError, FileNotFoundError):
        pass

    # Fallback to default install path
    default = os.path.join(
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        "Steam",
    )
    if os.path.isdir(default):
        return default

    return None


def find_library_dirs(steam_root: str) -> list[str]:
    """
    Parse libraryfolders.vdf and return all steamapps directories.
    """
    dirs = []
    seen = set()

    def add(path):
        path = os.path.normpath(path)
        if path not in seen and os.path.isdir(path):
            seen.add(path)
            dirs.append(path)

    # Main steamapps
    add(os.path.join(steam_root, "steamapps"))

    # Additional libraries from VDF
    vdf_path = os.path.join(steam_root, "steamapps", "libraryfolders.vdf")
    if os.path.isfile(vdf_path):
        try:
            with open(vdf_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            for path in re.findall(r'"path"\s+"([^"]+)"', content):
                # VDF may use forward slashes or escaped backslashes
                path = path.replace("\\\\", "\\")
                add(os.path.join(path, "steamapps"))
        except Exception:
            pass

    return dirs


def find_mw3_installs(steam_root: str) -> list[dict]:
    """
    Locate all MW3 install directories across all Steam libraries.
    Checks for MW3 base game (42680), Multiplayer (42690), and
    Dedicated Server (42750).
    Returns a list of dicts with 'appid', 'label', and 'install_dir'.

    Primary method: appmanifest ACF files in Steam library folders.
    Fallback: Windows registry keys written by Steam per app.
    """
    library_dirs = find_library_dirs(steam_root)
    found = []
    seen_dirs = set()

    # Primary: scan appmanifest ACF files
    if library_dirs:
        for appid, label in IW5_APPIDS.items():
            for steamapps_dir in library_dirs:
                acf = os.path.join(steamapps_dir, f"appmanifest_{appid}.acf")
                if not os.path.isfile(acf):
                    continue

                install_name = None
                state_flags = None
                try:
                    with open(acf, "r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            m = re.search(r'"installdir"\s+"([^"]+)"', line)
                            if m:
                                install_name = m.group(1)
                            m = re.search(r'"StateFlags"\s+"(\d+)"', line)
                            if m:
                                state_flags = m.group(1)
                            if install_name and state_flags:
                                break
                except Exception:
                    continue

                if not install_name or state_flags != "4":
                    continue

                install_dir = os.path.join(steamapps_dir, "common", install_name)
                norm = os.path.normpath(install_dir).lower()
                if os.path.isdir(install_dir) and norm not in seen_dirs:
                    seen_dirs.add(norm)
                    found.append({
                        "appid": appid,
                        "label": label,
                        "install_dir": install_dir,
                    })
                    break  # found this appid, move to next

    # Fallback: check Windows registry for install locations.
    # Steam writes an Uninstall key per app with InstallLocation.
    if not found:
        found = _find_mw3_installs_registry(seen_dirs)

    return found


def _find_mw3_installs_registry(seen_dirs: set) -> list[dict]:
    """
    Fallback detection via Windows registry.
    Steam writes keys at:
      HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Steam App XXXXX
    with an InstallLocation value pointing to the game directory.
    """
    found = []

    for appid, label in IW5_APPIDS.items():
        key_path = (
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
            rf"\Steam App {appid}"
        )
        try:
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                key_path,
                0,
                winreg.KEY_READ | winreg.KEY_WOW64_32KEY,
            )
            install_dir, _ = winreg.QueryValueEx(key, "InstallLocation")
            winreg.CloseKey(key)

            if not install_dir:
                continue

            install_dir = os.path.normpath(install_dir)
            norm = install_dir.lower()
            if os.path.isdir(install_dir) and norm not in seen_dirs:
                seen_dirs.add(norm)
                found.append({
                    "appid": appid,
                    "label": f"{label} (registry)",
                    "install_dir": install_dir,
                })
        except (OSError, FileNotFoundError):
            continue

    return found


# ── Detection ────────────────────────────────────────────────────────────────

def is_iw5_64bit(install_dir: str) -> bool:
    """
    Check whether MW3 is the 64-bit version by the size of
    main/iw_00.iwd. Returns True if above the 380 MB threshold.
    """
    marker = os.path.join(install_dir, _IW5_MARKER_FILE)
    if not os.path.isfile(marker):
        return False
    try:
        size = os.path.getsize(marker)
        return size > _IW5_64BIT_SIZE_THRESHOLD
    except OSError:
        return False


# ── Disk space ───────────────────────────────────────────────────────────────

def check_free_space_gb(path: str) -> float:
    """Return free space in GB at the given path."""
    try:
        usage = shutil.disk_usage(path)
        return usage.free / (1024 ** 3)
    except Exception:
        return 0.0


def has_enough_space(path: str) -> bool:
    return check_free_space_gb(path) >= REQUIRED_FREE_SPACE_GB



# ── QR detection ─────────────────────────────────────────────────────────────

def _is_qr_line(line: str) -> bool:
    """Check if a line is part of a QR code (mostly block characters)."""
    stripped = line.strip()
    if not stripped:
        return False
    block_count = sum(1 for c in stripped if c in _QR_CHARS)
    return block_count > len(stripped) * 0.5


# ── DepotDownloader QR download ──────────────────────────────────────────────

def run_depot_download(
    dd_path: str,
    staging_dir: str,
    depot_info: dict,
    username: str | None = None,
) -> str | None:
    """
    Run DepotDownloader for a single depot with QR authentication.
    Returns the captured username on success, None on failure.
    Retries automatically when QR expires, but surfaces real errors
    (access denied, 401, missing manifest) instead of misdiagnosing
    them as QR expiry.
    """
    os.makedirs(staging_dir, exist_ok=True)
    attempt = 0
    max_qr_retries = 5

    while True:
        attempt += 1
        cmd = [
            dd_path,
            "-app", str(depot_info["app"]),
            "-depot", str(depot_info["depot"]),
            "-manifest", depot_info["manifest"],
            "-dir", staging_dir,
            "-remember-password",
        ]

        if username:
            cmd.extend(["-username", username])
        else:
            cmd.append("-qr")

        if attempt > 1:
            info(f"Retrying QR login (attempt {attempt})...")
        else:
            info(f"Downloading depot {depot_info['depot']} (app {depot_info['app']})...")

        captured_username = None
        auth_succeeded = False
        error_lines = []
        got_qr_refresh = False

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=os.path.dirname(dd_path),
                encoding="utf-8",
                errors="replace",
            )

            qr_lines = []
            reading_qr = False

            for line in proc.stdout:
                line = line.rstrip("\n\r")

                # QR code start / refresh
                if "QR code has changed" in line or (
                    "Use the Steam Mobile App" in line
                ):
                    reading_qr = True
                    got_qr_refresh = True
                    qr_lines = []
                    # Clear previous QR from console
                    print()
                    info("Scan this QR code with the Steam Mobile App:")
                    print()
                    continue

                # QR code content
                if reading_qr:
                    if _is_qr_line(line):
                        qr_lines.append(line)
                        print(f"    {line}")
                        continue
                    elif qr_lines:
                        print()
                        info("Waiting for authentication...")
                        qr_lines = []
                        reading_qr = False

                # Auth success
                m = _USERNAME_RE.search(line)
                if m:
                    captured_username = m.group(1)
                    auth_succeeded = True
                    ok(f"Authenticated as: {captured_username}")
                    continue

                # Progress
                if "Got depot key" in line:
                    progress(f"Downloading depot {depot_info['depot']}...")
                elif "%" in line:
                    pct_match = re.search(r"([\d.]+)\s*%", line)
                    if pct_match:
                        pct = float(pct_match.group(1))
                        progress_bar(pct, label=f"Depot {depot_info['depot']}")
                elif "Total downloaded" in line or "already" in line.lower():
                    progress_bar_done(line.strip())
                else:
                    # Capture lines that might be error messages from
                    # DepotDownloader (access denied, 401, license
                    # failures, etc.) so we can surface them instead
                    # of silently retrying.
                    stripped = line.strip()
                    if stripped and not _is_qr_line(line):
                        error_lines.append(stripped)

            proc.wait()

            if proc.returncode == 0 and (auth_succeeded or username):
                progress_bar(100.0, label=f"Depot {depot_info['depot']}")
                progress_bar_done(f"Depot {depot_info['depot']} download complete.")
                return captured_username or username

            # Remembered credentials failed
            if username:
                fail(f"DepotDownloader exited with code {proc.returncode}")
                if error_lines:
                    fail("DepotDownloader output:")
                    for el in error_lines[-10:]:
                        print(f"         {C.DIM}{el}{C.RESET}")
                return None

            # Check if there was a real error after auth succeeded
            # (e.g. 401, access denied, missing manifest).
            # If so, don't retry QR; the problem isn't authentication.
            _error_keywords = (
                "401", "access denied", "aborting",
                "result: 0", "no manifest request code",
                "unable to download", "not completely downloaded",
                "not available", "could not get depot key",
            )
            real_error = False
            for el in error_lines:
                if any(kw in el.lower() for kw in _error_keywords):
                    real_error = True
                    break

            if real_error or (auth_succeeded and proc.returncode != 0):
                fail(f"DepotDownloader failed (exit code {proc.returncode}).")
                if error_lines:
                    fail("DepotDownloader output:")
                    for el in error_lines[-10:]:
                        print(f"         {C.DIM}{el}{C.RESET}")
                if auth_succeeded:
                    fail("Authentication succeeded but the download was denied.")
                    fail("This may mean your Steam account does not own the")
                    fail("required depot files. Check that you own MW3 Multiplayer")
                    fail("(appid 42690) or Dedicated Server (appid 42750) on Steam.")
                return None

            # Genuine QR expiry: no auth, no real errors, QR was shown
            if attempt >= max_qr_retries:
                fail(f"QR login failed after {max_qr_retries} attempts.")
                return None

            warn("QR code expired. Generating a new one...")

        except FileNotFoundError:
            fail("DepotDownloader.exe not found or failed to execute.")
            return None
        except Exception as ex:
            fail(f"DepotDownloader error: {ex}")
            return None


# ── Merge ────────────────────────────────────────────────────────────────────

def merge_depots(staging_dir: str, install_dir: str):
    """
    Merge downloaded 32-bit depot files over the MW3 install.

    Uses delete-then-move strategy:
      1. Delete old 64-bit files that will be replaced (frees space).
      2. Move staged files into place (instant rename on same drive).
    """
    info("Merging 32-bit files into MW3 install...")
    _merge_tree(staging_dir, install_dir)
    ok("Merge complete.")

    # Remove any DepotDownloader artifacts from the game directory
    _cleanup_dd_artifacts(install_dir)

    # Cleanup staging
    info("Cleaning up staging files...")
    try:
        shutil.rmtree(staging_dir, ignore_errors=True)
        ok("Staging files removed.")
    except Exception as ex:
        warn(f"Could not remove staging dir: {ex}")

    return True


def _cleanup_dd_artifacts(install_dir: str):
    """Remove DepotDownloader artifacts from the game directory."""
    # .DepotDownloader config directory
    dd_dir = os.path.join(install_dir, ".DepotDownloader")
    if os.path.isdir(dd_dir):
        try:
            shutil.rmtree(dd_dir, ignore_errors=True)
        except Exception:
            pass

    # .manifest files in the game root
    try:
        for fname in os.listdir(install_dir):
            if fname.endswith(".manifest"):
                os.remove(os.path.join(install_dir, fname))
    except Exception:
        pass


def _is_dd_artifact(rel_path: str) -> bool:
    """Check if a relative path is a DepotDownloader artifact, not a game file."""
    parts = rel_path.replace("\\", "/").split("/")
    # .DepotDownloader/ config directory
    if parts[0] == ".DepotDownloader":
        return True
    # Manifest files left in the download root
    if rel_path.endswith(".manifest"):
        return True
    return False


def _merge_tree(src: str, dst: str):
    """
    Delete-then-move all files from src into dst.
    Skips DepotDownloader artifacts (config dir, manifest files).
    """
    rel_files = []
    for dirpath, _, filenames in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        for fname in filenames:
            rf = fname if rel == "." else os.path.join(rel, fname)
            if not _is_dd_artifact(rf):
                rel_files.append(rf)

    total = len(rel_files)
    if total == 0:
        warn("No files to merge.")
        return

    # Phase 1: delete old versions
    deleted = 0
    for i, rf in enumerate(rel_files, 1):
        dst_file = os.path.join(dst, rf)
        if os.path.isfile(dst_file):
            try:
                os.remove(dst_file)
                deleted += 1
            except OSError:
                pass
        if i % 10 == 0 or i == total:
            progress_bar(i / total * 100.0, label="Removing old files")
    progress_bar_done(f"Removed {deleted} old files.")

    # Phase 2: move staged files into place
    for i, rf in enumerate(rel_files, 1):
        src_file = os.path.join(src, rf)
        dst_file = os.path.join(dst, rf)
        os.makedirs(os.path.dirname(dst_file), exist_ok=True)
        shutil.move(src_file, dst_file)
        if i % 10 == 0 or i == total:
            progress_bar(i / total * 100.0, label="Moving files")
    progress_bar_done(f"Moved {total} files into place.")


# ── Main flow ────────────────────────────────────────────────────────────────

def main():
    # Set UTF-8 console codepage for QR display
    os.system("chcp 65001 >nul 2>&1")

    banner()

    # Step 1: Find Steam
    info("Searching for Steam installation...")
    steam_root = find_steam_root()
    if not steam_root:
        fail("Steam installation not found.")
        fail("Checked the Windows registry and default install path.")
        press_enter("Press Enter to exit...")
        return 1

    ok(f"Steam found: {steam_root}")

    # Step 2: Find MW3
    info("Searching for MW3 (Call of Duty: Modern Warfare 3)...")
    installs = find_mw3_installs(steam_root)

    if not installs:
        fail("MW3 not found in any Steam library.")
        fail("Make sure MW3 Multiplayer (42690) or Dedicated Server (42750) is installed.")
        print()
        # Offer manual path entry
        if ask_yes_no("Enter the MW3 install path manually?", default=False):
            manual = input("  Path: ").strip().strip('"')
            if os.path.isdir(manual):
                installs = [{"appid": "manual", "label": "Manual", "install_dir": manual}]
            else:
                fail(f"Directory not found: {manual}")
                press_enter("Press Enter to exit...")
                return 1
        else:
            press_enter("Press Enter to exit...")
            return 1

    if len(installs) == 1:
        install_dir = installs[0]["install_dir"]
        appid = installs[0]["appid"]
        ok(f"MW3 {installs[0]['label']} found: {install_dir}")
    else:
        # Multiple installs found, let user pick
        print()
        info("Multiple MW3 installs found:")
        print()
        for i, inst in enumerate(installs, 1):
            print(f"    {C.CYAN}{i}{C.RESET}  {inst['label']} (appid {inst['appid']})")
            print(f"       {C.DIM}{inst['install_dir']}{C.RESET}")
            print()

        # Check if all need downgrading
        need_downgrade = [inst for inst in installs if is_iw5_64bit(inst["install_dir"])]
        all_option = None
        if len(need_downgrade) > 1:
            all_option = str(len(installs) + 1)
            print(f"    {C.CYAN}{all_option}{C.RESET}  Downgrade all")
            print()

        valid = [str(i) for i in range(1, len(installs) + 1)]
        if all_option:
            valid.append(all_option)

        while True:
            pick = input(f"  {C.YELLOW}  ?   Choose install ({'/'.join(valid)}):{C.RESET} ").strip()
            if pick in valid:
                break

        if pick == all_option:
            # Downgrade all installs that need it
            for inst in need_downgrade:
                print()
                info(f"Processing {inst['label']} ({inst['appid']})...")
                ok(f"Install dir: {inst['install_dir']}")
                result = downgrade_install(inst["install_dir"], inst["appid"], steam_root)
                if not result:
                    fail(f"Failed to downgrade {inst['label']}.")
            press_enter("Press Enter to exit...")
            return 0
        else:
            idx = int(pick) - 1
            install_dir = installs[idx]["install_dir"]
            appid = installs[idx]["appid"]
            ok(f"MW3 {installs[idx]['label']} selected: {install_dir}")

    # Run the downgrade
    success = downgrade_install(install_dir, appid, steam_root)
    press_enter("Press Enter to exit...")
    return 0 if success else 1


def downgrade_install(install_dir: str, appid: str, steam_root: str) -> bool:
    """
    Run the full downgrade flow for a single MW3 install directory.
    Returns True on success, False on failure.
    """
    # Check if downgrade is needed
    info("Checking MW3 version...")
    if not is_iw5_64bit(install_dir):
        print()
        ok("MW3 is already 32-bit. No downgrade needed.")
        ok("Plutonium should work with this install.")
        return True

    warn("MW3 is 64-bit. Downgrade required for Plutonium.")

    # Check disk space
    free_gb = check_free_space_gb(install_dir)
    info(f"Free disk space: {free_gb:.1f} GB (need {REQUIRED_FREE_SPACE_GB} GB)")
    if not has_enough_space(install_dir):
        fail(f"Not enough free disk space. Need at least {REQUIRED_FREE_SPACE_GB} GB.")
        fail("Free up space on the drive where MW3 is installed and try again.")
        return False

    ok("Sufficient disk space.")

    # Locate bundled DepotDownloader
    dd_path = get_depot_downloader_path()
    if not dd_path:
        return False

    ok(f"DepotDownloader ready: {os.path.basename(dd_path)}")

    # Select language
    language = ask_language()
    if language["name"] != "English":
        ok(f"Language: {language['name']} (depot {language['depot']})")
    else:
        ok("Language: English")

    # Build depot plan based on detected ownership and language
    depot_plan = get_depot_plan(appid, language)
    if appid == "42750":
        info("Detected: Dedicated Server install (appid 42750)")
    elif appid == "42690":
        info("Detected: Multiplayer install (appid 42690)")
    elif appid == "42680":
        info("Detected: MW3 base game install (appid 42680)")
    else:
        info("Manual path: using default depot set")
    info(f"Depots to download: {len(depot_plan)}")

    # Staging directory next to the game install
    staging_dir = os.path.join(
        os.path.dirname(install_dir),
        "_mw3_downgrade_staging",
    )

    print()
    info("You need to authenticate with your Steam account.")
    info("A QR code will appear. Scan it with the Steam Mobile App.")
    info("(Your login session is only used to download MW3 depot files.)")
    press_enter()

    # Download depots
    username = None
    for i, depot_info in enumerate(depot_plan):
        print()
        info(f"Depot {i + 1} of {len(depot_plan)}: {depot_info['depot']}")
        result = run_depot_download(
            dd_path, staging_dir, depot_info, username=username,
        )
        if result is None:
            fail(f"Failed to download depot {depot_info['depot']}.")
            # Cleanup partial staging
            if os.path.isdir(staging_dir):
                shutil.rmtree(staging_dir, ignore_errors=True)
            return False
        # Remember username for subsequent depots (skip QR)
        username = result

    # Merge
    print()
    success = merge_depots(staging_dir, install_dir)

    if success:
        # Verify
        if not is_iw5_64bit(install_dir):
            print()
            print(f"  {C.GREEN}{C.BOLD}{'=' * 54}{C.RESET}")
            print(f"  {C.GREEN}{C.BOLD}  MW3 downgraded to 32-bit successfully!{C.RESET}")
            print(f"  {C.GREEN}{C.BOLD}  Plutonium should now work with this install.{C.RESET}")
            print(f"  {C.GREEN}{C.BOLD}{'=' * 54}{C.RESET}")
        else:
            warn("Merge completed but MW3 still appears to be 64-bit.")
            warn("The marker file (main/iw_00.iwd) size has not changed.")
            warn("Try verifying MW3 files in Steam, then run this tool again.")
            return False
    else:
        fail("Merge failed. See errors above.")

    return success


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(f"\n\n  {C.YELLOW}Cancelled by user.{C.RESET}")
        sys.exit(1)
    except Exception as ex:
        print(f"\n  {C.RED}Unexpected error: {ex}{C.RESET}")
        import traceback
        traceback.print_exc()
        input("\n  Press Enter to exit...")
        sys.exit(1)
