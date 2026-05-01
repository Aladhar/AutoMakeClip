import unittest
from pathlib import Path

from automakeclip.config import RenderConfig
from automakeclip.render import _build_music_mix_filter, compute_music_start_offset
from automakeclip.types import MontagePlan, MusicTrack, Segment


class RenderSyncTests(unittest.TestCase):
    def test_aligns_first_drop_to_first_highlight_anchor(self) -> None:
        plan = MontagePlan(
            input_paths=[Path("/tmp/input.mp4")],
            output_path=Path("/tmp/output.mp4"),
            title="Test",
            subtitle="Test",
            target_seconds=38.0,
            source_duration=60.0,
            source_durations={"/tmp/input.mp4": 60.0},
            segments=[
                Segment(
                    start=10.0,
                    end=15.0,
                    score=5.0,
                    label="highlight",
                    note="",
                    source_path="/tmp/input.mp4",
                    highlight_time=13.8,
                ),
                Segment(
                    start=22.0,
                    end=27.0,
                    score=4.2,
                    label="fight",
                    note="",
                    source_path="/tmp/input.mp4",
                    highlight_time=24.0,
                ),
            ],
            mood="aggro",
            target_bpm=150.0,
            music=MusicTrack(
                title="Song",
                artist="Artist",
                license_name="License",
                page_url="local://song",
                download_url="local://song",
                drop_times=[18.2, 24.4, 31.0],
            ),
        )

        offset = compute_music_start_offset(plan, intro_seconds=2.4, montage_duration=20.0)

        self.assertAlmostEqual(offset, 12.0, places=1)

    def test_music_mix_filter_ducks_game_under_music(self) -> None:
        filter_complex = _build_music_mix_filter(
            config=RenderConfig(),
            music_start_offset=12.0,
            music_end=52.0,
            fade_out_start=38.0,
        )

        self.assertIn("[game][music]sidechaincompress", filter_complex)
        self.assertIn("weights=0.85 1.0", filter_complex)
        self.assertIn("volume=0.95", filter_complex)


if __name__ == "__main__":
    unittest.main()
