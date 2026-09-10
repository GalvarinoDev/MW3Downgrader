# MW3 Downgrader

Activision pushed a 64-bit update for Call of Duty: Modern Warfare 3 that breaks Plutonium compatibility. This tool detects the 64-bit install and replaces it with the correct 32-bit depot files so Plutonium works again.

Supports both the Multiplayer client (appid 42690) and the Dedicated Server (appid 42750). If both are installed, the tool lets you choose which to downgrade or do both at once.

Windows 10 and 11 only. No Python or other software required.

## How to use

1. Download `MW3_Downgrader.exe` from the [Releases](https://github.com/GalvarinoDev/MW3Downgrader/releases) page.
2. Run the exe. Windows SmartScreen may show a warning because the file is unsigned. Click **More info**, then **Run anyway**.
3. The tool finds your Steam and MW3 install automatically (Multiplayer, Dedicated Server, or both).
4. A QR code appears in the console. Scan it with the Steam Mobile App to authenticate.
5. The tool downloads the 32-bit depot files, merges them over your MW3 install, and verifies the result.

## Requirements

- MW3 Multiplayer (Steam appid 42690) and/or Dedicated Server (Steam appid 42750) installed
- Steam Mobile App on your phone (for the automated QR method)
- 15 GB of free disk space on the drive where MW3 is installed

## How it works

The tool checks the size of `main/iw_00.iwd` in your MW3 directory. The 64-bit version is about 420 MB. The 32-bit version is about 314 MB. If the file is above 380 MB, the install needs downgrading.

It then detects whether you have MW3 Multiplayer (appid 42690) or the Dedicated Server (appid 42750) installed, and downloads the correct depot set for your install type:

- **Multiplayer**: depots 42682 (base), 42683 (English), 42691 (MP binaries)
- **Dedicated Server**: depots 42682 (base), 42683 (English), 42751 (DS binaries)

The depots are downloaded at their last known 32-bit manifest versions. These are the same files Steam would give you if you could roll back the update yourself.

After download, the tool deletes the old 64-bit files and moves the 32-bit replacements into place.

## FAQ

**Is this safe?**
The tool only modifies files inside your MW3 install directory. It does not touch Steam, other games, or system files. If something goes wrong, verify MW3 file integrity through Steam to restore the original files.

**Do I need to keep Steam open?**
Steam does not need to be running. The tool uses its own downloader.

**The QR code expired before I could scan it.**
The tool generates a new QR code automatically. Take your time.

**Windows Defender flagged the exe.**
This is a false positive caused by unsigned PyInstaller executables. The source code is available in this repository for review.

**The tool says MW3 is not found.**
Make sure MW3 Multiplayer (42690) or Dedicated Server (42750) is fully installed through Steam. If it is installed but not detected, the tool will ask if you want to enter the path manually.

**Can I use this on Steam Deck or Linux?**
This tool is for Windows only. Steam Deck and Linux users can use [DeckOps](https://github.com/GalvarinoDev/DeckOps-Nightly), which has a built-in MW3 downgrader.

## Credits

- [DepotDownloader](https://github.com/SteamRE/DepotDownloader) by SteamRE (MIT License). Bundled in the release exe for automated depot downloads.
- Detection and merge logic adapted from [DeckOps-Nightly](https://github.com/GalvarinoDev/DeckOps-Nightly).

## License

MIT
