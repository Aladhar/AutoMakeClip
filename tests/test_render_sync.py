import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from automakeclip.config import AnalysisConfig, RenderConfig
from automakeclip.render import _build_music_mix_filter, compute_music_start_offset, render_montage
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

        self.assertIn("asplit=2[music_sc][music_mix]", filter_complex)
        self.assertIn("aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo", filter_complex)
        self.assertIn("[game_sc][music_sc]sidechaincompress", filter_complex)
        self.assertIn("weights=0.85 1.0", filter_complex)
        self.assertNotIn("normalize=0", filter_complex)
        self.assertIn("volume=0.95", filter_complex)

    def test_render_starts_with_gameplay_when_intro_duration_is_zero(self) -> None:
        with TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            concat_payloads = []
            plan = MontagePlan(
                input_paths=[root / "input.mp4"],
                output_path=root / "output.mp4",
                title="Test",
                subtitle="Test",
                target_seconds=10.0,
                source_duration=10.0,
                source_durations={str(root / "input.mp4"): 10.0},
                segments=[
                    Segment(
                        start=1.0,
                        end=4.0,
                        score=5.0,
                        label="highlight",
                        source_path=str(root / "input.mp4"),
                        highlight_time=3.0,
                    )
                ],
                mood="balanced",
                target_bpm=138.0,
            )

            def fake_run_ffmpeg(args):
                if "-f" in args and "concat" in args:
                    concat_path = Path(args[args.index("-i") + 1])
                    concat_payloads.append(concat_path.read_text(encoding="utf-8"))

            with patch("automakeclip.render._create_intro") as create_intro, patch(
                "automakeclip.render._render_segment"
            ), patch("automakeclip.render.run_ffmpeg", side_effect=fake_run_ffmpeg), patch(
                "automakeclip.render._write_sidecars"
            ), patch("automakeclip.latest_render.record_latest_render"):
                render_montage(plan, AnalysisConfig(), RenderConfig(), keep_temp=False)

        create_intro.assert_not_called()
        self.assertEqual(len(concat_payloads), 1)
        self.assertIn("segment_01.mp4", concat_payloads[0])
        self.assertNotIn("intro.mp4", concat_payloads[0])


if __name__ == "__main__":
    unittest.main()
