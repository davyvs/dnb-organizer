"""
╔══════════════════════════════════════════════════════════════╗
║          DnB Music Library Organizer  v1.5                   ║
║   Organizes MP3 / WAV / FLAC / M4A files by Genre → Label   ║
╚══════════════════════════════════════════════════════════════╝

Dependencies:
    pip install mutagen

Usage:
    python dnb_organizer.py
    (You will be prompted for Source, Destination, and optional
     Discogs token.)

Output structure:
    [Genre] / [Label] / [Artist] / [Artist] - [Title].ext

Online metadata lookup order (label + genre, when tags are missing):
    1. Beatport      — free, no key (sub_genre from __NEXT_DATA__ JSON)
    2. MusicBrainz   — free, no key
    3. Discogs       — free token required (discogs.com → Settings → Developers)
"""

import re
import json
import time
import shutil
import urllib.request
import urllib.parse
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.id3 import ID3NoHeaderError


# ─── Constants ────────────────────────────────────────────────────────────────

SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".aiff", ".aif"}

ILLEGAL_CHARS_RE = re.compile(r'[\\/:*?"<>|]')

UNKNOWN_LABEL  = "_Unknown Label"
UNKNOWN_ARTIST = "_Unknown Artist"
UNKNOWN_GENRE  = "_Unknown Genre"

MB_RATE_LIMIT      = 1.1
MB_USER_AGENT      = "dnb-organizer/1.5 ( https://github.com/davyvs/dnb-organizer )"
DISCOGS_RATE_LIMIT  = 1.1
BEATPORT_RATE_LIMIT = 2.0


# ─── Online Lookup Cache & Rate Limiter ───────────────────────────────────────

# Cache stores {"label": str, "genre": str} keyed by (artist, title)
_online_cache: dict  = {}
_last_mb_call: list  = [0.0]
_last_dg_call: list  = [0.0]
_last_bp_call: list  = [0.0]


def _rate_limit(last_call_ref: list, interval: float) -> None:
    elapsed = time.time() - last_call_ref[0]
    if elapsed < interval:
        time.sleep(interval - elapsed)
    last_call_ref[0] = time.time()


def _http_get(url: str, headers: dict) -> dict | None:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _http_get_html(url: str, headers: dict) -> str:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


# ─── Search Query Helpers ─────────────────────────────────────────────────────

_FEAT_RE     = re.compile(r"\s*[\(\[]\s*(?:feat|ft|featuring)\.?\s+[^\)\]]+[\)\]]", re.IGNORECASE)
_TRACKNUM_RE = re.compile(r"^\d{1,3}[.\s]+")


def clean_search_title(title: str) -> str:
    t = _TRACKNUM_RE.sub("", title).strip()
    t = _FEAT_RE.sub("", t).strip()
    return t


def search_variants(artist: str, title: str):
    """Yield (artist, title) pairs from most to least specific."""
    clean = clean_search_title(title)
    if clean and artist:
        yield artist, clean
    if clean and clean != title:
        yield artist, title
    if clean:
        yield "", clean


# ─── Beatport Lookup ──────────────────────────────────────────────────────────

_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.DOTALL,
)

_BEATPORT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _extract_beatport_data(data: dict) -> dict:
    """
    Walk Beatport's __NEXT_DATA__ and return the first result's label and genre.

    Beatport nests results under:
      props → pageProps → dehydratedState → queries → [*] → state → data → results

    Genre priority:
      sub_genre.name  (e.g. "Neurofunk", "Liquid", "Jump Up")
      genre.name      (e.g. "Drum & Bass") — only used if sub_genre is absent
                       or is just "Drum & Bass" (too broad to be useful)
    """
    result = {"label": "", "genre": ""}
    try:
        queries = (
            data.get("props", {})
                .get("pageProps", {})
                .get("dehydratedState", {})
                .get("queries", [])
        )
        for query in queries:
            results = (
                query.get("state", {})
                     .get("data", {})
                     .get("results", [])
            )
            for track in results:
                # ── Label ─────────────────────────────────────────────────
                if not result["label"]:
                    label = track.get("label", {})
                    if isinstance(label, dict):
                        result["label"] = label.get("name", "").strip()
                    if not result["label"]:
                        release = track.get("release", {})
                        if isinstance(release, dict):
                            lbl = release.get("label", {})
                            if isinstance(lbl, dict):
                                result["label"] = lbl.get("name", "").strip()

                # ── Genre ─────────────────────────────────────────────────
                if not result["genre"]:
                    sub = track.get("sub_genre", {})
                    if isinstance(sub, dict):
                        name = sub.get("name", "").strip()
                        # sub_genre "Drum & Bass" is too broad — skip it
                        if name and "drum" not in name.lower():
                            result["genre"] = name

                    # Fall back to top-level genre if sub_genre wasn't useful
                    if not result["genre"]:
                        top = track.get("genre", {})
                        if isinstance(top, dict):
                            result["genre"] = top.get("name", "").strip()

                if result["label"] and result["genre"]:
                    return result

    except Exception:
        pass
    return result


def _bp_query(artist: str, title: str) -> dict:
    """Fetch a Beatport search page and return {"label": str, "genre": str}."""
    _rate_limit(_last_bp_call, BEATPORT_RATE_LIMIT)

    query = " ".join(filter(None, [artist, title])).strip()
    if not query:
        return {"label": "", "genre": ""}

    url = "https://www.beatport.com/search/tracks?" + urllib.parse.urlencode({"q": query})
    html = _http_get_html(url, _BEATPORT_HEADERS)
    if not html:
        return {"label": "", "genre": ""}

    match = _NEXT_DATA_RE.search(html)
    if not match:
        return {"label": "", "genre": ""}

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {"label": "", "genre": ""}

    return _extract_beatport_data(data)


def lookup_beatport(artist: str, title: str) -> dict:
    """Try multiple query variants on Beatport; return best {"label", "genre"}."""
    best = {"label": "", "genre": ""}
    for a, t in search_variants(artist, title):
        result = _bp_query(a, t)
        if result["label"] and not best["label"]:
            best["label"] = result["label"]
        if result["genre"] and not best["genre"]:
            best["genre"] = result["genre"]
        if best["label"] and best["genre"]:
            break
    return best


# ─── MusicBrainz Lookup ───────────────────────────────────────────────────────

def _mb_query(artist: str, title: str) -> dict:
    """Run a MusicBrainz search; return {"label": str, "genre": str}."""
    _rate_limit(_last_mb_call, MB_RATE_LIMIT)

    query_parts = []
    if title:
        query_parts.append(f'recording:"{title}"')
    if artist:
        query_parts.append(f'artist:"{artist}"')
    if not query_parts:
        return {"label": "", "genre": ""}

    query = " AND ".join(query_parts)
    url = (
        "https://musicbrainz.org/ws/2/recording/?"
        + urllib.parse.urlencode({
            "query": query, "fmt": "json", "limit": "5",
            "inc": "genres+tags+releases+label-info",
        })
    )

    data = _http_get(url, {"User-Agent": MB_USER_AGENT, "Accept": "application/json"})
    if not data:
        return {"label": "", "genre": ""}

    result = {"label": "", "genre": ""}

    for recording in data.get("recordings", []):
        # Genre: from the recording's genre/tag list
        if not result["genre"]:
            genres = recording.get("genres", []) or recording.get("tags", [])
            # Sort by count descending (most voted first)
            genres_sorted = sorted(genres, key=lambda g: g.get("count", 0), reverse=True)
            for g in genres_sorted:
                name = g.get("name", "").strip()
                if name and name.lower() not in ("drum and bass", "drum & bass", "dnb", ""):
                    result["genre"] = name.title()
                    break

        # Label: from releases
        if not result["label"]:
            for release in recording.get("releases", []):
                for label_info in release.get("label-info", []):
                    name = label_info.get("label", {}).get("name", "").strip()
                    if name and name.lower() not in ("self-released", "not on label"):
                        result["label"] = name
                        break
                if result["label"]:
                    break

        if result["label"] and result["genre"]:
            break

    return result


def lookup_musicbrainz(artist: str, title: str) -> dict:
    """Try multiple query variants on MusicBrainz."""
    best = {"label": "", "genre": ""}
    for a, t in search_variants(artist, title):
        result = _mb_query(a, t)
        if result["label"] and not best["label"]:
            best["label"] = result["label"]
        if result["genre"] and not best["genre"]:
            best["genre"] = result["genre"]
        if best["label"] and best["genre"]:
            break
    return best


# ─── Discogs Lookup ───────────────────────────────────────────────────────────

def _dg_query(artist: str, title: str, token: str) -> dict:
    """Run a Discogs search; return {"label": str, "genre": str}."""
    _rate_limit(_last_dg_call, DISCOGS_RATE_LIMIT)

    params = {"type": "release", "token": token, "per_page": "5"}
    if title:
        params["q"] = title
    if artist:
        params["artist"] = artist

    url = "https://api.discogs.com/database/search?" + urllib.parse.urlencode(params)
    data = _http_get(url, {
        "User-Agent": MB_USER_AGENT,
        "Authorization": f"Discogs token={token}",
    })
    if not data:
        return {"label": "", "genre": ""}

    result = {"label": "", "genre": ""}
    for r in data.get("results", []):
        if not result["label"]:
            labels = r.get("label", [])
            if labels:
                result["label"] = labels[0].strip()
        if not result["genre"]:
            # Discogs 'style' is more specific than 'genre' for DnB
            styles = r.get("style", [])
            genres = r.get("genre", [])
            candidates = styles or genres
            for s in candidates:
                if s.lower() not in ("electronic", "drum n bass", "drum & bass", "dnb"):
                    result["genre"] = s.strip()
                    break
        if result["label"] and result["genre"]:
            break
    return result


def lookup_discogs(artist: str, title: str, token: str) -> dict:
    """Try multiple query variants on Discogs."""
    best = {"label": "", "genre": ""}
    for a, t in search_variants(artist, title):
        result = _dg_query(a, t, token)
        if result["label"] and not best["label"]:
            best["label"] = result["label"]
        if result["genre"] and not best["genre"]:
            best["genre"] = result["genre"]
        if best["label"] and best["genre"]:
            break
    return best


# ─── Combined Online Lookup ───────────────────────────────────────────────────

def lookup_online(
    artist: str,
    title: str,
    discogs_token: str = "",
    use_beatport: bool = True,
) -> dict:
    """
    Return {"label": str, "genre": str} by querying sources in order:
        1. Beatport     — label + sub_genre from same page fetch
        2. MusicBrainz  — label + community genre tags
        3. Discogs      — label + style tags (token required)

    Results are cached. Each source fills in whichever fields are still
    empty, so a partial Beatport hit (label only) will still try MusicBrainz
    for the genre.
    """
    cache_key = (artist.lower().strip(), title.lower().strip())
    if cache_key in _online_cache:
        return _online_cache[cache_key]

    best = {"label": "", "genre": ""}

    def _merge(result: dict) -> None:
        if result["label"] and not best["label"]:
            best["label"] = result["label"]
        if result["genre"] and not best["genre"]:
            best["genre"] = result["genre"]

    # 1 — Beatport (gives both in one fetch)
    if use_beatport and (artist or title):
        try:
            _merge(lookup_beatport(artist, title))
        except Exception:
            pass

    # 2 — MusicBrainz (fill in whatever's still missing)
    if (not best["label"] or not best["genre"]) and (artist or title):
        try:
            _merge(lookup_musicbrainz(artist, title))
        except Exception:
            pass

    # 3 — Discogs
    if (not best["label"] or not best["genre"]) and discogs_token and (artist or title):
        try:
            _merge(lookup_discogs(artist, title, discogs_token))
        except Exception:
            pass

    _online_cache[cache_key] = best
    return best


# ─── Helpers ──────────────────────────────────────────────────────────────────

def sanitize(name: str) -> str:
    replaced = ILLEGAL_CHARS_RE.sub(" ", name)
    return re.sub(r"\s+", " ", replaced).strip()


def title_case(name: str) -> str:
    return name.title()


def clean_folder_name(raw: str, fallback: str) -> str:
    cleaned = sanitize(raw).strip()
    if not cleaned:
        return fallback
    return title_case(cleaned)


def unique_destination(dest_path: Path) -> Path:
    if not dest_path.exists():
        return dest_path
    stem, suffix, parent = dest_path.stem, dest_path.suffix, dest_path.parent
    counter = 2
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def extract_tag(audio, *keys: str) -> str:
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
    """Return artist, label, title, and genre from the file's embedded tags."""
    result = {"artist": "", "label": "", "title": "", "genre": ""}

    try:
        audio = MutagenFile(filepath, easy=True)
        if audio is None:
            return result

        result["artist"] = extract_tag(audio,
            "artist", "TPE1", "©ART")

        result["label"] = extract_tag(audio,
            "organization", "label", "publisher", "TPUB",
            "----:com.apple.iTunes:LABEL",
            "----:com.apple.iTunes:label",
            "----:com.apple.iTunes:Publisher",
            "----:com.apple.iTunes:publisher")

        result["title"] = extract_tag(audio,
            "title", "TIT2", "©nam")

        result["genre"] = extract_tag(audio,
            "genre",                           # EasyID3 / Vorbis / EasyMP4
            "TCON",                            # Raw ID3
            "©gen",                            # Raw MP4
            "----:com.apple.iTunes:GENRE")

    except (ID3NoHeaderError, Exception):
        pass

    return result


def build_filename(artist_folder: str, title: str, ext: str) -> str | None:
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
    use_beatport: bool = True,
) -> None:
    """
    Walk source_dir recursively and move each audio file into:
        dest_dir / [Genre] / [Label] / [Artist] / [Artist] - [Title].ext

    Missing label and genre tags are resolved via online lookup before
    falling back to _Unknown Label / _Unknown Genre.
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
            needs_lookup = use_online and (meta["artist"] or meta["title"])
            needs_label  = not meta["label"]
            needs_genre  = not meta["genre"]

            # ── Online lookup when label or genre is missing ───────────────
            if needs_lookup and (needs_label or needs_genre):
                print("  🔍", end="", flush=True)
                found = lookup_online(
                    meta["artist"], meta["title"], discogs_token, use_beatport
                )
                if needs_label and found["label"]:
                    meta["label"] = found["label"]
                    print(f" label:{found['label']}", end="", flush=True)
                if needs_genre and found["genre"]:
                    meta["genre"] = found["genre"]
                    print(f" genre:{found['genre']}", end="", flush=True)
                if (needs_label and not meta["label"]) or (needs_genre and not meta["genre"]):
                    missing = []
                    if needs_label and not meta["label"]: missing.append("label")
                    if needs_genre and not meta["genre"]: missing.append("genre")
                    print(f"  [{'/'.join(missing)} not found]", end="", flush=True)

            print("  →  ", end="", flush=True)

            genre_folder  = clean_folder_name(meta["genre"],  UNKNOWN_GENRE)
            label_folder  = clean_folder_name(meta["label"],  UNKNOWN_LABEL)
            artist_folder = clean_folder_name(meta["artist"], UNKNOWN_ARTIST)
            ext           = filepath.suffix.lower()

            filename = build_filename(artist_folder, meta["title"], ext)
            if filename is None:
                filename = f"{title_case(sanitize(filepath.stem))}{ext}"

            target_dir = dest_dir / genre_folder / label_folder / artist_folder
            target_dir.mkdir(parents=True, exist_ok=True)

            dest_file = unique_destination(target_dir / filename)
            shutil.move(str(filepath), dest_file)
            print(f"{dest_file.relative_to(dest_dir)}")
            files_moved += 1

        except Exception as exc:
            print(f"\n  ERROR — {exc}")
            errors.append((filepath, str(exc)))
            files_skipped += 1

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
    print("║          DnB Music Library Organizer  v1.5                   ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()
    print("  Supported formats : MP3 · WAV · FLAC · M4A · AIFF")
    print("  Output structure  : [Genre] / [Label] / [Artist] / [Artist] - [Title].ext")
    print()

    # ── Online lookup setup ───────────────────────────────────────────────
    print("  ── Online metadata lookup (label + genre) ───────────────────")
    print("  Sources: Beatport (sub_genre) → MusicBrainz → Discogs")
    print()

    use_online_raw = input("  Enable online lookup? [Y/n]  ").strip().lower()
    use_online = use_online_raw not in ("n", "no")

    use_beatport  = False
    discogs_token = ""

    if use_online:
        bp_raw = input("  Enable Beatport lookup? [Y/n]  ").strip().lower()
        use_beatport = bp_raw not in ("n", "no")

        print()
        print("  Discogs token (optional — press Enter to skip):")
        print("  Get one free at: discogs.com → Settings → Developers")
        discogs_token = input("  Discogs token: ").strip()

        print()
        sources = ["MusicBrainz"]
        if use_beatport:
            sources.insert(0, "Beatport")
        if discogs_token:
            sources.append("Discogs")
        print(f"  ✔  Online sources: {' → '.join(sources)}")
    else:
        print("  ℹ  Online lookup disabled — using file tags only.")
    print()

    # ── Directories ───────────────────────────────────────────────────────
    source_dir = prompt_directory("  Source directory (where your files are now):\n  > ")
    dest_dir   = prompt_directory("  Destination directory (where to put the organised files):\n  > ")

    print(f"\n  Source      : {source_dir}")
    print(f"  Destination : {dest_dir}")
    print(f"  Structure   : Genre / Label / Artist / Track")

    confirm = input("\n  Proceed? [y/N]  ").strip().lower()
    if confirm not in ("y", "yes"):
        print("  Aborted.")
        return

    organize_library(source_dir, dest_dir, discogs_token, use_online, use_beatport)


if __name__ == "__main__":
    main()
