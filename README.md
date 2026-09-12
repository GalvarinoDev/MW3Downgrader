# MW3 Downgrader

Activision pushed a 64-bit update for Call of Duty: Modern Warfare 3 that breaks Plutonium compatibility. This tool detects the 64-bit install and replaces it with the correct 32-bit depot files so Plutonium works again.

Also detects missing or incorrect DLC Collections 1 through 4 and downloads them automatically for users who own them on Steam.

Supports the MW3 base game (appid 42680), Multiplayer client (appid 42690), and the Dedicated Server (appid 42750). If multiple are installed, the tool lets you choose which to downgrade or do all at once.

Includes language selection: English, Spanish, German, French, Italian, Russian, Japanese, and Polish.

Windows 10 and 11 only. No Python or other software required.

## How to use

1. Download `MW3_Downgrader.exe` from the [Releases](https://github.com/GalvarinoDev/MW3Downgrader/releases) page.
2. Run the exe. Windows SmartScreen may show a warning because the file is unsigned. Click **More info**, then **Run anyway**.
3. The tool finds your Steam and MW3 install automatically (base game, Multiplayer, Dedicated Server, or multiple).
4. The tool checks whether the base game needs downgrading (64-bit to 32-bit).
5. The tool scans for DLC Collections 1 through 4 by checking map file sizes on disk. Any collection that is missing or has incorrect files is flagged for download.
6. If a base-game downgrade is needed, select your language (English is the default) and optionally include singleplayer files.
7. A QR code appears in the console. Scan it with the Steam Mobile App to authenticate.
8. The tool downloads all needed depot files (base game and/or DLC), merges them over your MW3 install, and verifies the result.

## DLC support

The tool automatically detects DLC by checking specific map files in your install:

- **Collection 1** (Liberation, Piazza, Overwatch, Black Box): checks `zone/dlc/mp_overwatch.ff`
- **Collection 2** (Sanctuary, Foundation, Oasis + Face Off maps): checks `zone/dlc/mp_cement.ff`
- **Collection 3 - Chaos Pack** (Intersection, U-Turn, Vortex + Spec Ops): checks `zone/dlc/mp_crosswalk_ss.ff`
- **Collection 4 - Final Assault** (Boardwalk, Gulch, Parish, Off Shore, Decommission): checks `zone/dlc/mp_shipbreaker.ff`

If a marker file is missing or the wrong size, that collection is flagged for download. Each collection requires separate Steam ownership. If your account does not own a DLC, Steam will deny the download and the tool will tell you which collection failed.

You can run the tool on an already-downgraded (32-bit) install purely to fix DLC. The base-game downgrade step is skipped automatically when MW3 is already 32-bit.

## Requirements

- MW3 (Steam appid 42680), MW3 Multiplayer (appid 42690), and/or Dedicated Server (appid 42750) installed
- Steam Mobile App on your phone (for the automated QR method)
- Steam ownership of each DLC collection you want to download
- 15 GB of free disk space on the drive where MW3 is installed

## How it works

The tool checks the size of `main/iw_00.iwd` in your MW3 directory. The 64-bit version is about 420 MB. The 32-bit version is about 314 MB. If the file is above 380 MB, the install needs downgrading.

It then detects whether you have MW3 (appid 42680), Multiplayer (appid 42690), or the Dedicated Server (appid 42750) installed, and downloads the correct depot set for your install type:

- **MW3 / Multiplayer**: depots 42682 (base), language depot, 42681 (SP binaries), 42691 (MP binaries)
- **Dedicated Server**: depots 42682 (base), language depot, 42751 (DS binaries)
- **DLC Collection 1**: depot 42695
- **DLC Collection 2**: depot 42696
- **DLC Collection 3**: depot 42697
- **DLC Collection 4**: depot 42698

The language depot defaults to English (42683) but can be swapped to Spanish, German, French, Italian, Russian, Japanese, or Polish.

Detection uses Steam's appmanifest files as the primary method, with a Windows registry fallback if those aren't found.

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
Make sure MW3 (42680), MW3 Multiplayer (42690), or Dedicated Server (42750) is fully installed through Steam. The tool checks both appmanifest files and the Windows registry. If it still cannot find your install, it will ask if you want to enter the path manually.

**A DLC download failed.**
The most likely cause is that your Steam account does not own that DLC collection. Each collection (1 through 4) is a separate purchase on Steam. The tool will tell you which collection failed.

**The tool says a DLC has the wrong version.**
This means the marker file exists but is a different size than expected. The tool will re-download that collection to replace it with the correct version.

**Can I use this on Steam Deck or Linux?**
This tool is for Windows only. Steam Deck and Linux users can use [DeckOps](https://github.com/GalvarinoDev/DeckOps-Nightly), which has a built-in MW3 downgrader.

## Credits

- [DepotDownloader](https://github.com/SteamRE/DepotDownloader) by SteamRE (MIT License). Bundled in the release exe for automated depot downloads.
- Detection and merge logic adapted from [DeckOps-Nightly](https://github.com/GalvarinoDev/DeckOps-Nightly).
- DLC depot and manifest IDs referenced from the [community depot list](https://gist.github.com/Josu-A/b3698ee46b66225401a5583044f5789a) by Josu-A.

## License

MIT
