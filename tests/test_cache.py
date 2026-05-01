import tempfile
import unittest
from pathlib import Path

from automakeclip.cache import load_cached_analysis_entries, store_analysis_cache
from automakeclip.config import AnalysisConfig
from automakeclip.types import AnalysisTimeline, VideoMetadata


class CacheTests(unittest.TestCase):
    def test_load_cached_analysis_entries_returns_valid_current_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            source_path = root / "Overwatch__2026-03-06__20-04-22.mp4"
            source_path.write_bytes(b"video")
            config = AnalysisConfig()

            store_analysis_cache(
                root / "cache",
                source_path,
                config,
                VideoMetadata(duration=4.0, width=1920, height=1080, fps=60.0),
                _timeline(duration=4.0),
            )

            entries = load_cached_analysis_entries(root / "cache", config)

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0][0], source_path)
            self.assertEqual(entries[0][1].duration, 4.0)
            self.assertEqual(entries[0][2].duration, 4.0)

    def test_load_cached_analysis_entries_ignores_stale_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            source_path = root / "Overwatch__2026-03-06__20-04-22.mp4"
            source_path.write_bytes(b"video")
            config = AnalysisConfig()
            cache_dir = root / "cache"

            store_analysis_cache(
                cache_dir,
                source_path,
                config,
                VideoMetadata(duration=4.0, width=1920, height=1080, fps=60.0),
                _timeline(duration=4.0),
            )
            source_path.write_bytes(b"changed")

            self.assertEqual(load_cached_analysis_entries(cache_dir, config), [])


def _timeline(duration: float) -> AnalysisTimeline:
    return AnalysisTimeline(
        times=[0.0, 1.0, 2.0],
        visual_motion=[0.1, 0.8, 0.1],
        killfeed_motion=[0.0, 0.9, 0.0],
        hud_motion=[0.1, 0.6, 0.1],
        center_motion=[0.1, 0.7, 0.1],
        audio_rms=[0.1, 0.8, 0.1],
        audio_flux=[0.1, 0.8, 0.1],
        scene_change=[0.0, 0.5, 0.0],
        gameplay_confidence=[0.1, 0.9, 0.1],
        scores=[0.1, 1.0, 0.1],
        duration=duration,
    )


if __name__ == "__main__":
    unittest.main()
