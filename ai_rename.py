#!/usr/bin/env python3
"""AI-powered Jellyfin Naming & Metadata Script

Uses an OpenAI-compatible language model to look up IMDB episode metadata
for TV-show files based on:
  • the show name    – taken from the top-level folder
  • the season       – taken from the season folder (if present)
  • the episode number – extracted from the filename

Files are then renamed to the Jellyfin best-practice naming scheme and a
companion ``.nfo`` sidecar file is written with IMDB metadata so that
Jellyfin can match and display rich episode information.

    Multi-season show  →  Show Name - S01E01 - Episode Title [tt0000000].ext
    Season-missing show → Show Name - S01E01 - Episode Title [tt0000000].ext

When no IMDB ID is available the ``[tt…]`` tag is omitted.

Shows with a single continuous run of episodes (e.g. Naruto, Yu-Gi-Oh) are
detected automatically when there is no season folder; missing season values
are defaulted to season 1.

Configuration is done through environment variables (or a .env file):
  MEDIA_FOLDER      Path to the root folder that contains the media files.
                    (required)
  OPENAI_API_KEY    API key for the OpenAI-compatible service.  (required)
  OPENAI_BASE_URL   Base URL for the API endpoint.
                    Default: https://api.openai.com/v1
    OPENAI_MODEL      Model to use for lookups.  Default: gpt-4o-mini
    OPENAI_TIMEOUT    Request timeout (seconds) for each AI API call.
                                        Default: 60
    OPENAI_BATCH_SIZE Episodes requested per API call.
                                        Default: 50
    DRY_RUN           Set to "1" to print what would be renamed without
                    actually renaming anything.  Default: 0 (off).
    DRY_RUN_FOLDER    Optional output folder used when DRY_RUN=1. If set,
                                        renamed copies and NFO files are written there, preserving
                                        relative subfolders from MEDIA_FOLDER.
"""

import json
import logging
import os
import re
import shutil
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(levelname)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Video file extensions that will be processed
# ---------------------------------------------------------------------------
VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv",
    ".m4v", ".ts", ".m2ts", ".mpg", ".mpeg", ".webm",
}

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

# Patterns to extract an episode number from a filename stem (tried in order)
_EP_PATTERNS = [
    # Standard:  S01E01  /  S1E1
    re.compile(
        r"[Ss](?P<season>\d{1,2})[Ee](?P<episode>\d{1,3})",
        re.IGNORECASE,
    ),
    # Alternate:  1x01  /  01x01
    re.compile(
        r"\b(?P<season>\d{1,2})x(?P<episode>\d{2,3})\b",
        re.IGNORECASE,
    ),
    # Bare episode keyword:  Ep01 / Episode 1 / ep.03
    re.compile(
        r"\b[Ee]p(?:isode)?\.?\s*(?P<episode>\d{1,3})\b",
        re.IGNORECASE,
    ),
    # Plain number:  "001" / "01" / "1"
    re.compile(
        r"\b(?P<episode>\d{1,3})\b",
    ),
]

# Detect a season number inside a folder name like "Season 01", "S2", etc.
_SEASON_FOLDER_RE = re.compile(
    r"[Ss](?:eason)?\s*(\d{1,2})",
    re.IGNORECASE,
)

# Replace dots, underscores, and whitespace runs with a single space.
_DOT_UNDER_RE = re.compile(r"[._\s]+")
_INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1F]')


# ---------------------------------------------------------------------------
# .env loader (same logic as rename.py)
# ---------------------------------------------------------------------------

def load_env_file(file_path: str = ".env", override: bool = False) -> int:
    """Load environment variables from a .env-style file.

    Returns the number of variables loaded.
    """
    if not os.path.isfile(file_path):
        return 0

    loaded = 0
    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue

                if line.startswith("export "):
                    line = line[len("export "):].strip()

                if "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()

                if not key:
                    continue

                if (
                    len(value) >= 2
                    and value[0] == value[-1]
                    and value[0] in {'"', "'"}
                ):
                    value = value[1:-1]

                if not override and key in os.environ:
                    continue

                os.environ[key] = value
                loaded += 1
    except OSError as exc:
        log.warning("Could not read %s: %s", file_path, exc)

    return loaded


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    """Normalise separators in a filename fragment."""
    text = _DOT_UNDER_RE.sub(" ", text)
    text = re.sub(r"(?:^|\s)-+|-+(?:$|\s)", " ", text)
    return re.sub(r" {2,}", " ", text).strip()


def _safe_filename_component(text: str) -> str:
    """Sanitise text so it is safe to use as part of a filename.

    AI-returned episode titles can contain characters that are invalid on
    Windows (for example ":"), so normalise those before building paths.
    """
    cleaned = _INVALID_FILENAME_CHARS_RE.sub(" ", text)
    cleaned = _clean(cleaned)
    # Windows disallows trailing dots/spaces in path components.
    return cleaned.rstrip(" .")


# ---------------------------------------------------------------------------
# Filename / path analysis
# ---------------------------------------------------------------------------

def extract_episode_number(stem: str):
    """Return the episode number found in *stem*, or ``None``."""
    for pattern in _EP_PATTERNS:
        m = pattern.search(stem)
        if m is not None:
            return int(m.group("episode"))
    return None


def extract_episode_title(stem: str) -> str:
    """Return an episode title fragment from *stem*, if present.

    Uses the same episode marker patterns as ``extract_episode_number`` and
    returns the cleaned text that appears after the episode marker.
    """
    for pattern in _EP_PATTERNS:
        m = pattern.search(stem)
        if m is not None:
            return _clean(stem[m.end() :])
    return ""


def parse_season_from_folder(folder_name: str):
    """Return a season number from a folder name like ``Season 01``, or ``None``."""
    m = _SEASON_FOLDER_RE.search(folder_name)
    if m:
        return int(m.group(1))
    return None


def infer_show_name(filepath: str, base_folder: str) -> str:
    """Infer the show name from path context.

    If *base_folder* is a library root, the first folder under it is treated
    as the show name. If *base_folder* is already a single show folder,
    fallback to that folder name.
    """
    rel = os.path.relpath(filepath, base_folder)
    parts = rel.split(os.sep)
    if len(parts) > 1:
        return _clean(parts[0])
    return _clean(os.path.basename(os.path.normpath(base_folder)))


def infer_season(filepath: str, base_folder: str):
    """Return a season number from the directory structure, or ``None``.

    Walks from the file's directory up to (but not including) the show-name
    folder looking for a season marker.
    """
    rel = os.path.relpath(filepath, base_folder)
    parts = rel.split(os.sep)
    # Directory parts only (exclude filename).
    dir_parts = parts[:-1]

    # If MEDIA_FOLDER is a library root and we have show/subfolders, skip the
    # first directory because it is the show folder. If MEDIA_FOLDER is already
    # a show folder, keep the first entry so folders like "Season 01" are seen.
    if len(dir_parts) >= 2:
        season_parts = dir_parts[1:]
    else:
        season_parts = dir_parts

    for part in season_parts:
        season = parse_season_from_folder(part)
        if season is not None:
            return season
    return None


# ---------------------------------------------------------------------------
# AI lookup
# ---------------------------------------------------------------------------

_BATCH_SIZE = 50  # max episodes per API call to stay within token limits
_MAX_API_RETRIES = 4
_RETRY_BASE_DELAY_SECONDS = 2.0
_RETRY_MAX_DELAY_SECONDS = 30.0


def _build_prompt(show_name: str, season, episode_numbers: list) -> str:
    """Build the prompt that asks the AI for IMDB episode metadata."""
    eps = ", ".join(str(e) for e in sorted(episode_numbers))

    if season is not None:
        context = (
            f'For the TV show "{show_name}", Season {season}, '
            f"provide the IMDB metadata for episodes: {eps}."
        )
    else:
        context = (
            f'For the TV show "{show_name}" '
            f"(this show uses a single continuous episode numbering with no "
            f"separate seasons), provide the IMDB metadata for "
            f"episodes: {eps}."
        )

    return (
        f"{context}\n\n"
        "Respond with ONLY a JSON object mapping episode numbers (as string "
        "keys) to objects containing IMDB metadata.  Each value must have:\n"
        '  "title"   – the official IMDB episode title\n'
        '  "imdb_id" – the IMDB ID (e.g. "tt0959621"), or null if unknown\n'
        '  "aired"   – the original air date as "YYYY-MM-DD", or null\n'
        '  "plot"    – a short plot summary, or null\n\n'
        "Do not include any other text.\n"
        "Example:\n"
        '{"1": {"title": "Pilot", "imdb_id": "tt0959621", '
        '"aired": "2008-01-20", "plot": "A chemistry teacher is diagnosed '
        'with cancer."}}'
    )


def query_episode_metadata(
    show_name: str,
    season,
    episode_numbers: list,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
    request_timeout: float = 60.0,
    batch_size: int = None,
) -> dict:
    """Query the AI for IMDB episode metadata.

    Returns ``{episode_number: {"title": str, "imdb_id": str|None,
    "aired": str|None, "plot": str|None}}``.

    Large batches are split into groups of *batch_size* episodes to stay
    within token limits.
    """
    all_meta: dict = {}
    sorted_eps = sorted(set(episode_numbers))
    effective_batch_size = max(1, int(_BATCH_SIZE if batch_size is None else batch_size))

    for i in range(0, len(sorted_eps), effective_batch_size):
        batch = sorted_eps[i : i + effective_batch_size]
        prompt = _build_prompt(show_name, season, batch)
        meta = _call_ai(prompt, api_key, base_url, model, request_timeout)
        all_meta.update(meta)

    return all_meta


def _retry_delay_seconds(http_error, attempt: int) -> float:
    """Return retry delay, honoring Retry-After when available."""
    headers = getattr(http_error, "headers", None)
    if headers:
        retry_after = headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass

    delay = _RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
    return min(delay, _RETRY_MAX_DELAY_SECONDS)


def _is_timeout_error(exc: OSError) -> bool:
    """Return True when an exception indicates request timeout."""
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    return "timed out" in str(exc).lower()


def _call_ai(
    prompt: str,
    api_key: str,
    base_url: str,
    model: str,
    request_timeout: float = 60.0,
) -> dict:
    """Make a single chat-completion request and parse the JSON response.

    Returns ``{episode_number_int: {"title": str, "imdb_id": str|None,
    "aired": str|None, "plot": str|None}}``.
    """
    url = f"{base_url.rstrip('/')}/chat/completions"

    payload = json.dumps({
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a TV-show metadata assistant that provides IMDB "
                    "episode information.  When asked for episode metadata, "
                    "respond with ONLY a valid JSON object mapping episode "
                    "numbers (as string keys) to objects with keys: "
                    '"title", "imdb_id", "aired", "plot".  '
                    "No markdown, no explanation."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    for attempt in range(1, _MAX_API_RETRIES + 2):
        try:
            with urllib.request.urlopen(req, timeout=request_timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if retryable and attempt <= _MAX_API_RETRIES:
                delay = _retry_delay_seconds(exc, attempt)
                log.warning(
                    "AI API HTTP error %s: %s. Retrying in %.1fs (%d/%d).",
                    exc.code,
                    exc.reason,
                    delay,
                    attempt,
                    _MAX_API_RETRIES,
                )
                time.sleep(delay)
                continue

            log.error("AI API HTTP error %s: %s", exc.code, exc.reason)
            return {}
        except urllib.error.URLError as exc:
            # Retry transient network errors using exponential backoff.
            if attempt <= _MAX_API_RETRIES:
                delay = min(
                    _RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)),
                    _RETRY_MAX_DELAY_SECONDS,
                )
                log.warning(
                    "AI API connection error: %s. Retrying in %.1fs (%d/%d).",
                    exc.reason,
                    delay,
                    attempt,
                    _MAX_API_RETRIES,
                )
                time.sleep(delay)
                continue

            log.error("AI API connection error: %s", exc.reason)
            return {}
        except json.JSONDecodeError as exc:
            log.error("AI API response error: %s", exc)
            return {}
        except OSError as exc:
            if _is_timeout_error(exc) and attempt <= _MAX_API_RETRIES:
                delay = min(
                    _RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)),
                    _RETRY_MAX_DELAY_SECONDS,
                )
                log.warning(
                    "AI API timeout: %s. Retrying in %.1fs (%d/%d).",
                    exc,
                    delay,
                    attempt,
                    _MAX_API_RETRIES,
                )
                time.sleep(delay)
                continue

            log.error("AI API response error: %s", exc)
            return {}

    # Extract the assistant's text reply
    try:
        text = body["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError):
        log.error("Unexpected AI API response structure.")
        return {}

    # Strip markdown code fences if the model wraps its answer
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        log.error("AI returned invalid JSON: %s", text[:200])
        return {}

    if not isinstance(raw, dict):
        log.error("AI returned non-object JSON: %s", type(raw).__name__)
        return {}

    # Normalise into {int: {title, imdb_id, aired, plot}}
    result: dict = {}
    for k, v in raw.items():
        ep = int(k)
        if isinstance(v, dict):
            result[ep] = {
                "title": str(v.get("title") or ""),
                "imdb_id": v.get("imdb_id") or None,
                "aired": v.get("aired") or None,
                "plot": str(v.get("plot") or "") if v.get("plot") else None,
            }
        else:
            # Graceful fallback: if the model returns a plain string title
            result[ep] = {
                "title": str(v),
                "imdb_id": None,
                "aired": None,
                "plot": None,
            }
    return result


# ---------------------------------------------------------------------------
# Filename building
# ---------------------------------------------------------------------------

def build_jellyfin_name(
    show_name: str, season, episode: int, episode_title: str,
    imdb_id: str = None,
) -> str:
    """Construct the Jellyfin-compliant filename stem.

    When *imdb_id* is provided the ``[ttXXXXXXX]`` tag is appended
    so Jellyfin can match the file to the correct IMDB entry.
    """
    if not episode_title:
        episode_title = f"Episode {episode:02d}"

    show_name = _safe_filename_component(show_name)
    episode_title = _safe_filename_component(episode_title)

    if season is not None:
        ep_marker = f"S{season:02d}E{episode:02d}"
    else:
        ep_marker = f"E{episode:02d}"

    if show_name:
        stem = f"{show_name} - {ep_marker} - {episode_title}"
    else:
        stem = f"{ep_marker} - {episode_title}"

    if imdb_id:
        stem = f"{stem} [{imdb_id}]"

    return stem


# ---------------------------------------------------------------------------
# NFO sidecar generation
# ---------------------------------------------------------------------------

def build_nfo_xml(
    show_name: str,
    season,
    episode: int,
    title: str,
    imdb_id: str = None,
    aired: str = None,
    plot: str = None,
) -> str:
    """Return the XML content for a Jellyfin/Kodi-style episode .nfo file."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<episodedetails>",
        f"  <title>{xml_escape(title)}</title>",
        f"  <showtitle>{xml_escape(show_name)}</showtitle>",
    ]
    if season is not None:
        lines.append(f"  <season>{season}</season>")
    lines.append(f"  <episode>{episode}</episode>")
    if imdb_id:
        lines.append(
            f'  <uniqueid type="imdb" default="true">{xml_escape(imdb_id)}</uniqueid>'
        )
    if aired:
        lines.append(f"  <aired>{xml_escape(aired)}</aired>")
    if plot:
        lines.append(f"  <plot>{xml_escape(plot)}</plot>")
    lines.append("</episodedetails>")
    return "\n".join(lines) + "\n"


def write_nfo(nfo_path: str, xml_content: str, dry_run: bool = False) -> bool:
    """Write an NFO sidecar file.  Returns ``True`` if written."""
    if dry_run:
        log.info("[DRY RUN] Would write NFO: %s", os.path.basename(nfo_path))
        return True
    if os.path.exists(nfo_path):
        log.info("NFO already exists, skipping: %s", os.path.basename(nfo_path))
        return False
    with open(nfo_path, "w", encoding="utf-8") as fh:
        fh.write(xml_content)
    log.info("Wrote NFO: %s", os.path.basename(nfo_path))
    return True


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

def collect_episodes(folder: str) -> dict:
    """Backward-compatible wrapper that returns validated episode groups only."""
    groups, _unresolved = collect_episodes_with_issues(folder)
    return groups


def collect_episodes_with_issues(folder: str):
    """Walk *folder* and return a nested dict grouping files by show/season.

    Structure::

        {
            (show_name, season_or_None): {
                episode_number: filepath,
                ...
            },
            ...
        }

    Also returns a list of unresolved files in the form:

        [(filepath, reason), ...]

    Missing season information is resolved by defaulting to season 1.
    """
    groups: dict = {}
    unresolved = []

    for root, _dirs, files in os.walk(folder):
        for fname in sorted(files):
            _, ext = os.path.splitext(fname)
            if ext.lower() not in VIDEO_EXTENSIONS:
                continue

            filepath = os.path.join(root, fname)
            stem, _ = os.path.splitext(fname)

            show_name = infer_show_name(filepath, folder)
            if not show_name:
                log.warning("Cannot determine show name for: %s", filepath)
                unresolved.append((filepath, "missing show name"))
                continue

            season = infer_season(filepath, folder)
            if season is None:
                season = 1

            episode = extract_episode_number(stem)
            if episode is None:
                log.warning("Cannot determine episode number for: %s", fname)
                unresolved.append((filepath, "missing episode number"))
                continue

            key = (show_name, season)
            groups.setdefault(key, {})[episode] = filepath

    return groups, unresolved


def write_unresolved_report(
    folder: str,
    unresolved: list,
    dry_run: bool = False,
    dry_run_folder: str = "",
):
    """Write unresolved episode files to a report and return its path.

    The report lists files that could not be processed because required
    metadata (typically episode number) could not be determined.
    """
    if not unresolved:
        return None

    report_root = dry_run_folder if (dry_run and dry_run_folder) else folder
    report_path = os.path.join(report_root, "unresolved_episode_info.txt")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)

    lines = [
        "Unresolved episode metadata report",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
    ]

    for filepath, reason in sorted(unresolved, key=lambda item: item[0].lower()):
        rel_path = os.path.relpath(filepath, folder)
        lines.append(f"{rel_path} | reason: {reason}")

    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")

    return report_path


def process_folder(
    folder: str,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
    dry_run: bool = False,
    dry_run_folder: str = "",
    request_timeout: float = 60.0,
    batch_size: int = None,
):
    """Walk *folder*, look up IMDB metadata via AI, rename files, and write NFOs."""
    dry_run_folder = (dry_run_folder or "").strip()
    groups, unresolved = collect_episodes_with_issues(folder)

    report_path = write_unresolved_report(
        folder,
        unresolved,
        dry_run=dry_run,
        dry_run_folder=dry_run_folder,
    )
    if report_path:
        log.warning(
            "Logged %d unresolved file(s) to: %s",
            len(unresolved),
            report_path,
        )

    if not groups:
        log.info("No processable video files found.")
        return

    renamed = 0
    skipped = 0
    nfos_written = 0

    for (show_name, season), episodes in sorted(groups.items()):
        episode_numbers = sorted(episodes.keys())
        season_label = f"Season {season}" if season is not None else "single-season"
        log.info(
            "Looking up IMDB metadata for %d episode(s) of '%s' (%s) …",
            len(episode_numbers),
            show_name,
            season_label,
        )

        metadata = query_episode_metadata(
            show_name,
            season,
            episode_numbers,
            api_key,
            base_url,
            model,
            request_timeout,
            batch_size,
        )

        for ep_num in episode_numbers:
            filepath = episodes[ep_num]
            directory = os.path.dirname(filepath)
            old_filename = os.path.basename(filepath)
            stem, ext = os.path.splitext(old_filename)
            existing_title = extract_episode_title(stem)

            ep_meta = metadata.get(ep_num, {})
            title = ep_meta.get("title", "")
            imdb_id = ep_meta.get("imdb_id")
            aired = ep_meta.get("aired")
            plot = ep_meta.get("plot")

            # Prefer AI metadata title, but preserve a title already present
            # in the source filename before falling back to "Episode NN".
            resolved_title = title if title else existing_title

            new_stem = build_jellyfin_name(
                show_name, season, ep_num, resolved_title, imdb_id,
            )
            new_filename = new_stem + ext

            output_directory = directory
            if dry_run and dry_run_folder:
                rel_dir = os.path.relpath(directory, folder)
                output_directory = (
                    dry_run_folder
                    if rel_dir in ("", ".")
                    else os.path.join(dry_run_folder, rel_dir)
                )

            new_filepath = os.path.join(output_directory, new_filename)

            # --- rename the video file ---
            if new_filename == old_filename and not (dry_run and dry_run_folder):
                log.info("Already compliant: %s", old_filename)
                skipped += 1
            elif dry_run:
                if dry_run_folder:
                    os.makedirs(output_directory, exist_ok=True)
                    if os.path.exists(new_filepath):
                        log.warning(
                            "Dry-run target already exists, skipping: %s  →  %s",
                            old_filename,
                            new_filename,
                        )
                        skipped += 1
                    else:
                        shutil.copy2(filepath, new_filepath)
                        log.info(
                            "[DRY RUN -> FOLDER] %s  →  %s",
                            old_filename,
                            os.path.relpath(new_filepath, dry_run_folder),
                        )
                        renamed += 1
                else:
                    log.info("[DRY RUN] %s  →  %s", old_filename, new_filename)
                    renamed += 1
            elif os.path.exists(new_filepath):
                log.warning(
                    "Target already exists, skipping: %s  →  %s",
                    old_filename,
                    new_filename,
                )
                skipped += 1
            else:
                os.rename(filepath, new_filepath)
                log.info("Renamed: %s  →  %s", old_filename, new_filename)
                renamed += 1

            # --- write the NFO sidecar ---
            # Use the title fallback consistent with the filename
            nfo_title = resolved_title if resolved_title else f"Episode {ep_num:02d}"
            nfo_xml = build_nfo_xml(
                show_name, season, ep_num, nfo_title,
                imdb_id=imdb_id, aired=aired, plot=plot,
            )
            nfo_path = os.path.join(output_directory, new_stem + ".nfo")
            if write_nfo(nfo_path, nfo_xml, dry_run=(dry_run and not dry_run_folder)):
                nfos_written += 1

    log.info(
        "Done — %d file(s) %s, %d skipped, %d NFO(s) %s, %d unresolved.",
        renamed,
        "would be renamed" if dry_run else "renamed",
        skipped,
        nfos_written,
        "would be written" if dry_run else "written",
        len(unresolved),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    load_env_file()

    media_folder = os.environ.get("MEDIA_FOLDER", "").strip()
    if not media_folder:
        log.error(
            "MEDIA_FOLDER environment variable is not set. "
            "Please set it to the path of the folder to process."
        )
        sys.exit(1)

    if not os.path.isdir(media_folder):
        log.error("MEDIA_FOLDER does not point to a directory: %s", media_folder)
        sys.exit(1)

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        log.error(
            "OPENAI_API_KEY environment variable is not set. "
            "An API key is required to look up episode titles."
        )
        sys.exit(1)

    base_url = os.environ.get(
        "OPENAI_BASE_URL", "https://api.openai.com/v1"
    ).strip()
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()
    dry_run = os.environ.get("DRY_RUN", "0").strip() == "1"
    dry_run_folder = os.environ.get("DRY_RUN_FOLDER", "").strip()
    try:
        request_timeout = float(os.environ.get("OPENAI_TIMEOUT", "60").strip())
    except ValueError:
        request_timeout = 60.0
    try:
        batch_size = int(os.environ.get("OPENAI_BATCH_SIZE", str(_BATCH_SIZE)).strip())
    except ValueError:
        batch_size = _BATCH_SIZE

    if dry_run:
        log.info("DRY RUN mode — no files will be renamed.")
        if dry_run_folder:
            log.info("DRY RUN folder enabled: %s", dry_run_folder)

    log.info("Processing folder: %s", media_folder)
    log.info("Using model: %s @ %s", model, base_url)
    log.info("AI timeout: %.1fs | batch size: %d", request_timeout, max(1, batch_size))

    process_folder(
        media_folder,
        api_key,
        base_url,
        model,
        dry_run,
        dry_run_folder,
        request_timeout,
        max(1, batch_size),
    )


if __name__ == "__main__":
    main()

