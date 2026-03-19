# dnb-organizer 🎵

A Python script that automatically organises Drum and Bass music libraries (MP3, WAV, FLAC) into a clean folder hierarchy using embedded metadata tags.

## Output structure

```
[Label] / [Artist] / [Artist] - [Track Title].ext
```

Example:
```
PRSPCT Recordings/
├── Deathmachine/
│   └── Deathmachine - Photon Pain - VIP.flac
├── Dolphin/
│   ├── Dolphin - Ghost Notes VIP.flac
│   ├── Dolphin - Horror Flow.flac
│   └── Dolphin - Killer Beez.flac
└── Neurocore/
    ├── Neurocore - Dunes.flac
    └── Neurocore - Energy Never Dies.flac
```

## Features

- Reads `Artist`, `Label/Publisher`, and `Title` tags via [mutagen](https://mutagen.readthedocs.io/)
- Supports **MP3**, **WAV**, and **FLAC** (handles ID3, Vorbis Comments, and EasyID3 key variants automatically)
- Falls back to `_Unknown Label` / `_Unknown Artist` for files with missing tags
- Strips illegal filesystem characters (`/ \ : * ? " < > |`), replacing them with spaces to preserve word boundaries
- Converts all folder names to Title Case
- Appends `(2)`, `(3)` etc. to avoid overwriting files with the same name
- Recurses through subdirectories — works on an entire NAS share in one run

## Requirements

- Python 3.7+
- [mutagen](https://pypi.org/project/mutagen/)

```bash
pip install mutagen
```

## Usage

```bash
python dnb_organizer.py
```

You will be prompted for:

1. **Source directory** — where your audio files currently live (e.g. a NAS share or local folder)
2. **Destination directory** — where the organised hierarchy should be created (can be the same as source to reorganise in-place)

Then confirm with `y` and let it run.

### Example (Windows NAS)

```
Source:       \\NAS\Music\DnB
Destination:  \\NAS\Music\DnB
```

### Example (macOS / Linux NAS mount)

```
Source:       /Volumes/NAS/Music
Destination:  /Volumes/NAS/Music
```

## Notes

- Files are **moved**, not copied — no duplicate storage used
- The script is safe to re-run: conflicts are resolved automatically with numbered suffixes
- No files are deleted at any point
