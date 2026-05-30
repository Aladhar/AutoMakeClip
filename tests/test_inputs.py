import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from automakeclip.inputs import (
    YoutubeEntry,
    _filter_overwatch_entries,
    _is_probable_url,
    _looks_like_youtube_streams_page,
    _parse_youtube_timecode,
    _playlist_end_args,
    _youtube_start_time_seconds,
    looks_like_non_gameplay_source,
    resolve_inputs,
)


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

    def test_stream_page_limit_can_be_unbounded(self) -> None:
        self.assertEqual(_playlist_end_args(None), [])
        self.assertEqual(_playlist_end_args(0), [])
        self.assertEqual(_playlist_end_args(8), ["--playlist-end", "8"])

    def test_parses_youtube_timestamp_inputs(self) -> None:
        self.assertEqual(_parse_youtube_timecode("4719s"), 4719.0)
        self.assertEqual(_parse_youtube_timecode("1h18m39s"), 4719.0)
        self.assertEqual(_youtube_start_time_seconds("https://www.youtube.com/watch?v=abc&t=4719s"), 4719.0)
        self.assertEqual(_youtube_start_time_seconds("https://youtu.be/abc?start=90"), 90.0)

    def test_detects_non_gameplay_source_names(self) -> None:
        self.assertTrue(looks_like_non_gameplay_source(Path("/tmp/zoom_0.mp4")))
        self.assertFalse(looks_like_non_gameplay_source(Path("/tmp/Overwatch__2026-03-06__20-04-22.mp4")))

    def test_resolves_nested_local_video_files(self) -> None:
        with TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            nested = root / "Overwatch Application"
            nested.mkdir()
            direct_mp4 = root / "Overwatch__2026-03-06__20-04-22.mp4"
            nested_mkv = nested / "Overwatch Application.mkv"
            ignored_text = nested / "notes.txt"
            direct_mp4.write_bytes(b"")
            nested_mkv.write_bytes(b"")
            ignored_text.write_text("not a video", encoding="utf-8")

            resolved = resolve_inputs([[str(root)]], download_root=root / "downloads")

            self.assertEqual(resolved, sorted([direct_mp4.resolve(), nested_mkv.resolve()], key=lambda path: str(path).lower()))
