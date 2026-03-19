# dnb-organizer 🎵

A Python tool that automatically organises Drum and Bass music libraries into a clean folder hierarchy using embedded metadata tags and online lookups from Beatport, MusicBrainz, and Discogs.

Available as both a **command-line script** and a **desktop GUI**.

---

## Output structure

```
[Genre] / [Label] / [Artist] / [Artist] - [Track Title].ext
```

Example:
```
Neurofunk/
├── Prspct Recordings/
│   ├── Deathmachine/
│   │   └── Deathmachine - Photon Pain VIP.flac
│   └── Neonlight/
│       └── Neonlight - Orbit.flac
Liquid/
└── Hospital Records/
    └── Logistics/
        └── Logistics - Abandon The Machine.flac
_Unknown Genre/
└── Ram Records/
    └── Voltage/
        └── Voltage - Black Mamba.m4a
```

---

## Features

**Metadata**
- Reads `Artist`, `Label`, `Title`, and `Genre` tags from embedded file metadata
- Supports ID3 (MP3), Vorbis Comments (FLAC), iTunes atoms (M4A), and AIFF tags
- Handles all common tag key variants automatically (EasyID3, raw ID3, iTunes freeform atoms)

**Online lookup** — when tags are missing, queries external sources in order:
1. **Beatport** — preferred for DnB; extracts sub-genre (`Neurofunk`, `Liquid`, `Jump Up`, etc.) and label from the same page fetch — no extra API calls
2. **MusicBrainz** — free, no key required; community-voted genre tags
3. **Discogs** — free personal token required; uses `style` field for DnB sub-genres

**Smart search**
- Strips leading track numbers (`34 Black Mamba` → `Black Mamba`)
- Strips feat. credits before searching (`New Forever (feat. Samahra Eames)` → `New Forever`)
- Tries multiple query variants per source (artist + title → title only) to maximise hit rate
- Caches all results so the same track is never looked up twice

**File handling**
- Supports **MP3 · WAV · FLAC · M4A · AIFF**
- Strips illegal filesystem characters (`/ \ : * ? " < > |`), replacing with spaces
- Converts all folder names to Title Case
- Appends `(2)`, `(3)` etc. to resolve filename conflicts without overwriting
- Works on UNC paths (`\\NAS\Music`) and mapped drives — no admin required
- Recurses through subdirectories — safe to run on an entire NAS share

**Fallbacks**
- `_Unknown Genre` — if no genre is found in tags or online
- `_Unknown Label` — if no label is found
- `_Unknown Artist` — if no artist tag exists

---

## Installation

```bash
pip install mutagen
pip install customtkinter   # only needed for the GUI
```

Requires Python 3.10+.

---

## Usage

### GUI (recommended)

```bash
python dnb_organizer_ui.py
```

A dark-themed desktop window with folder pickers, toggles for each lookup source, a live progress bar, and a scrollable log.

### Command line

```bash
python dnb_organizer.py
```

You will be prompted for:
1. Whether to enable online lookup (and which sources)
2. Optional Discogs personal access token
3. **Source directory** — where your audio files currently live
4. **Destination directory** — where to build the organised hierarchy (can be the same as source to reorganise in-place)

#### NAS examples

**Windows (UNC path — works even without a mapped drive):**
```
Source:       \\NAS\Music\DnB
Destination:  \\NAS\Music\DnB
```

**Windows (mapped drive — run as your normal user, not admin):**
```
Source:       P:\Music\DnB
Destination:  P:\Music\DnB
```

**macOS / Linux:**
```
Source:       /Volumes/NAS/Music
Destination:  /Volumes/NAS/Music
```

---

## Getting a Discogs token

1. Log in at [discogs.com](https://www.discogs.com)
2. Go to **Settings → Developers**
3. Click **Generate new token**
4. Paste it into the script or GUI when prompted

---

## Notes

- Files are **moved**, not copied — no duplicate storage used
- Safe to re-run — conflicts are resolved automatically with numbered suffixes
- No files are ever deleted
- Online lookup adds ~2s per track (Beatport rate limit) for files with missing tags — for large libraries, expect a longer runtime
