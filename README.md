# Jellyfin Naming Conversion Script

A Python script that recursively renames TV-show media files so that every
filename follows the
[Jellyfin best-practice naming scheme](https://jellyfin.org/docs/general/server/media/shows/).

## Output format

| Show type | Output filename |
|-----------|----------------|
| Multi-season | `Show Name - S01E01 - Episode Title.ext` |
| Single-season | `Show Name - E01 - Episode Title.ext` |

When no episode title can be determined from the original filename the
episode number is used as a fallback title (e.g. `Episode 01`).

## Supported input patterns

The script recognises many common filename conventions automatically:

| Pattern | Example |
|---------|---------|
| Standard `SxxExx` | `Show.Name.S01E01.Pilot.mkv` |
| Alternate `NxNN` | `Show Name - 2x04 - Episode Title.mp4` |
| Three-digit episode number | `Show.Name.101.Episode.Title.mkv` |
| Episode keyword | `Show Name Ep03 Title.mkv` / `Episode 7` |
| Bare episode number | `05 - Title.mkv` / `01.mkv` |

Separators between fields (`.` `_` `-` spaces) are all handled gracefully.
Compound-word hyphens inside titles (e.g. `Thirty-Seven`, `RBMK-1000`) are
preserved.

The show name is taken from the containing folder name when the file lives
inside a named subdirectory (the most reliable source); otherwise it is
extracted from the filename itself.

## Usage

### Requirements

* Python 3.7 or later — no third-party packages required.

### Running the script

```bash
# Rename files for real
MEDIA_FOLDER=/path/to/your/tv-shows python rename.py

# Preview changes without touching any files (dry-run mode)
DRY_RUN=1 MEDIA_FOLDER=/path/to/your/tv-shows python rename.py
```

### Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MEDIA_FOLDER` | **Yes** | — | Path to the root folder containing media files |
| `DRY_RUN` | No | `0` | Set to `1` to preview renames without changing files |

The script automatically reads a local `.env` file (if present) before
checking environment variables, so you can place settings there:

```dotenv
MEDIA_FOLDER=C:/path/to/your/tv-shows
DRY_RUN=1
```

### Example

Given this folder layout:

```
/media/tv/
├── Breaking Bad/
│   ├── Season 01/
│   │   └── breaking.bad.S01E01.Pilot.mkv
│   └── Season 02/
│       └── breaking.bad.S02E01.Seven.Thirty-Seven.mkv
└── Chernobyl/
    └── Chernobyl.Ep01.RBMK-1000.mkv
```

Running `MEDIA_FOLDER=/media/tv python rename.py` produces:

```
/media/tv/
├── Breaking Bad/
│   ├── Season 01/
│   │   └── Breaking Bad - S01E01 - Pilot.mkv
│   └── Season 02/
│       └── Breaking Bad - S02E01 - Seven Thirty-Seven.mkv
└── Chernobyl/
    └── Chernobyl - E01 - RBMK-1000.mkv
```

## Running the tests

```bash
python -m unittest tests/test_rename.py -v
```
