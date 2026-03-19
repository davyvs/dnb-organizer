"""
╔══════════════════════════════════════════════════════════════╗
║          DnB Music Library Organizer  v1.0                   ║
║   Organizes MP3 / WAV / FLAC files by Label → Artist        ║
╚══════════════════════════════════════════════════════════════╝

Dependencies:
    pip install mutagen

Usage:
    python dnb_organizer.py
    (You will be prompted for Source and Destination directories.)
"""

import os
import re
import shutil
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.id3 import ID3NoHeaderError


# ─── Constants ────────────────────────────────────────────────────────────────

SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".flac"}

ILLEGAL_CHARS_RE = re.compile(r'[\\/:*?"<>|]')

UNKNOWN_LABEL  = "_Unknown Label"
UNKNOWN_ARTIST = "_Unknown Artist"


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
    """Convert a string to Title Case, preserving all-caps acronyms (e.g. 'DJ')."""
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
            # ID3 frames expose .text; Vorbis comments return plain lists
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

        # Artist  — ID3: TPE1 / easy: 'artist'
        result["artist"] = extract_tag(
            audio,
            "artist",      # EasyID3 / EasyMP4 / Vorbis
            "TPE1",        # Raw ID3
            "©ART",        # MP4
        )

        # Label / Publisher  — ID3: TPUB / easy: 'organization' or 'label'
        result["label"] = extract_tag(
            audio,
            "organization",  # EasyID3 maps TPUB here
            "label",         # Vorbis comment (FLAC)
            "publisher",     # Some taggers
            "TPUB",          # Raw ID3
        )

        # Title  — used for the filename
        result["title"] = extract_tag(
            audio,
            "title",   # EasyID3 / Vorbis
            "TIT2",    # Raw ID3
            "©nam",    # MP4
        )

    except (ID3NoHeaderError, Exception):
        pass  # File has no tags — all fields stay empty

    return result


def build_filename(artist_folder: str, title: str, ext: str) -> str:
    """
    Build the destination filename:  'Artist - Track Title.ext'
    Falls back gracefully if title is missing.
    """
    clean_title = sanitize(title).strip()
    if clean_title:
        clean_title = title_case(clean_title)
        return f"{artist_folder} - {clean_title}{ext}"
    # No title tag: keep the original filename (already cleaned by caller)
    return None  # Signal to caller to use original stem


# ─── Core Logic ───────────────────────────────────────────────────────────────

def organize_library(source_dir: Path, dest_dir: Path) -> None:
    """
    Walk `source_dir` recursively, read metadata from every supported audio
    file, and move it into:
        dest_dir / [Label] / [Artist] / [Artist] - [Title].ext
    """
    files_found   = 0
    files_moved   = 0
    files_skipped = 0
    errors        = []

    # Collect all supported files first so progress is predictable
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
        files_found += 1
        rel = filepath.relative_to(source_dir)
        print(f"  [{idx}/{total}]  {rel}", end="  →  ", flush=True)

        try:
            meta = read_metadata(filepath)

            label_folder  = clean_folder_name(meta["label"],  UNKNOWN_LABEL)
            artist_folder = clean_folder_name(meta["artist"], UNKNOWN_ARTIST)
            ext           = filepath.suffix.lower()

            # Build filename
            filename = build_filename(artist_folder, meta["title"], ext)
            if filename is None:
                # No title tag: sanitise & Title-Case the original stem
                original_stem = title_case(sanitize(filepath.stem))
                filename = f"{original_stem}{ext}"

            # Create target directory
            target_dir = dest_dir / label_folder / artist_folder
            target_dir.mkdir(parents=True, exist_ok=True)

            # Resolve conflicts
            dest_file = unique_destination(target_dir / filename)

            # Move the file
            shutil.move(str(filepath), dest_file)
            print(f"{dest_file.relative_to(dest_dir)}")
            files_moved += 1

        except Exception as exc:
            print(f"ERROR — {exc}")
            errors.append((filepath, str(exc)))
            files_skipped += 1

    # ── Summary ──────────────────────────────────────────────────────────────
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
    """Prompt the user for a directory path and validate it."""
    while True:
        raw = input(prompt_text).strip().strip('"').strip("'")
        path = Path(raw).expanduser().resolve()
        if path.is_dir():
            return path
        print(f"  ⚠  '{path}' is not a valid directory. Please try again.\n")


def main() -> None:
    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║          DnB Music Library Organizer  v1.0                   ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()
    print("  Supported formats : MP3 · WAV · FLAC")
    print("  Output structure  : [Label] / [Artist] / [Artist] - [Title].ext")
    print()

    source_dir = prompt_directory("  Source directory (where your files are now):\n  > ")
    dest_dir   = prompt_directory("  Destination directory (where to put the organised files):\n  > ")

    print(f"\n  Source      : {source_dir}")
    print(f"  Destination : {dest_dir}")

    confirm = input("\n  Proceed? [y/N]  ").strip().lower()
    if confirm not in ("y", "yes"):
        print("  Aborted.")
        return

    organize_library(source_dir, dest_dir)


if __name__ == "__main__":
    main()
