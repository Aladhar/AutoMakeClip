import unittest

from automakeclip.inputs import YoutubeEntry, _filter_overwatch_entries, _is_probable_url, _looks_like_youtube_streams_page


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
