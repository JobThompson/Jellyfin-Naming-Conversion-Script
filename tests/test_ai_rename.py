"""Tests for ai_rename.py"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock
from xml.etree import ElementTree

# Adjust path so we can import ai_rename.py from the repo root
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_rename import (
    extract_episode_number,
    extract_episode_title,
    parse_season_from_folder,
    infer_show_name,
    infer_season,
    build_jellyfin_name,
    build_nfo_xml,
    write_nfo,
    collect_episodes,
    process_folder,
    load_env_file,
    _build_prompt,
    _call_ai,
    query_episode_metadata,
)


class TestExtractEpisodeNumber(unittest.TestCase):
    """Unit tests for extract_episode_number()."""

    def test_standard_s01e05(self):
        self.assertEqual(extract_episode_number("Show.Name.S01E05.Title"), 5)

    def test_alternate_2x04(self):
        self.assertEqual(extract_episode_number("Show Name - 2x04 - Title"), 4)

    def test_episode_keyword(self):
        self.assertEqual(extract_episode_number("Show Ep03 Title"), 3)

    def test_plain_number(self):
        self.assertEqual(extract_episode_number("001"), 1)

    def test_three_digit_number(self):
        self.assertEqual(extract_episode_number("Naruto 220"), 220)

    def test_no_episode(self):
        self.assertIsNone(extract_episode_number("no_episode_here"))


class TestExtractEpisodeTitle(unittest.TestCase):
    """Unit tests for extract_episode_title()."""

    def test_extracts_title_after_numbered_episode(self):
        stem = "Yu-Gi-Oh! 5D's - 001 - On Your Mark, Get Set, Duel!"
        self.assertEqual(extract_episode_title(stem), "On Your Mark, Get Set, Duel!")

    def test_no_title_fragment(self):
        self.assertEqual(extract_episode_title("S01E05"), "")


class TestParseSeasonFromFolder(unittest.TestCase):
    """Unit tests for parse_season_from_folder()."""

    def test_season_01(self):
        self.assertEqual(parse_season_from_folder("Season 01"), 1)

    def test_season_2(self):
        self.assertEqual(parse_season_from_folder("Season 2"), 2)

    def test_s03(self):
        self.assertEqual(parse_season_from_folder("S03"), 3)

    def test_season10(self):
        self.assertEqual(parse_season_from_folder("Season10"), 10)

    def test_no_season(self):
        self.assertIsNone(parse_season_from_folder("Specials"))

    def test_no_season_plain_name(self):
        self.assertIsNone(parse_season_from_folder("Breaking Bad"))


class TestInferShowName(unittest.TestCase):
    """Unit tests for infer_show_name()."""

    def test_nested_path(self):
        base = "/media/tv"
        fp = "/media/tv/Breaking Bad/Season 01/file.mkv"
        self.assertEqual(infer_show_name(fp, base), "Breaking Bad")

    def test_direct_child(self):
        base = "/media/tv"
        fp = "/media/tv/file.mkv"
        self.assertEqual(infer_show_name(fp, base), "tv")

    def test_show_folder_only(self):
        base = "/media/tv"
        fp = "/media/tv/Naruto/001.mkv"
        self.assertEqual(infer_show_name(fp, base), "Naruto")

    def test_base_folder_as_show_folder(self):
        base = "/media/tv/YuGiOh 5Ds"
        fp = "/media/tv/YuGiOh 5Ds/001.mkv"
        self.assertEqual(infer_show_name(fp, base), "YuGiOh 5Ds")


class TestInferSeason(unittest.TestCase):
    """Unit tests for infer_season()."""

    def test_season_folder(self):
        base = "/media/tv"
        fp = "/media/tv/Show/Season 02/file.mkv"
        self.assertEqual(infer_season(fp, base), 2)

    def test_no_season_folder(self):
        base = "/media/tv"
        fp = "/media/tv/Naruto/file.mkv"
        self.assertIsNone(infer_season(fp, base))

    def test_s_prefix_folder(self):
        base = "/media/tv"
        fp = "/media/tv/Show/S03/file.mkv"
        self.assertEqual(infer_season(fp, base), 3)

    def test_show_root_with_season_folder(self):
        base = "/media/tv/Show"
        fp = "/media/tv/Show/Season 02/file.mkv"
        self.assertEqual(infer_season(fp, base), 2)


class TestBuildJellyfinName(unittest.TestCase):
    """Unit tests for build_jellyfin_name()."""

    def test_multi_season_with_title(self):
        name = build_jellyfin_name("Breaking Bad", 1, 1, "Pilot")
        self.assertEqual(name, "Breaking Bad - S01E01 - Pilot")

    def test_multi_season_with_imdb_id(self):
        name = build_jellyfin_name("Breaking Bad", 1, 1, "Pilot", imdb_id="tt0959621")
        self.assertEqual(name, "Breaking Bad - S01E01 - Pilot [tt0959621]")

    def test_single_season_with_title(self):
        name = build_jellyfin_name("Naruto", None, 1, "Enter: Naruto Uzumaki!")
        self.assertEqual(name, "Naruto - E01 - Enter Naruto Uzumaki!")

    def test_no_title_fallback(self):
        name = build_jellyfin_name("Show", 2, 5, "")
        self.assertEqual(name, "Show - S02E05 - Episode 05")

    def test_single_season_no_title(self):
        name = build_jellyfin_name("Naruto", None, 220, "")
        self.assertEqual(name, "Naruto - E220 - Episode 220")

    def test_no_imdb_id_no_tag(self):
        name = build_jellyfin_name("Show", 1, 1, "Pilot", imdb_id=None)
        self.assertNotIn("[", name)


class TestBuildNfoXml(unittest.TestCase):
    """Unit tests for build_nfo_xml()."""

    def test_full_metadata(self):
        xml = build_nfo_xml(
            "Breaking Bad", 1, 1, "Pilot",
            imdb_id="tt0959621",
            aired="2008-01-20",
            plot="A chemistry teacher is diagnosed with cancer.",
        )
        root = ElementTree.fromstring(xml)
        self.assertEqual(root.tag, "episodedetails")
        self.assertEqual(root.find("title").text, "Pilot")
        self.assertEqual(root.find("showtitle").text, "Breaking Bad")
        self.assertEqual(root.find("season").text, "1")
        self.assertEqual(root.find("episode").text, "1")
        uid = root.find("uniqueid")
        self.assertEqual(uid.text, "tt0959621")
        self.assertEqual(uid.get("type"), "imdb")
        self.assertEqual(root.find("aired").text, "2008-01-20")
        self.assertIn("cancer", root.find("plot").text)

    def test_minimal_metadata(self):
        xml = build_nfo_xml("Show", None, 5, "Episode 05")
        root = ElementTree.fromstring(xml)
        self.assertEqual(root.find("title").text, "Episode 05")
        self.assertIsNone(root.find("season"))
        self.assertIsNone(root.find("uniqueid"))
        self.assertIsNone(root.find("aired"))
        self.assertIsNone(root.find("plot"))

    def test_special_characters_escaped(self):
        xml = build_nfo_xml("Tom & Jerry", 1, 1, "Cat <&> Mouse")
        root = ElementTree.fromstring(xml)
        self.assertEqual(root.find("title").text, "Cat <&> Mouse")
        self.assertEqual(root.find("showtitle").text, "Tom & Jerry")


class TestWriteNfo(unittest.TestCase):
    """Unit tests for write_nfo()."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_writes_nfo(self):
        path = os.path.join(self.tmp, "test.nfo")
        result = write_nfo(path, "<episodedetails></episodedetails>\n")
        self.assertTrue(result)
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as fh:
            self.assertIn("episodedetails", fh.read())

    def test_dry_run_does_not_write(self):
        path = os.path.join(self.tmp, "test.nfo")
        result = write_nfo(path, "<episodedetails/>", dry_run=True)
        self.assertTrue(result)
        self.assertFalse(os.path.exists(path))

    def test_skips_existing_nfo(self):
        path = os.path.join(self.tmp, "test.nfo")
        with open(path, "w") as fh:
            fh.write("existing")
        result = write_nfo(path, "<new/>")
        self.assertFalse(result)
        with open(path, "r") as fh:
            self.assertEqual(fh.read(), "existing")


class TestBuildPrompt(unittest.TestCase):
    """Unit tests for _build_prompt()."""

    def test_multi_season_prompt(self):
        prompt = _build_prompt("Breaking Bad", 1, [1, 2, 3])
        self.assertIn("Breaking Bad", prompt)
        self.assertIn("Season 1", prompt)
        self.assertIn("1, 2, 3", prompt)
        self.assertIn("IMDB", prompt)
        self.assertIn("imdb_id", prompt)

    def test_single_season_prompt(self):
        prompt = _build_prompt("Naruto", None, [1, 2])
        self.assertIn("Naruto", prompt)
        self.assertIn("single continuous episode numbering", prompt)
        self.assertIn("1, 2", prompt)

    def test_episodes_sorted(self):
        prompt = _build_prompt("Show", 1, [5, 1, 3])
        self.assertIn("1, 3, 5", prompt)


class TestCallAI(unittest.TestCase):
    """Unit tests for _call_ai() with mocked HTTP responses."""

    _BASE_URL = "https://api.openai.com/v1"
    _MODEL = "gpt-4o-mini"
    _KEY = "fake-key"

    def _mock_response(self, content_text):
        """Create a mock urllib response."""
        body = json.dumps({
            "choices": [{
                "message": {"content": content_text}
            }]
        }).encode("utf-8")

        resp = mock.MagicMock()
        resp.read.return_value = body
        resp.__enter__ = mock.MagicMock(return_value=resp)
        resp.__exit__ = mock.MagicMock(return_value=False)
        return resp

    @mock.patch("ai_rename.urllib.request.urlopen")
    def test_valid_metadata_response(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response(
            json.dumps({
                "1": {
                    "title": "Pilot",
                    "imdb_id": "tt0959621",
                    "aired": "2008-01-20",
                    "plot": "A chemistry teacher gets a diagnosis.",
                }
            })
        )
        result = _call_ai("test", self._KEY, self._BASE_URL, self._MODEL)
        self.assertEqual(result[1]["title"], "Pilot")
        self.assertEqual(result[1]["imdb_id"], "tt0959621")
        self.assertEqual(result[1]["aired"], "2008-01-20")
        self.assertIn("diagnosis", result[1]["plot"])

    @mock.patch("ai_rename.urllib.request.urlopen")
    def test_fallback_plain_string_value(self, mock_urlopen):
        """Graceful fallback when AI returns simple {ep: title} format."""
        mock_urlopen.return_value = self._mock_response(
            '{"1": "Pilot"}'
        )
        result = _call_ai("test", self._KEY, self._BASE_URL, self._MODEL)
        self.assertEqual(result[1]["title"], "Pilot")
        self.assertIsNone(result[1]["imdb_id"])

    @mock.patch("ai_rename.urllib.request.urlopen")
    def test_json_with_code_fences(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response(
            '```json\n{"1": {"title": "Pilot", "imdb_id": null, '
            '"aired": null, "plot": null}}\n```'
        )
        result = _call_ai("test", self._KEY, self._BASE_URL, self._MODEL)
        self.assertEqual(result[1]["title"], "Pilot")

    @mock.patch("ai_rename.urllib.request.urlopen")
    def test_invalid_json_returns_empty(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response("not json at all")
        result = _call_ai("test", self._KEY, self._BASE_URL, self._MODEL)
        self.assertEqual(result, {})

    @mock.patch("ai_rename.urllib.request.urlopen")
    def test_http_error_returns_empty(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "url", 401, "Unauthorized", {}, None,
        )
        result = _call_ai("test", self._KEY, self._BASE_URL, self._MODEL)
        self.assertEqual(result, {})

    @mock.patch("ai_rename.time.sleep", return_value=None)
    @mock.patch("ai_rename.urllib.request.urlopen")
    def test_retries_once_on_429_then_succeeds(self, mock_urlopen, _mock_sleep):
        import urllib.error

        first = urllib.error.HTTPError("url", 429, "Too Many Requests", {}, None)
        second = self._mock_response(
            json.dumps({
                "1": {
                    "title": "Pilot",
                    "imdb_id": "tt0959621",
                    "aired": "2008-01-20",
                    "plot": "A chemistry teacher gets a diagnosis.",
                }
            })
        )
        mock_urlopen.side_effect = [first, second]

        result = _call_ai("test", self._KEY, self._BASE_URL, self._MODEL)
        self.assertEqual(result[1]["title"], "Pilot")
        self.assertEqual(mock_urlopen.call_count, 2)

    @mock.patch("ai_rename.time.sleep", return_value=None)
    @mock.patch("ai_rename.urllib.request.urlopen")
    def test_retries_once_on_timeout_then_succeeds(self, mock_urlopen, _mock_sleep):
        first = TimeoutError("The read operation timed out")
        second = self._mock_response(
            json.dumps({
                "1": {
                    "title": "Pilot",
                    "imdb_id": "tt0959621",
                    "aired": "2008-01-20",
                    "plot": "A chemistry teacher gets a diagnosis.",
                }
            })
        )
        mock_urlopen.side_effect = [first, second]

        result = _call_ai("test", self._KEY, self._BASE_URL, self._MODEL)
        self.assertEqual(result[1]["title"], "Pilot")
        self.assertEqual(mock_urlopen.call_count, 2)


class TestQueryEpisodeMetadata(unittest.TestCase):
    """Unit tests for query_episode_metadata() with mocked _call_ai."""

    _BASE_URL = "https://api.openai.com/v1"
    _MODEL = "gpt-4o-mini"
    _KEY = "fake-key"

    @mock.patch("ai_rename._call_ai")
    def test_single_batch(self, mock_call):
        mock_call.return_value = {
            1: {"title": "Pilot", "imdb_id": "tt1", "aired": None, "plot": None},
        }
        result = query_episode_metadata(
            "Show", 1, [1], self._KEY, self._BASE_URL, self._MODEL,
        )
        self.assertEqual(result[1]["title"], "Pilot")
        mock_call.assert_called_once()

    @mock.patch("ai_rename._BATCH_SIZE", 2)
    @mock.patch("ai_rename._call_ai")
    def test_multiple_batches(self, mock_call):
        mock_call.side_effect = [
            {
                1: {"title": "First", "imdb_id": None, "aired": None, "plot": None},
                2: {"title": "Second", "imdb_id": None, "aired": None, "plot": None},
            },
            {
                3: {"title": "Third", "imdb_id": None, "aired": None, "plot": None},
            },
        ]
        result = query_episode_metadata(
            "Show", None, [1, 2, 3], self._KEY, self._BASE_URL, self._MODEL,
        )
        self.assertEqual(len(result), 3)
        self.assertEqual(result[3]["title"], "Third")
        self.assertEqual(mock_call.call_count, 2)


class TestCollectEpisodes(unittest.TestCase):
    """Unit tests for collect_episodes()."""

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

    def test_multi_season_show(self):
        self._make_file("Breaking Bad", "Season 01", "S01E01.mkv")
        self._make_file("Breaking Bad", "Season 01", "S01E02.mkv")
        self._make_file("Breaking Bad", "Season 02", "S02E01.mkv")
        groups = collect_episodes(self.tmp)

        self.assertIn(("Breaking Bad", 1), groups)
        self.assertIn(("Breaking Bad", 2), groups)
        self.assertEqual(sorted(groups[("Breaking Bad", 1)].keys()), [1, 2])
        self.assertEqual(sorted(groups[("Breaking Bad", 2)].keys()), [1])

    def test_single_season_show(self):
        self._make_file("Naruto", "001.mkv")
        self._make_file("Naruto", "002.mkv")
        self._make_file("Naruto", "003.mkv")
        groups = collect_episodes(self.tmp)

        self.assertIn(("Naruto", 1), groups)
        self.assertEqual(sorted(groups[("Naruto", 1)].keys()), [1, 2, 3])

    def test_ignores_non_video_files(self):
        self._make_file("Show", "Season 01", "S01E01.mkv")
        self._make_file("Show", "Season 01", "S01E01.txt")
        groups = collect_episodes(self.tmp)

        self.assertEqual(len(groups[("Show", 1)]), 1)

    def test_ignores_files_at_root_level(self):
        self._make_file("random_file.mkv")
        groups = collect_episodes(self.tmp)
        self.assertEqual(len(groups), 0)


class TestProcessFolder(unittest.TestCase):
    """Integration tests for process_folder() with mocked AI calls."""

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

    @mock.patch("ai_rename.query_episode_metadata")
    def test_renames_with_imdb_metadata(self, mock_query):
        self._make_file("Breaking Bad", "Season 01", "S01E01.mkv")
        mock_query.return_value = {
            1: {
                "title": "Pilot",
                "imdb_id": "tt0959621",
                "aired": "2008-01-20",
                "plot": "A chemistry teacher gets a diagnosis.",
            }
        }

        process_folder(self.tmp, api_key="fake")

        # Check renamed video file
        expected_video = os.path.join(
            self.tmp, "Breaking Bad", "Season 01",
            "Breaking Bad - S01E01 - Pilot [tt0959621].mkv",
        )
        self.assertTrue(os.path.exists(expected_video))

        # Check NFO sidecar
        expected_nfo = os.path.join(
            self.tmp, "Breaking Bad", "Season 01",
            "Breaking Bad - S01E01 - Pilot [tt0959621].nfo",
        )
        self.assertTrue(os.path.exists(expected_nfo))
        root = ElementTree.parse(expected_nfo).getroot()
        self.assertEqual(root.find("title").text, "Pilot")
        self.assertEqual(root.find("uniqueid").text, "tt0959621")
        self.assertEqual(root.find("aired").text, "2008-01-20")

    @mock.patch("ai_rename.query_episode_metadata")
    def test_renames_single_season_show(self, mock_query):
        self._make_file("Naruto", "001.mkv")
        self._make_file("Naruto", "002.mkv")
        mock_query.return_value = {
            1: {
                "title": "Enter: Naruto Uzumaki!",
                "imdb_id": "tt0409591",
                "aired": "2002-10-03",
                "plot": "Naruto is introduced.",
            },
            2: {
                "title": "My Name is Konohamaru!",
                "imdb_id": "tt0409592",
                "aired": "2002-10-10",
                "plot": None,
            },
        }

        process_folder(self.tmp, api_key="fake")

        self.assertTrue(
            os.path.exists(
                os.path.join(
                    self.tmp, "Naruto",
                    "Naruto - S01E01 - Enter Naruto Uzumaki! [tt0409591].mkv",
                )
            )
        )
        self.assertTrue(
            os.path.exists(
                os.path.join(
                    self.tmp, "Naruto",
                    "Naruto - S01E02 - My Name is Konohamaru! [tt0409592].mkv",
                )
            )
        )
        # NFO files also created
        self.assertTrue(
            os.path.exists(
                os.path.join(
                    self.tmp, "Naruto",
                    "Naruto - S01E01 - Enter Naruto Uzumaki! [tt0409591].nfo",
                )
            )
        )

    @mock.patch("ai_rename.query_episode_metadata")
    def test_dry_run_does_not_rename_or_write(self, mock_query):
        fp = self._make_file("Show", "Season 01", "S01E01.mkv")
        mock_query.return_value = {
            1: {"title": "Pilot", "imdb_id": "tt123", "aired": None, "plot": None},
        }

        process_folder(self.tmp, api_key="fake", dry_run=True)

        # Original still exists
        self.assertTrue(os.path.exists(fp))
        # New file does NOT exist
        self.assertFalse(
            os.path.exists(
                os.path.join(
                    self.tmp, "Show", "Season 01",
                    "Show - S01E01 - Pilot [tt123].mkv",
                )
            )
        )
        # NFO not written
        self.assertFalse(
            os.path.exists(
                os.path.join(
                    self.tmp, "Show", "Season 01",
                    "Show - S01E01 - Pilot [tt123].nfo",
                )
            )
        )

    @mock.patch("ai_rename.query_episode_metadata")
    def test_dry_run_folder_writes_staged_outputs(self, mock_query):
        fp = self._make_file("Show", "Season 01", "S01E01.mkv")
        dry_run_out = os.path.join(self.tmp, "_dryrun")
        mock_query.return_value = {
            1: {"title": "Pilot", "imdb_id": "tt123", "aired": None, "plot": None},
        }

        process_folder(
            self.tmp,
            api_key="fake",
            dry_run=True,
            dry_run_folder=dry_run_out,
        )

        # Original file is untouched.
        self.assertTrue(os.path.exists(fp))

        staged_video = os.path.join(
            dry_run_out,
            "Show",
            "Season 01",
            "Show - S01E01 - Pilot [tt123].mkv",
        )
        staged_nfo = os.path.join(
            dry_run_out,
            "Show",
            "Season 01",
            "Show - S01E01 - Pilot [tt123].nfo",
        )
        self.assertTrue(os.path.exists(staged_video))
        self.assertTrue(os.path.exists(staged_nfo))

    @mock.patch("ai_rename.query_episode_metadata")
    def test_fallback_title_when_ai_returns_empty(self, mock_query):
        self._make_file("Show", "Season 01", "S01E05.mkv")
        mock_query.return_value = {}

        process_folder(self.tmp, api_key="fake")

        expected = os.path.join(
            self.tmp, "Show", "Season 01", "Show - S01E05 - Episode 05.mkv",
        )
        self.assertTrue(os.path.exists(expected))
        # NFO still created with fallback title
        nfo = os.path.join(
            self.tmp, "Show", "Season 01", "Show - S01E05 - Episode 05.nfo",
        )
        self.assertTrue(os.path.exists(nfo))

    @mock.patch("ai_rename.query_episode_metadata")
    def test_preserves_existing_filename_title_when_ai_returns_empty(self, mock_query):
        self._make_file(
            "YuGiOh 5Ds",
            "Yu-Gi-Oh! 5D's - 001 - On Your Mark, Get Set, Duel!.mkv",
        )
        mock_query.return_value = {}

        process_folder(self.tmp, api_key="fake")

        expected = os.path.join(
            self.tmp,
            "YuGiOh 5Ds",
            "YuGiOh 5Ds - S01E01 - On Your Mark, Get Set, Duel!.mkv",
        )
        self.assertTrue(os.path.exists(expected))

        expected_nfo = os.path.join(
            self.tmp,
            "YuGiOh 5Ds",
            "YuGiOh 5Ds - S01E01 - On Your Mark, Get Set, Duel!.nfo",
        )
        self.assertTrue(os.path.exists(expected_nfo))

    @mock.patch("ai_rename.query_episode_metadata")
    def test_does_not_overwrite_existing(self, mock_query):
        fp = self._make_file("Show", "Season 01", "S01E01.mkv")
        self._make_file("Show", "Season 01", "Show - S01E01 - Pilot.mkv")
        mock_query.return_value = {
            1: {"title": "Pilot", "imdb_id": None, "aired": None, "plot": None},
        }

        process_folder(self.tmp, api_key="fake")

        # Original still exists (was not deleted)
        self.assertTrue(os.path.exists(fp))

    @mock.patch("ai_rename.query_episode_metadata")
    def test_high_episode_numbers_single_season(self, mock_query):
        """Shows like Yu-Gi-Oh with 200+ episodes in a single run."""
        self._make_file("Yu-Gi-Oh", "220.mkv")
        mock_query.return_value = {
            220: {
                "title": "The Final Duel Part 4",
                "imdb_id": "tt0817076",
                "aired": "2004-09-29",
                "plot": "The final showdown.",
            },
        }

        process_folder(self.tmp, api_key="fake")

        expected = os.path.join(
            self.tmp, "Yu-Gi-Oh",
            "Yu-Gi-Oh - S01E220 - The Final Duel Part 4 [tt0817076].mkv",
        )
        self.assertTrue(os.path.exists(expected))

    @mock.patch("ai_rename.query_episode_metadata")
    def test_writes_unresolved_report_for_missing_episode_number(self, mock_query):
        self._make_file("Show", "Season 01", "Episode Title Only.mkv")
        mock_query.return_value = {}

        process_folder(self.tmp, api_key="fake")

        report = os.path.join(self.tmp, "unresolved_episode_info.txt")
        self.assertTrue(os.path.exists(report))
        with open(report, "r", encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("Show\\Season 01\\Episode Title Only.mkv", content)
        self.assertIn("missing episode number", content)

    @mock.patch("ai_rename.query_episode_metadata")
    def test_no_imdb_id_omits_tag(self, mock_query):
        self._make_file("Show", "Season 01", "S01E01.mkv")
        mock_query.return_value = {
            1: {"title": "Pilot", "imdb_id": None, "aired": None, "plot": None},
        }

        process_folder(self.tmp, api_key="fake")

        expected = os.path.join(
            self.tmp, "Show", "Season 01", "Show - S01E01 - Pilot.mkv",
        )
        self.assertTrue(os.path.exists(expected))


class TestLoadEnvFile(unittest.TestCase):
    """Unit tests for load_env_file()."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env_path = os.path.join(self.tmp, ".env")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_loads_values(self):
        with open(self.env_path, "w", encoding="utf-8") as handle:
            handle.write("OPENAI_API_KEY=sk-test123\n")

        with mock.patch.dict(os.environ, {}, clear=True):
            loaded = load_env_file(self.env_path)
            self.assertEqual(loaded, 1)
            self.assertEqual(os.environ.get("OPENAI_API_KEY"), "sk-test123")


if __name__ == "__main__":
    unittest.main()
