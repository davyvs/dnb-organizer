"""
╔══════════════════════════════════════════════════════════════╗
║          DnB Music Library Organizer  v1.1                   ║
║   Organizes MP3 / WAV / FLAC files by Label → Artist        ║
╚══════════════════════════════════════════════════════════════╝

Dependencies:
    pip install mutagen requests

Usage:
    python dnb_organizer.py
    (You will be prompted for Source and Destination directories,
     and optionally a Discogs personal access token.)
"""

import os
import re
import json
import time
import shutil
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.id3 import ID3NoHeaderError


# ─── Constants ────────────────────────────────────────────────────────────────

SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".aiff", ".aif"}

ILLEGAL_CHARS_RE = re.compile(r'[\\/:*?"<>|]')

UNKNOWN_LABEL  = "_Unknown Label"
UNKNOWN_ARTIST = "_Unknown Artist"

# MusicBrainz requires at least 1 second between requests
MB_RATE_LIMIT  = 1.1
MB_USER_AGENT  = "dnb-organizer/1.1 ( https://github.com/davyvs/dnb-organizer )"

DISCOGS_RATE_LIMIT = 1.1


# ─── Online Lookup Cache & Rate Limiter ───────────────────────────────────────

_label_cache: dict  = {}   # (artist, title) → label string
_last_mb_call: list = [0.0]
_last_dg_call: list = [0.0]


def _rate_limit(last_call_ref: list, interval: float) -> None:
    """Block until `interval` seconds have passed since the last call."""
    elapsed = time.time() - last_call_ref[0]
    if elapsed < interval:
        time.sleep(interval - elapsed)
    last_call_ref[0] = time.time()


def _http_get(url: str, headers: dict) -> dict | None:
    """Perform a GET request and return parsed JSON, or None on failure."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


# ─── MusicBrainz Lookup ───────────────────────────────────────────────────────

def lookup_label_musicbrainz(artist: str, title: str) -> str:
    """
    Search MusicBrainz for a recording matching artist + title.
    Returns the first label name found, or '' if nothing useful comes back.
    """
    _rate_limit(_last_mb_call, MB_RATE_LIMIT)

    query_parts = []
    if title:
        query_parts.append(f'recording:"{title}"')
    if artist:
        query_parts.append(f'artist:"{artist}"')
    if not query_parts:
        return ""

    query = " AND ".join(query_parts)
    url = (
        "https://musicbrainz.org/ws/2/recording/?"
        + urllib.parse.urlencode({"query": query, "fmt": "json", "limit": "3"})
    )

    data = _http_get(url, {"User-Agent": MB_USER_AGENT, "Accept": "application/json"})
    if not data:
        return ""

    for recording in data.get("recordings", []):
        for release in recording.get("releases", []):
            for label_info in release.get("label-info", []):
                name = label_info.get("label", {}).get("name", "").strip()
                if name and name.lower() not in ("self-released", "not on label"):
                    return name

    return ""


# ─── Discogs Lookup ───────────────────────────────────────────────────────────

def lookup_label_discogs(artist: str, title: str, token: str) -> str:
    """
    Search Discogs for a release matching artist + title.
    Returns the first label name found, or '' on failure.
    Requires a Discogs personal access token.
    """
    _rate_limit(_last_dg_call, DISCOGS_RATE_LIMIT)

    params = {"type": "release", "token": token, "per_page": "3"}
    if title:
        params["q"] = title
    if artist:
        params["artist"] = artist

    url = "https://api.discogs.com/database/search?" + urllib.parse.urlencode(params)

    data = _http_get(
        url,
        {
            "User-Agent": MB_USER_AGENT,
            "Authorization": f"Discogs token={token}",
        },
    )
    if not data:
        return ""

    for result in data.get("results", []):
        labels = result.get("label", [])
        if labels:
            return labels[0].strip()

    return ""


# ─── Combined Online Lookup ───────────────────────────────────────────────────

def lookup_label_online(artist: str, title: str, discogs_token: str = "") -> str:
    """
    Try MusicBrainz first, then Discogs (if a token is provided).
    Results are cached so the same (artist, title) is never looked up twice.
    Returns a label string, or '' if nothing is found.
    """
    cache_key = (artist.lower().strip(), title.lower().strip())
    if cache_key in _label_cache:
        return _label_cache[cache_key]

    label = ""

    # 1 — MusicBrainz (free, no key required)
    if artist or title:
        try:
            label = lookup_label_musicbrainz(artist, title)
        except Exception:
            label = ""

    # 2 — Discogs fallback
    if not label and discogs_token and (artist or title):
        try:
            label = lookup_label_discogs(artist, title, discogs_token)
        except Exception:
            label = ""

    _label_cache[cache_key] = label
    return label


# ─── Helpers ──────────────────────────────────────────────────────────────────

def sanitize(name: str) -> str:
    """
    Replace illegal filesystem characters with a space (preserving word
    boundaries, e.g. 'Hospital/Records' → 'Hospital Records') then collapse
    any runs of whitespace and strip the result.
    """
    replaced = ILLEGAL_CHARS_RE.sub(" ", name)
    return re.sub(r"\s+", " ", replaced).strip()


def title_case(name: str) -> str:
    """Convert a string to Title Case."""
    return name.title()


def clean_folder_name(raw: str, fallback: str) -> str:
    """
    Return a clean, Title-Cased folder name.
    Falls back to `fallback` if `raw` is empty after cleaning.
    """
    cleaned = sanitize(raw).strip()
    if not cleaned:
        return fallback
    return title_case(cleaned)


def unique_destination(dest_path: Path) -> Path:
    """
    If `dest_path` already exists, append an incrementing number before the
    extension until a free name is found.

    Example:  Artist - Track.mp3  →  Artist - Track (2).mp3
    """
    if not dest_path.exists():
        return dest_path

    stem   = dest_path.stem
    suffix = dest_path.suffix
    parent = dest_path.parent
    counter = 2

    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def extract_tag(audio, *keys: str) -> str:
    """
    Try multiple tag key names and return the first non-empty string found.
    Handles both dict-style (MP3/ID3) and list-style (FLAC/Vorbis) tag values.
    Returns an empty string if nothing is found.
    """
    for key in keys:
        try:
            value = audio.get(key)
            if value is None:
                continue
            if hasattr(value, "text"):
                text = " / ".join(str(v) for v in value.text if str(v).strip())
            elif isinstance(value, (list, tuple)):
                text = " / ".join(str(v) for v in value if str(v).strip())
            else:
                text = str(value).strip()
            if text:
                return text
        except Exception:
            continue
    return ""


def read_metadata(filepath: Path) -> dict:
    """
    Return a dict with 'artist', 'label', and 'title' extracted from the file.
    Gracefully handles unreadable or tag-less files.
    """
    result = {"artist": "", "label": "", "title": ""}

    try:
        audio = MutagenFile(filepath, easy=True)
        if audio is None:
            return result

        result["artist"] = extract_tag(
            audio,
            "artist",                        # EasyID3 / EasyMP4 / Vorbis
            "TPE1",                          # Raw ID3
            "©ART",                          # Raw MP4
        )
        result["label"] = extract_tag(
            audio,
            "organization",                  # EasyID3 → TPUB
            "label",                         # Vorbis (FLAC)
            "publisher",                     # Some taggers
            "TPUB",                          # Raw ID3
            "----:com.apple.iTunes:LABEL",   # iTunes M4A freeform atom
            "----:com.apple.iTunes:label",
            "----:com.apple.iTunes:Publisher",
            "----:com.apple.iTunes:publisher",
        )
        result["title"] = extract_tag(
            audio,
            "title",                         # EasyID3 / EasyMP4 / Vorbis
            "TIT2",                          # Raw ID3
            "©nam",                          # Raw MP4
        )

    except (ID3NoHeaderError, Exception):
        pass

    return result


def build_filename(artist_folder: str, title: str, ext: str) -> str | None:
    """
    Build the destination filename:  'Artist - Track Title.ext'
    Returns None if title is missing (caller uses original stem).
    """
    clean_title = sanitize(title).strip()
    if clean_title:
        return f"{artist_folder} - {title_case(clean_title)}{ext}"
    return None


# ─── Core Logic ───────────────────────────────────────────────────────────────

def organize_library(
    source_dir: Path,
    dest_dir: Path,
    discogs_token: str = "",
    use_online: bool = True,
) -> None:
    """
    Walk `source_dir` recursively, read metadata from every supported audio
    file, and move it into:
        dest_dir / [Label] / [Artist] / [Artist] - [Title].ext

    When the Label tag is empty, queries MusicBrainz (and optionally Discogs)
    to fill it in before falling back to _Unknown Label.
    """
    files_moved   = 0
    files_skipped = 0
    errors        = []

    all_files = [
        p for p in source_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    total = len(all_files)
    if total == 0:
        print("\n⚠  No supported audio files found in the source directory.")
        return

    print(f"\n  Found {total} audio file(s). Starting organisation…\n")

    for idx, filepath in enumerate(all_files, 1):
        rel = filepath.relative_to(source_dir)
        print(f"  [{idx}/{total}]  {rel}", end="", flush=True)

        try:
            meta = read_metadata(filepath)

            # ── Online label lookup when tag is missing ───────────────────
            if not meta["label"] and use_online and (meta["artist"] or meta["title"]):
                print("  🔍 looking up label…", end="", flush=True)
                found = lookup_label_online(
                    meta["artist"], meta["title"], discogs_token
                )
                if found:
                    meta["label"] = found
                    print(f" found: {found}", end="", flush=True)
                else:
                    print("  not found", end="", flush=True)

            print("  →  ", end="", flush=True)

            label_folder  = clean_folder_name(meta["label"],  UNKNOWN_LABEL)
            artist_folder = clean_folder_name(meta["artist"], UNKNOWN_ARTIST)
            ext           = filepath.suffix.lower()

            filename = build_filename(artist_folder, meta["title"], ext)
            if filename is None:
                original_stem = title_case(sanitize(filepath.stem))
                filename = f"{original_stem}{ext}"

            target_dir = dest_dir / label_folder / artist_folder
            target_dir.mkdir(parents=True, exist_ok=True)

            dest_file = unique_destination(target_dir / filename)
            shutil.move(str(filepath), dest_file)
            print(f"{dest_file.relative_to(dest_dir)}")
            files_moved += 1

        except Exception as exc:
            print(f"\n  ERROR — {exc}")
            errors.append((filepath, str(exc)))
            files_skipped += 1

    # ── Summary ───────────────────────────────────────────────────────────
    print("\n" + "─" * 60)
    print(f"  ✔  Moved   : {files_moved}")
    print(f"  ✖  Skipped : {files_skipped}")
    if errors:
        print("\n  Files with errors:")
        for fp, msg in errors:
            print(f"    • {fp.name}  —  {msg}")
    print("─" * 60)
    print("  Done! Your library has been organised.")


# ─── Entry Point ──────────────────────────────────────────────────────────────

def prompt_directory(prompt_text: str) -> Path:
    """
    Prompt for a directory path. Uses Path directly without resolve() so
    that UNC paths (\\NAS\share) and mapped drives work on Windows.
    """
    while True:
        raw = input(prompt_text).strip().strip('"').strip("'")
        if not raw:
            print("  ⚠  Please enter a path.\n")
            continue
        try:
            path = Path(raw).expanduser()
            if path.is_dir():
                return path
        except (OSError, ValueError):
            pass
        print(f"  ⚠  '{raw}' is not a valid directory. Please try again.\n")
        print("       Tip: Use the full path, e.g.  P:\\Music  or  \\\\NAS\\Music\n")


def main() -> None:
    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║          DnB Music Library Organizer  v1.1                   ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()
    print("  Supported formats : MP3 · WAV · FLAC · M4A · AIFF")
    print("  Output structure  : [Label] / [Artist] / [Artist] - [Title].ext")
    print()

    # ── Online lookup setup ───────────────────────────────────────────────
    print("  ── Online label lookup (for files missing a Label tag) ──────")
    print("  MusicBrainz will be tried automatically (free, no key needed).")
    print()
    print("  Discogs token (optional — press Enter to skip):")
    print("  Get one free at: discogs.com → Settings → Developers")
    discogs_token = input("  Discogs token: ").strip()
    if discogs_token:
        print("  ✔  Discogs enabled as fallback.")
    else:
        print("  ℹ  Discogs skipped — MusicBrainz only.")
    print()

    use_online_raw = input("  Enable online lookup? [Y/n]  ").strip().lower()
    use_online = use_online_raw not in ("n", "no")
    print()

    # ── Directories ───────────────────────────────────────────────────────
    source_dir = prompt_directory("  Source directory (where your files are now):\n  > ")
    dest_dir   = prompt_directory("  Destination directory (where to put the organised files):\n  > ")

    print(f"\n  Source      : {source_dir}")
    print(f"  Destination : {dest_dir}")
    print(f"  Online lookup: {'MusicBrainz' + (' + Discogs' if discogs_token else '')  if use_online else 'disabled'}")

    confirm = input("\n  Proceed? [y/N]  ").strip().lower()
    if confirm not in ("y", "yes"):
        print("  Aborted.")
        return

    organize_library(source_dir, dest_dir, discogs_token, use_online)


if __name__ == "__main__":
    main()
