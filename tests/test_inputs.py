import unittest
from pathlib import Path

from automakeclip.inputs import YoutubeEntry, _filter_overwatch_entries, _is_probable_url, _looks_like_youtube_streams_page, looks_like_non_gameplay_source


class InputTests(unittest.TestCase):
    def test_detects_urls_and_streams_pages(self) -> None:
        self.assertTrue(_is_probable_url("https://www.youtube.com/watch?v=abc"))
        self.assertTrue(_looks_like_youtube_streams_page("https://www.youtube.com/@Heatheperson1/streams"))
        self.assertFalse(_looks_like_youtube_streams_page("https://www.youtube.com/watch?v=abc"))

    def test_filters_overwatch_stream_titles(self) -> None:
        entries = [
            YoutubeEntry(title="Overwatch ranked session", webpage_url="https://youtube.com/watch?v=1"),
            YoutubeEntry(title="variety night", webpage_url="https://youtube.com/watch?v=2"),
            YoutubeEntry(title="OW2 practice", webpage_url="https://youtube.com/watch?v=3"),
        ]
        filtered = _filter_overwatch_entries(entries)
        self.assertEqual([entry.webpage_url for entry in filtered], ["https://youtube.com/watch?v=1", "https://youtube.com/watch?v=3"])

    def test_detects_non_gameplay_source_names(self) -> None:
        self.assertTrue(looks_like_non_gameplay_source(Path("/tmp/zoom_0.mp4")))
        self.assertFalse(looks_like_non_gameplay_source(Path("/tmp/Overwatch__2026-03-06__20-04-22.mp4")))
