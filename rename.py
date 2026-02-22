#!/usr/bin/env python3
"""Jellyfin Naming Conversion Script

Renames TV-show media files inside a folder tree so that every filename
follows the Jellyfin best-practice naming scheme:

  Single-season show  →  Show Name - E01 - Episode Title.ext
  Multi-season show   →  Show Name - S01E01 - Episode Title.ext

When no episode title can be found in the original filename the episode
number is used as the title (e.g. "Episode 01").

Configuration is done through environment variables:
  MEDIA_FOLDER   Path to the root folder that contains the media files.
                 (required)
  DRY_RUN        Set to "1" to print what would be renamed without
                 actually renaming anything.  Default: 0 (off).
"""

import logging
import os
import re
import sys

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
# Regex patterns tried in order to locate the season/episode marker inside
# a filename stem.  Each pattern must expose named groups:
#   season   – season number (optional; absent → single-season show)
#   episode  – first / only episode number
#   ep2      – second episode number for multi-episode files (optional)
# ---------------------------------------------------------------------------
_EP_PATTERNS = [
    # Standard:  S01E01  /  S1E1  /  s01e01e02  /  S01E01-E02
    re.compile(
        r"[Ss](?P<season>\d{1,2})[Ee](?P<episode>\d{1,3})"
        r"(?:[-_]?[Ee](?P<ep2>\d{1,3}))?",
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
    # Plain number anywhere in stem:  "1" / "01" / "001"
    re.compile(
        r"\b(?P<episode>\d{1,3})\b",
    ),
]

# Replace dots, underscores, and whitespace runs with a single space.
# Hyphens are handled separately so that compound words like "Thirty-Seven"
# are preserved while edge/isolated hyphens are removed.
_DOT_UNDER_RE = re.compile(r"[._\s]+")


def load_env_file(file_path: str = ".env", override: bool = False) -> int:
    """Load environment variables from a .env-style file.

    Lines must be in ``KEY=VALUE`` form. Empty lines and comments are ignored.
    If *override* is ``False`` (default), existing environment variables are
    preserved.

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
                    line = line[len("export ") :].strip()

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


def _clean(text: str) -> str:
    """Normalise separators in a filename fragment.

    * Dots, underscores, and whitespace are converted to single spaces.
    * Hyphens that are surrounded by spaces or sit at the edges (i.e. used as
      field separators) are removed; hyphens that sit between non-space
      characters (e.g. "Thirty-Seven") are preserved.
    """
    text = _DOT_UNDER_RE.sub(" ", text)
    # Remove hyphens adjacent to spaces or at start/end
    text = re.sub(r"(?:^|\s)-+|-+(?:$|\s)", " ", text)
    # Collapse multiple spaces and strip
    return re.sub(r" {2,}", " ", text).strip()


def parse_filename(stem: str):
    """Try to decompose *stem* (no extension) into its components.

    Returns a tuple ``(show_name, season, episode, episode_title)`` where
    *season* is ``None`` for single-season shows and *episode_title* may be
    an empty string when it cannot be determined.
    """
    for pattern in _EP_PATTERNS:
        m = pattern.search(stem)
        if m is None:
            continue

        episode = int(m.group("episode"))

        # Season number – may not be present in the pattern
        try:
            season_str = m.group("season")
            season = int(season_str) if season_str else None
        except IndexError:
            season = None

        # Everything before the match  →  show name
        before = stem[: m.start()]
        show_name = _clean(before)

        # Everything after the match  →  episode title
        after = stem[m.end() :]
        episode_title = _clean(after)

        # If the "before" portion is empty the filename probably starts
        # directly with the episode marker (e.g. "S01E01 Title").  In that
        # case we cannot recover the show name from the filename alone.
        return show_name, season, episode, episode_title

    return None


def build_jellyfin_name(show_name: str, season, episode: int, episode_title: str) -> str:
    """Construct the Jellyfin-compliant filename stem."""
    # Episode title fallback
    if not episode_title:
        episode_title = f"Episode {episode:02d}"

    if season is not None:
        ep_marker = f"S{season:02d}E{episode:02d}"
    else:
        ep_marker = f"E{episode:02d}"

    if show_name:
        return f"{show_name} - {ep_marker} - {episode_title}"
    return f"{ep_marker} - {episode_title}"


def _infer_show_name_from_path(filepath: str, base_folder: str) -> str:
    """Walk up the directory tree to find a plausible show-name folder.

    The folder immediately under *base_folder* is treated as the show name.
    """
    rel = os.path.relpath(filepath, base_folder)
    parts = rel.split(os.sep)
    if len(parts) > 1:
        # parts[0] is the top-level folder inside base_folder
        return _clean(parts[0])
    return ""


def rename_file(
    filepath: str,
    base_folder: str,
    dry_run: bool = False,
    default_season=None,
) -> bool:
    """Attempt to rename *filepath* to a Jellyfin-compliant name.

    Returns ``True`` if a rename was performed (or would be in dry-run mode).
    """
    directory = os.path.dirname(filepath)
    filename = os.path.basename(filepath)
    stem, ext = os.path.splitext(filename)

    parsed = parse_filename(stem)
    if parsed is None:
        log.warning("Could not parse episode info from: %s", filename)
        return False

    show_name, season, episode, episode_title = parsed

    if season is None and default_season is not None:
        season = default_season

    # The folder name is the most reliable source for the show name; use it
    # whenever the file lives inside a named subdirectory of the base folder.
    folder_show_name = _infer_show_name_from_path(filepath, base_folder)
    if folder_show_name:
        show_name = folder_show_name
    elif not show_name:
        show_name = ""

    new_stem = build_jellyfin_name(show_name, season, episode, episode_title)
    new_filename = new_stem + ext
    new_filepath = os.path.join(directory, new_filename)

    if new_filename == filename:
        log.info("Already compliant: %s", filename)
        return False

    if dry_run:
        log.info("[DRY RUN] %s  →  %s", filename, new_filename)
        return True

    # Guard against clobbering an existing file
    if os.path.exists(new_filepath):
        log.warning(
            "Target already exists, skipping: %s  →  %s",
            filename,
            new_filename,
        )
        return False

    os.rename(filepath, new_filepath)
    log.info("Renamed: %s  →  %s", filename, new_filename)
    return True


def process_folder(folder: str, dry_run: bool = False):
    """Walk *folder* recursively and rename every recognised video file."""
    renamed = 0
    skipped = 0

    for root, _dirs, files in os.walk(folder):
        inferred_season = None

        # Infer a season for this directory from files that explicitly
        # include one (e.g. S01E01, 2x04). If exactly one distinct season is
        # found, use it as fallback for seasonless files in the same folder.
        seasons_found = set()
        for fname in files:
            _, ext = os.path.splitext(fname)
            if ext.lower() not in VIDEO_EXTENSIONS:
                continue

            stem, _ = os.path.splitext(fname)
            parsed = parse_filename(stem)
            if parsed is None:
                continue

            _, season, _episode, _title = parsed
            if season is not None:
                seasons_found.add(season)

        if len(seasons_found) == 1:
            inferred_season = next(iter(seasons_found))

        for fname in sorted(files):
            _, ext = os.path.splitext(fname)
            if ext.lower() not in VIDEO_EXTENSIONS:
                continue

            filepath = os.path.join(root, fname)
            result = rename_file(
                filepath,
                folder,
                dry_run=dry_run,
                default_season=inferred_season,
            )
            if result:
                renamed += 1
            else:
                skipped += 1

    log.info(
        "Done — %d file(s) %s, %d skipped.",
        renamed,
        "would be renamed" if dry_run else "renamed",
        skipped,
    )


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

    dry_run = os.environ.get("DRY_RUN", "0").strip() == "1"

    if dry_run:
        log.info("DRY RUN mode — no files will be renamed.")

    log.info("Processing folder: %s", media_folder)
    process_folder(media_folder, dry_run=dry_run)


if __name__ == "__main__":
    main()
