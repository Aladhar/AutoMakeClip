import json
import tempfile
import unittest
import wave
from pathlib import Path

from automakeclip.config import MusicConfig
from automakeclip.music import select_music_track


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

    def _write_silent_wav(self, path: Path) -> None:
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(44100)
            handle.writeframes(b"\x00\x00" * 4410)


if __name__ == "__main__":
    unittest.main()
