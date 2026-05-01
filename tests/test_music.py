import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from automakeclip.config import MusicConfig
from automakeclip.music import MusicSelectionError, resolve_music_manifest_path, select_music_track


class MusicTests(unittest.TestCase):
    def test_prefers_local_youtube_safe_track(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            music_dir = root / "music"
            music_dir.mkdir()
            track_path = music_dir / "heatcheck.wav"
            self._write_silent_wav(track_path)

            manifest_path = root / "tracks.json"
            manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "title": "Heat Check",
                            "artist": "Creator Track",
                            "license_name": "YouTube Audio Library",
                            "page_url": "https://youtube.com/audiolibrary",
                            "download_url": "local://library",
                            "local_path": str(track_path),
                            "bpm": 138,
                            "tags": ["electronic", "house"],
                            "source_kind": "youtube_audio_library",
                            "youtube_safe": True,
                            "trend_score": 9.5,
                            "drop_times": [18.0, 25.5],
                        }
                    ]
                ),
                encoding="utf-8",
            )

            track = select_music_track(
                mood="balanced",
                target_bpm=138.0,
                output_dir=root / "output_music",
                config=MusicConfig(source="library", library_manifest=str(manifest_path), allow_generated_fallback=False),
            )

            self.assertEqual(track.title, "Heat Check")
            self.assertTrue(track.youtube_safe)
            self.assertEqual(track.source_kind, "youtube_audio_library")
            self.assertEqual(track.local_path, track_path)
            self.assertEqual(track.drop_times, [18.0, 25.5])

    def test_bad_manifest_falls_back_only_when_explicitly_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            manifest_path = root / "tracks.json"
            manifest_path.write_text("{not valid json", encoding="utf-8")

            track = select_music_track(
                mood="balanced",
                target_bpm=138.0,
                output_dir=root / "output_music",
                config=MusicConfig(
                    source="library",
                    library_manifest=str(manifest_path),
                    allow_generated_fallback=True,
                ),
            )

            self.assertEqual(track.source_kind, "generated")

    def test_spotify_source_only_uses_spotify_tagged_local_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            music_dir = root / "music"
            music_dir.mkdir()
            spotify_path = music_dir / "spotify.wav"
            library_path = music_dir / "library.wav"
            self._write_silent_wav(spotify_path)
            self._write_silent_wav(library_path)

            manifest_path = root / "tracks.json"
            manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "title": "Library Track",
                            "artist": "Local",
                            "local_path": str(library_path),
                            "bpm": 138,
                            "tags": ["electronic", "house"],
                            "source_kind": "youtube_audio_library",
                            "youtube_safe": True,
                            "trend_score": 20.0,
                        },
                        {
                            "title": "Spotify Track",
                            "artist": "Spotify Artist",
                            "page_url": "https://open.spotify.com/track/example",
                            "download_url": "spotify:track:example",
                            "local_path": str(spotify_path),
                            "bpm": 138,
                            "tags": ["electronic", "house"],
                            "source_kind": "spotify",
                            "trend_score": 1.0,
                        },
                    ]
                ),
                encoding="utf-8",
            )

            track = select_music_track(
                mood="balanced",
                target_bpm=138.0,
                output_dir=root / "output_music",
                config=MusicConfig(source="spotify", library_manifest=str(manifest_path), allow_generated_fallback=False),
            )

            self.assertEqual(track.title, "Spotify Track")
            self.assertEqual(track.source_kind, "spotify")

    def test_youtube_source_downloads_playlist_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            downloaded_path = root / "output_music" / "playlist.mp3"
            manifest_path = root / "tracks.json"
            manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "title": "Stream Playlist",
                            "artist": "YouTube",
                            "page_url": "https://www.youtube.com/watch?v=abc&list=playlist",
                            "download_url": "https://www.youtube.com/watch?v=abc&list=playlist",
                            "source_kind": "youtube_playlist",
                            "tags": ["electronic", "gaming"],
                            "trend_score": 8.0,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            def fake_download(track, output_dir, config):
                output_dir.mkdir(parents=True, exist_ok=True)
                downloaded_path.write_bytes(b"mp3")
                return downloaded_path

            with patch("automakeclip.music._download_youtube_audio", side_effect=fake_download):
                track = select_music_track(
                    mood="balanced",
                    target_bpm=138.0,
                    output_dir=root / "output_music",
                    config=MusicConfig(source="youtube", library_manifest=str(manifest_path), allow_generated_fallback=False),
                )

            self.assertEqual(track.title, "Stream Playlist")
            self.assertEqual(track.source_kind, "youtube_playlist")
            self.assertEqual(track.local_path, downloaded_path)

    def test_spotify_manifest_without_local_audio_explains_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            manifest_path = root / "tracks.json"
            manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "title": "Spotify Track",
                            "artist": "Spotify Artist",
                            "page_url": "https://open.spotify.com/track/example",
                            "download_url": "spotify:track:example",
                            "source_kind": "spotify",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaises(MusicSelectionError) as raised:
                select_music_track(
                    mood="balanced",
                    target_bpm=138.0,
                    output_dir=root / "output_music",
                    config=MusicConfig(source="spotify", library_manifest=str(manifest_path), allow_generated_fallback=False),
                )

            self.assertIn("local audio file path", str(raised.exception))

    def test_resolve_music_manifest_path_prefers_existing_cwd_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            manifest_path = root / "tracks.json"
            manifest_path.write_text("[]", encoding="utf-8")
            previous_cwd = Path.cwd()
            try:
                import os

                os.chdir(root)
                resolved = resolve_music_manifest_path("tracks.json")
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(resolved, manifest_path.resolve())

    def _write_silent_wav(self, path: Path) -> None:
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(44100)
            handle.writeframes(b"\x00\x00" * 4410)


if __name__ == "__main__":
    unittest.main()
