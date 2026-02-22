"""Tests for rename.py"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

# Adjust path so we can import rename.py from the repo root
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rename import (
    parse_filename,
    build_jellyfin_name,
    rename_file,
    process_folder,
    load_env_file,
)


class TestParseFilename(unittest.TestCase):
    """Unit tests for parse_filename()."""

    # ------------------------------------------------------------------
    # Standard S01E01 patterns
    # ------------------------------------------------------------------
    def test_standard_s01e01(self):
        result = parse_filename("Show.Name.S01E01.Episode.Title")
        self.assertIsNotNone(result)
        show, season, episode, title = result
        self.assertEqual(show, "Show Name")
        self.assertEqual(season, 1)
        self.assertEqual(episode, 1)
        self.assertEqual(title, "Episode Title")

    def test_standard_s01e01_dashes(self):
        result = parse_filename("Show Name - S02E05 - Some Title")
        self.assertIsNotNone(result)
        show, season, episode, title = result
        self.assertEqual(show, "Show Name")
        self.assertEqual(season, 2)
        self.assertEqual(episode, 5)
        self.assertEqual(title, "Some Title")

    def test_standard_s01e01_no_title(self):
        result = parse_filename("Show.Name.S03E12")
        self.assertIsNotNone(result)
        show, season, episode, title = result
        self.assertEqual(show, "Show Name")
        self.assertEqual(season, 3)
        self.assertEqual(episode, 12)
        self.assertEqual(title, "")

    def test_standard_s01e01_lowercase(self):
        result = parse_filename("show_name_s01e01_ep_name")
        self.assertIsNotNone(result)
        _, season, episode, _ = result
        self.assertEqual(season, 1)
        self.assertEqual(episode, 1)

    # ------------------------------------------------------------------
    # Alternate 1x01 patterns
    # ------------------------------------------------------------------
    def test_alternate_1x01(self):
        result = parse_filename("Show Name - 2x04 - Episode Title")
        self.assertIsNotNone(result)
        show, season, episode, title = result
        self.assertEqual(show, "Show Name")
        self.assertEqual(season, 2)
        self.assertEqual(episode, 4)
        self.assertEqual(title, "Episode Title")

    # ------------------------------------------------------------------
    # Three-digit episode numbering
    # ------------------------------------------------------------------
    def test_three_digit_episode_not_interpreted_as_season_split(self):
        result = parse_filename("Anime.Show.101.Episode.Title")
        self.assertIsNotNone(result)
        _, season, episode, _ = result
        self.assertIsNone(season)
        self.assertEqual(episode, 101)

    def test_three_digit_episode_212(self):
        result = parse_filename("Anime.Show.212")
        self.assertIsNotNone(result)
        _, season, episode, _ = result
        self.assertIsNone(season)
        self.assertEqual(episode, 212)

    # ------------------------------------------------------------------
    # Episode keyword patterns
    # ------------------------------------------------------------------
    def test_episode_keyword_ep(self):
        result = parse_filename("Show Name Ep03 Title")
        self.assertIsNotNone(result)
        _, season, episode, _ = result
        self.assertIsNone(season)
        self.assertEqual(episode, 3)

    def test_episode_keyword_episode(self):
        result = parse_filename("Show Name Episode 7 Some Title")
        self.assertIsNotNone(result)
        _, season, episode, _ = result
        self.assertEqual(episode, 7)

    # ------------------------------------------------------------------
    # Plain number patterns
    # ------------------------------------------------------------------
    def test_plain_number(self):
        result = parse_filename("01")
        self.assertIsNotNone(result)
        _, season, episode, _ = result
        self.assertIsNone(season)
        self.assertEqual(episode, 1)

    def test_plain_number_with_title(self):
        result = parse_filename("05 - Some Episode Title")
        self.assertIsNotNone(result)
        _, _, episode, title = result
        self.assertEqual(episode, 5)
        self.assertEqual(title, "Some Episode Title")

    # ------------------------------------------------------------------
    # Unrecognised input
    # ------------------------------------------------------------------
    def test_unrecognisable_returns_none(self):
        result = parse_filename("no_episode_info_here")
        self.assertIsNone(result)


class TestBuildJellyfinName(unittest.TestCase):
    """Unit tests for build_jellyfin_name()."""

    def test_multi_season_with_title(self):
        name = build_jellyfin_name("Breaking Bad", 1, 1, "Pilot")
        self.assertEqual(name, "Breaking Bad - S01E01 - Pilot")

    def test_multi_season_no_title_uses_fallback(self):
        name = build_jellyfin_name("Breaking Bad", 2, 5, "")
        self.assertEqual(name, "Breaking Bad - S02E05 - Episode 05")

    def test_single_season_with_title(self):
        name = build_jellyfin_name("Chernobyl", None, 3, "Open Wide O Earth")
        self.assertEqual(name, "Chernobyl - E03 - Open Wide O Earth")

    def test_single_season_no_title_uses_fallback(self):
        name = build_jellyfin_name("Chernobyl", None, 2, "")
        self.assertEqual(name, "Chernobyl - E02 - Episode 02")

    def test_no_show_name(self):
        name = build_jellyfin_name("", 1, 4, "Some Title")
        self.assertEqual(name, "S01E04 - Some Title")

    def test_zero_padded_numbers(self):
        name = build_jellyfin_name("Show", 3, 9, "Title")
        self.assertEqual(name, "Show - S03E09 - Title")


class TestRenameFile(unittest.TestCase):
    """Integration tests for rename_file() using a temporary directory."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _make_file(self, *path_parts):
        path = os.path.join(self.tmp, *path_parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w"):
            pass
        return path

    def test_renames_standard_pattern(self):
        fp = self._make_file("Show.Name.S01E01.Episode.Title.mkv")
        result = rename_file(fp, self.tmp)
        self.assertTrue(result)
        expected = os.path.join(self.tmp, "Show Name - S01E01 - Episode Title.mkv")
        self.assertTrue(os.path.exists(expected))

    def test_dry_run_does_not_rename(self):
        fp = self._make_file("Show.Name.S01E02.Some.Title.mkv")
        result = rename_file(fp, self.tmp, dry_run=True)
        self.assertTrue(result)
        # Original file must still exist
        self.assertTrue(os.path.exists(fp))
        # New file must NOT exist
        expected = os.path.join(self.tmp, "Show Name - S01E02 - Some Title.mkv")
        self.assertFalse(os.path.exists(expected))

    def test_already_compliant_returns_false(self):
        fp = self._make_file("Show Name - S01E01 - Pilot.mkv")
        result = rename_file(fp, self.tmp)
        self.assertFalse(result)

    def test_infers_show_name_from_folder(self):
        fp = self._make_file("Breaking Bad", "Season 01", "S01E01.Pilot.mkv")
        result = rename_file(fp, self.tmp)
        self.assertTrue(result)
        expected = os.path.join(
            self.tmp,
            "Breaking Bad",
            "Season 01",
            "Breaking Bad - S01E01 - Pilot.mkv",
        )
        self.assertTrue(os.path.exists(expected))

    def test_no_episode_title_uses_fallback(self):
        fp = self._make_file("Show.Name.S02E03.mkv")
        rename_file(fp, self.tmp)
        expected = os.path.join(self.tmp, "Show Name - S02E03 - Episode 03.mkv")
        self.assertTrue(os.path.exists(expected))

    def test_does_not_overwrite_existing_file(self):
        fp = self._make_file("Show.Name.S01E01.Pilot.mkv")
        # Create the target file already
        target = os.path.join(self.tmp, "Show Name - S01E01 - Pilot.mkv")
        with open(target, "w"):
            pass
        result = rename_file(fp, self.tmp)
        self.assertFalse(result)
        # Original must still exist
        self.assertTrue(os.path.exists(fp))

    def test_non_video_file_is_ignored(self):
        fp = self._make_file("Show.Name.S01E01.Pilot.txt")
        fp2 = self._make_file("Show.Name.S01E02.Pilot.mkv")
        process_folder(self.tmp)
        # .txt file should be untouched (not renamed)
        self.assertTrue(os.path.exists(fp))


class TestProcessFolder(unittest.TestCase):
    """End-to-end tests for process_folder()."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _make_file(self, *path_parts):
        path = os.path.join(self.tmp, *path_parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w"):
            pass
        return path

    def test_renames_files_in_nested_folders(self):
        self._make_file("Breaking Bad", "Season 01", "breaking.bad.S01E01.Pilot.mkv")
        self._make_file("Breaking Bad", "Season 02", "breaking.bad.S02E01.Seven.Thirty-Seven.mkv")
        process_folder(self.tmp)
        self.assertTrue(
            os.path.exists(
                os.path.join(
                    self.tmp,
                    "Breaking Bad",
                    "Season 01",
                    "Breaking Bad - S01E01 - Pilot.mkv",
                )
            )
        )
        self.assertTrue(
            os.path.exists(
                os.path.join(
                    self.tmp,
                    "Breaking Bad",
                    "Season 02",
                    "Breaking Bad - S02E01 - Seven Thirty-Seven.mkv",
                )
            )
        )

    def test_single_season_show(self):
        self._make_file("Chernobyl", "Chernobyl.Ep01.Rbmk-1000.mkv")
        process_folder(self.tmp)
        self.assertTrue(
            os.path.exists(
                os.path.join(
                    self.tmp,
                    "Chernobyl",
                    "Chernobyl - E01 - Rbmk-1000.mkv",
                )
            )
        )

    def test_dry_run_leaves_files_unchanged(self):
        fp = self._make_file("Show.S01E05.Title.mp4")
        process_folder(self.tmp, dry_run=True)
        self.assertTrue(os.path.exists(fp))
        expected = os.path.join(self.tmp, "Show - S01E05 - Title.mp4")
        self.assertFalse(os.path.exists(expected))

    def test_high_episode_number_inherits_folder_season(self):
        self._make_file("Show", "Season 01", "Show.S01E01.Pilot.mkv")
        self._make_file("Show", "Season 01", "Show.101.The.Long.Road.mkv")
        process_folder(self.tmp)
        self.assertTrue(
            os.path.exists(
                os.path.join(
                    self.tmp,
                    "Show",
                    "Season 01",
                    "Show - S01E01 - Pilot.mkv",
                )
            )
        )
        self.assertTrue(
            os.path.exists(
                os.path.join(
                    self.tmp,
                    "Show",
                    "Season 01",
                    "Show - S01E101 - The Long Road.mkv",
                )
            )
        )


class TestLoadEnvFile(unittest.TestCase):
    """Unit tests for load_env_file()."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env_path = os.path.join(self.tmp, ".env")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_loads_values_from_dotenv(self):
        with open(self.env_path, "w", encoding="utf-8") as handle:
            handle.write(
                "# comment\n"
                "MEDIA_FOLDER= C:/Media/TV \n"
                "DRY_RUN='1'\n"
                "export EXTRA_VAR=extra\n"
            )

        with mock.patch.dict(os.environ, {}, clear=True):
            loaded = load_env_file(self.env_path)
            self.assertEqual(loaded, 3)
            self.assertEqual(os.environ.get("MEDIA_FOLDER"), "C:/Media/TV")
            self.assertEqual(os.environ.get("DRY_RUN"), "1")
            self.assertEqual(os.environ.get("EXTRA_VAR"), "extra")

    def test_does_not_override_existing_by_default(self):
        with open(self.env_path, "w", encoding="utf-8") as handle:
            handle.write("DRY_RUN=1\n")

        with mock.patch.dict(os.environ, {"DRY_RUN": "0"}, clear=True):
            loaded = load_env_file(self.env_path)
            self.assertEqual(loaded, 0)
            self.assertEqual(os.environ.get("DRY_RUN"), "0")

    def test_override_replaces_existing_value(self):
        with open(self.env_path, "w", encoding="utf-8") as handle:
            handle.write("DRY_RUN=1\n")

        with mock.patch.dict(os.environ, {"DRY_RUN": "0"}, clear=True):
            loaded = load_env_file(self.env_path, override=True)
            self.assertEqual(loaded, 1)
            self.assertEqual(os.environ.get("DRY_RUN"), "1")


if __name__ == "__main__":
    unittest.main()
