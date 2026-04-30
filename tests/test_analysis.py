import unittest

from automakeclip.analysis import extract_candidate_segments
from automakeclip.config import AnalysisConfig
from automakeclip.types import AnalysisTimeline, VideoMetadata


class AnalysisTests(unittest.TestCase):
    def test_prefers_steelseries_kill_windows(self) -> None:
        timeline = AnalysisTimeline(
            times=[0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0],
            visual_motion=[0.1, 0.2, 0.8, 1.0, 0.7, 0.2, 0.1],
            killfeed_motion=[0.0, 0.1, 0.7, 1.1, 0.8, 0.1, 0.0],
            hud_motion=[0.1, 0.2, 0.6, 0.9, 0.6, 0.2, 0.1],
            center_motion=[0.1, 0.2, 0.7, 0.95, 0.7, 0.2, 0.1],
            audio_rms=[0.05, 0.1, 0.4, 0.6, 0.35, 0.1, 0.05],
            audio_flux=[0.02, 0.08, 0.3, 0.5, 0.28, 0.08, 0.02],
            scene_change=[0.02, 0.05, 0.3, 0.45, 0.22, 0.04, 0.02],
            gameplay_confidence=[-0.5, 0.0, 0.7, 1.0, 0.8, -0.1, -0.4],
            scores=[-0.3, 0.1, 0.9, 1.2, 0.85, 0.1, -0.2],
            duration=32.0,
        )
        metadata = VideoMetadata(
            duration=32.0,
            width=2560,
            height=1440,
            fps=60.0,
            kill_events=[
                {"type": "KILL", "timestamp": 12.0, "name": "ELIMINATION"},
                {"type": "KILL", "timestamp": 14.5, "name": "DOUBLE ELIMINATION"},
            ],
            source_game="Overwatch",
            trigger_name="multikill",
        )

        candidates = extract_candidate_segments(timeline, metadata, AnalysisConfig())

        self.assertTrue(candidates)
        best = max(candidates, key=lambda item: item.score)
        self.assertEqual(best.label, "slay")
        self.assertLessEqual(best.start, 12.0)
        self.assertGreaterEqual(best.end, 14.5)
        self.assertIn("SteelSeries", best.note)


if __name__ == "__main__":
    unittest.main()
