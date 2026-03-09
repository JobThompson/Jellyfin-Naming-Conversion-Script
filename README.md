# Jellyfin Naming Conversion Script

A Python script that recursively renames TV-show media files so that every
filename follows the
[Jellyfin best-practice naming scheme](https://jellyfin.org/docs/general/server/media/shows/).

This repository contains **two scripts**:

| Script | Purpose |
|--------|---------|
| `rename.py` | Renames files based on information already in the filename |
| `ai_rename.py` | Uses AI to look up **IMDB episode metadata** (titles, IDs, air dates, plots) and writes `.nfo` sidecar files |

## rename.py — Filename-based renaming

### Output format

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
python -m unittest tests/test_ai_rename.py -v
```

---

## ai_rename.py — AI-powered IMDB metadata renaming

A separate script that uses an **OpenAI-compatible language model** to look up
**IMDB episode metadata** (titles, IMDB IDs, air dates, plot summaries) and
then:

1. **Renames** each video file to the Jellyfin naming format using the official
   IMDB episode title.
2. **Writes a `.nfo` sidecar file** next to each video with IMDB metadata so
   that Jellyfin can match and display rich episode information without an
   internet lookup.

### Output format

| Show type | Output filename |
|-----------|----------------|
| Multi-season | `Show Name - S01E01 - Episode Title [tt0000000].ext` |
| Single-season | `Show Name - E01 - Episode Title [tt0000000].ext` |

When an IMDB ID is available it is appended as `[ttXXXXXXX]` for Jellyfin
matching.  When no IMDB ID is returned the tag is omitted.

A companion `.nfo` file is created for each episode containing:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<episodedetails>
  <title>Pilot</title>
  <showtitle>Breaking Bad</showtitle>
  <season>1</season>
  <episode>1</episode>
  <uniqueid type="imdb" default="true">tt0959621</uniqueid>
  <aired>2008-01-20</aired>
  <plot>A chemistry teacher is diagnosed with terminal lung cancer.</plot>
</episodedetails>
```

### Single-season / long-running shows

Shows like **Naruto** or **Yu-Gi-Oh** that use a single continuous episode
numbering with no separate seasons are supported automatically.  When no
`Season XX` folder is found, the script treats the show as single-season
and uses the `E01` format (no `S01` prefix).

### Requirements

* Python 3.7 or later — no third-party packages required.
* An **OpenAI-compatible API key** (e.g. from [OpenAI](https://platform.openai.com/)
  or a local model server).

### Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MEDIA_FOLDER` | **Yes** | — | Path to the root folder containing media files |
| `OPENAI_API_KEY` | **Yes** | — | API key for the OpenAI-compatible service |
| `OPENAI_BASE_URL` | No | `https://api.openai.com/v1` | Base URL for the API endpoint |
| `OPENAI_MODEL` | No | `gpt-4o-mini` | Model to use for metadata lookups |
| `DRY_RUN` | No | `0` | Set to `1` to preview renames without changing files |

Settings can also be placed in a `.env` file:

```dotenv
MEDIA_FOLDER=/path/to/your/tv-shows
OPENAI_API_KEY=sk-your-key-here
OPENAI_MODEL=gpt-4o-mini
DRY_RUN=1
```

### Running the script

```bash
# Rename files and write NFO metadata for real
MEDIA_FOLDER=/path/to/tv-shows OPENAI_API_KEY=sk-... python ai_rename.py

# Preview changes without touching any files (dry-run mode)
DRY_RUN=1 MEDIA_FOLDER=/path/to/tv-shows OPENAI_API_KEY=sk-... python ai_rename.py
```

### Example

Given this folder layout:

```
/media/tv/
├── Breaking Bad/
│   └── Season 01/
│       └── S01E01.mkv
└── Naruto/
    ├── 001.mkv
    └── 002.mkv
```

Running `MEDIA_FOLDER=/media/tv OPENAI_API_KEY=sk-... python ai_rename.py`
produces:

```
/media/tv/
├── Breaking Bad/
│   └── Season 01/
│       ├── Breaking Bad - S01E01 - Pilot [tt0959621].mkv
│       └── Breaking Bad - S01E01 - Pilot [tt0959621].nfo
└── Naruto/
    ├── Naruto - E01 - Enter: Naruto Uzumaki! [tt0409591].mkv
    ├── Naruto - E01 - Enter: Naruto Uzumaki! [tt0409591].nfo
    ├── Naruto - E02 - My Name is Konohamaru! [tt0409592].mkv
    └── Naruto - E02 - My Name is Konohamaru! [tt0409592].nfo
```
