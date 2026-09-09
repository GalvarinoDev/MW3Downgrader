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
import textwrap
import time
import winreg

# ── Version ──────────────────────────────────────────────────────────────────

VERSION = "1.0.0"

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

IW5_APP_ID = 42680  # MW3 base app

# Appids to search for (MP and Dedicated Server)
IW5_APPIDS = {
    "42690": "Multiplayer",
    "42750": "Dedicated Server",
}

IW5_DEPOTS = (
    {"depot": 42682, "manifest": "2661317971072643596"},
    {"depot": 42683, "manifest": "1595601894688570808"},
)

IW5_DEPOT_IDS    = (42682, 42683)
IW5_DEPOT_CMDS   = (
    "download_depot 42680 42682 2661317971072643596",
    "download_depot 42680 42683 1595601894688570808",
)

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
    Checks for both Multiplayer (42690) and Dedicated Server (42750).
    Returns a list of dicts with 'appid', 'label', and 'install_dir'.
    """
    library_dirs = find_library_dirs(steam_root)
    if not library_dirs:
        return []

    found = []
    seen_dirs = set()

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


# ── Clipboard ────────────────────────────────────────────────────────────────

def copy_to_clipboard(text: str):
    """Copy text to the Windows clipboard via clip.exe."""
    try:
        proc = subprocess.run(
            "clip",
            input=text.encode("utf-8"),
            shell=True,
            timeout=5,
        )
        if proc.returncode == 0:
            return True
    except Exception:
        pass
    return False


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
    Retries automatically when QR expires.
    """
    os.makedirs(staging_dir, exist_ok=True)
    attempt = 0

    while True:
        attempt += 1
        cmd = [
            dd_path,
            "-app", str(IW5_APP_ID),
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
            info(f"Downloading depot {depot_info['depot']}...")

        captured_username = None
        auth_succeeded = False

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
                    qr_lines = []
                    # Clear previous QR from console
                    print()
                    info("Scan this QR code with the Steam Mobile App:")
                    print()
                    continue

                # QR code content — print directly to console
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
                elif "%" in line and ("download" in line.lower() or
                                      "/" in line):
                    # Overwrite the current line for progress bar effect
                    print(f"\r  {C.DIM}  ...  {line.strip()}{C.RESET}",
                          end="", flush=True)
                elif "Total downloaded" in line or "already" in line.lower():
                    print()  # newline after progress
                    progress(line.strip())

            proc.wait()

            if proc.returncode == 0 and (auth_succeeded or username):
                print()  # ensure newline
                ok(f"Depot {depot_info['depot']} download complete.")
                return captured_username or username

            # Remembered credentials failed — don't retry
            if username:
                fail(f"DepotDownloader exited with code {proc.returncode}")
                return None

            # QR expired — loop to get a fresh one
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

    Handles both QR path (files directly in staging_dir) and manual
    path (depot_XXXXX subdirectories).
    """
    depot_42682_dir = os.path.join(staging_dir, "depot_42682")
    has_subdirs = os.path.isdir(depot_42682_dir)

    if has_subdirs:
        # Manual Steam console path: subdirs per depot
        for depot_id in IW5_DEPOT_IDS:
            depot_path = os.path.join(staging_dir, f"depot_{depot_id}")
            if not os.path.isdir(depot_path):
                fail(f"Depot directory not found: {depot_path}")
                return False
            info(f"Merging depot {depot_id}...")
            _merge_tree(depot_path, install_dir)
            ok(f"Depot {depot_id} merged.")
    else:
        # QR path: files directly in staging_dir
        info("Merging 32-bit files into MW3 install...")
        _merge_tree(staging_dir, install_dir)
        ok("Merge complete.")

    # Cleanup staging
    info("Cleaning up staging files...")
    try:
        shutil.rmtree(staging_dir, ignore_errors=True)
        ok("Staging files removed.")
    except Exception as ex:
        warn(f"Could not remove staging dir: {ex}")

    return True


def _merge_tree(src: str, dst: str):
    """
    Delete-then-move all files from src into dst.
    Reports progress every 25 files.
    """
    rel_files = []
    for dirpath, _, filenames in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        for fname in filenames:
            rel_files.append(
                fname if rel == "." else os.path.join(rel, fname)
            )

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
        if i % 25 == 0 or i == total:
            print(f"\r  {C.DIM}  ...  Removing old files... {i}/{total}{C.RESET}",
                  end="", flush=True)
    print()

    # Phase 2: move staged files into place
    for i, rf in enumerate(rel_files, 1):
        src_file = os.path.join(src, rf)
        dst_file = os.path.join(dst, rf)
        os.makedirs(os.path.dirname(dst_file), exist_ok=True)
        shutil.move(src_file, dst_file)
        if i % 25 == 0 or i == total:
            print(f"\r  {C.DIM}  ...  Moving files... {i}/{total}{C.RESET}",
                  end="", flush=True)
    print()


# ── Manual Steam console path ───────────────────────────────────────────────

def run_manual_path(steam_root: str, install_dir: str):
    """
    Walk the user through the manual Steam console download_depot path.
    No DepotDownloader needed — uses Steam's built-in console.
    """
    print()
    info("Manual download via Steam console.")
    print()
    print(f"  {C.BOLD}You need to run these two commands in the Steam console.{C.RESET}")
    print(f"  Steam will download the 32-bit depot files to its internal staging area.")
    print()

    for i, cmd in enumerate(IW5_DEPOT_CMDS, 1):
        print(f"  {C.CYAN}Command {i}:{C.RESET}  {C.BOLD}{cmd}{C.RESET}")
    print()

    # Offer to open Steam console
    if ask_yes_no("Open Steam console now?"):
        try:
            os.startfile("steam://open/console")
            ok("Steam console opened. Switch to Steam and paste the commands.")
        except Exception:
            warn("Could not open Steam console. Open Steam, click Steam > Settings,")
            warn("then type steam://open/console in the address bar.")

    # Offer to copy commands to clipboard
    if ask_yes_no("Copy the first command to clipboard?"):
        if copy_to_clipboard(IW5_DEPOT_CMDS[0]):
            ok("Copied. Paste it in the Steam console with Ctrl+V.")
        else:
            warn("Could not copy to clipboard. Copy it manually from above.")

    print()
    info("After pasting the first command, wait for it to finish.")
    info("Then paste the second command.")
    print()

    if ask_yes_no("Copy the second command to clipboard?"):
        if copy_to_clipboard(IW5_DEPOT_CMDS[1]):
            ok("Copied.")

    print()
    info("When both downloads finish, press Enter here to continue.")
    info(f"Steam stores the files under: {C.DIM}{steam_root}{C.RESET}")
    press_enter("Press Enter when both depot downloads are complete...")

    # Find staging dir
    staging = find_manual_staging(steam_root)
    if not staging:
        fail("Could not find depot staging directory.")
        fail("Expected location: <Steam>/steamapps/content/app_42680/")
        fail("Check that both download_depot commands completed successfully.")
        return False

    ok(f"Found staging directory: {staging}")
    return merge_depots(staging, install_dir)


def find_manual_staging(steam_root: str) -> str | None:
    """
    Locate the depot staging directory after download_depot.
    On Windows, Steam places files under steamapps/content/.
    """
    candidates = [
        os.path.join(steam_root, "steamapps", "content",
                     f"app_{IW5_APP_ID}"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


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
                result = downgrade_install(inst["install_dir"], steam_root)
                if not result:
                    fail(f"Failed to downgrade {inst['label']}.")
            press_enter("Press Enter to exit...")
            return 0
        else:
            idx = int(pick) - 1
            install_dir = installs[idx]["install_dir"]
            ok(f"MW3 {installs[idx]['label']} selected: {install_dir}")

    # Run the downgrade
    success = downgrade_install(install_dir, steam_root)
    press_enter("Press Enter to exit...")
    return 0 if success else 1


def downgrade_install(install_dir: str, steam_root: str) -> bool:
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

    # Choose download method
    print()
    print(f"  {C.BOLD}Choose a download method:{C.RESET}")
    print()
    print(f"    {C.CYAN}1{C.RESET}  Automated (QR code login via DepotDownloader)")
    print(f"       Scan a QR code with the Steam Mobile App. Fastest option.")
    print()
    print(f"    {C.CYAN}2{C.RESET}  Manual (Steam console commands)")
    print(f"       Paste two commands in the Steam console. No extra tools.")
    print()

    while True:
        choice = input(f"  {C.YELLOW}  ?   Enter 1 or 2:{C.RESET} ").strip()
        if choice in ("1", "2"):
            break

    if choice == "2":
        success = run_manual_path(steam_root, install_dir)
        if success:
            print()
            ok(f"{C.GREEN}{C.BOLD}MW3 downgraded to 32-bit successfully!{C.RESET}")
            ok("You can now use Plutonium.")
        else:
            fail("Downgrade did not complete. See errors above.")
        return success

    # Automated path via DepotDownloader
    dd_path = get_depot_downloader_path()
    if not dd_path:
        warn("Falling back to manual method.")
        success = run_manual_path(steam_root, install_dir)
        if success:
            print()
            ok(f"{C.GREEN}{C.BOLD}MW3 downgraded to 32-bit successfully!{C.RESET}")
            ok("You can now use Plutonium.")
        return success

    ok(f"DepotDownloader ready: {os.path.basename(dd_path)}")

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

    # Download both depots
    username = None
    for i, depot_info in enumerate(IW5_DEPOTS):
        print()
        info(f"Depot {i + 1} of {len(IW5_DEPOTS)}: {depot_info['depot']}")
        result = run_depot_download(
            dd_path, staging_dir, depot_info, username=username,
        )
        if result is None:
            fail(f"Failed to download depot {depot_info['depot']}.")
            fail("You can try the manual method instead (option 2).")
            # Cleanup partial staging
            if os.path.isdir(staging_dir):
                shutil.rmtree(staging_dir, ignore_errors=True)
            return False
        # Remember username for second depot (skip QR)
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
