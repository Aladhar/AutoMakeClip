import unittest

from automakeclip.analysis import extract_borderline_review_segments, extract_candidate_segments
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
        self.assertAlmostEqual(best.highlight_time or 0.0, 14.5)
        self.assertFalse(any("Generic" in candidate.note for candidate in candidates))

    def test_generates_generic_peak_candidates_without_metadata(self) -> None:
        timeline = AnalysisTimeline(
            times=[0.0, 4.0, 8.0, 12.0, 16.0, 20.0, 24.0, 28.0],
            visual_motion=[0.1, 0.2, 0.3, 0.9, 1.0, 0.8, 0.2, 0.1],
            killfeed_motion=[0.0, 0.1, 0.2, 0.9, 1.1, 0.85, 0.1, 0.0],
            hud_motion=[0.05, 0.1, 0.15, 0.7, 0.9, 0.7, 0.1, 0.05],
            center_motion=[0.05, 0.1, 0.2, 0.8, 0.95, 0.75, 0.1, 0.05],
            audio_rms=[0.02, 0.05, 0.08, 0.35, 0.5, 0.38, 0.05, 0.02],
            audio_flux=[0.01, 0.03, 0.05, 0.4, 0.6, 0.45, 0.04, 0.01],
            scene_change=[0.01, 0.02, 0.04, 0.3, 0.42, 0.31, 0.03, 0.01],
            gameplay_confidence=[-0.3, -0.1, 0.1, 0.7, 0.95, 0.8, 0.1, -0.2],
            scores=[-0.2, -0.1, 0.0, 0.8, 1.0, 0.82, 0.0, -0.1],
            duration=32.0,
        )
        metadata = VideoMetadata(duration=32.0, width=1920, height=1080, fps=60.0)

        candidates = extract_candidate_segments(timeline, metadata, AnalysisConfig())

        self.assertTrue(candidates)
        self.assertTrue(any("Generic" in candidate.note for candidate in candidates))
        self.assertTrue(any(candidate.highlight_time is not None for candidate in candidates))

    def test_extracts_borderline_review_segments_from_midband_activity(self) -> None:
        timeline = AnalysisTimeline(
            times=[0.0, 8.0, 16.0, 24.0, 32.0, 40.0, 48.0, 56.0, 64.0],
            visual_motion=[0.1, 0.18, 0.48, 0.36, 0.95, 0.34, 0.45, 0.16, 0.1],
            killfeed_motion=[0.0, 0.08, 0.31, 0.22, 1.05, 0.24, 0.29, 0.06, 0.0],
            hud_motion=[0.04, 0.09, 0.21, 0.16, 0.82, 0.18, 0.2, 0.08, 0.04],
            center_motion=[0.05, 0.1, 0.34, 0.28, 0.92, 0.3, 0.33, 0.09, 0.05],
            audio_rms=[0.02, 0.03, 0.14, 0.12, 0.46, 0.13, 0.15, 0.03, 0.02],
            audio_flux=[0.01, 0.02, 0.12, 0.08, 0.58, 0.09, 0.11, 0.02, 0.01],
            scene_change=[0.01, 0.02, 0.09, 0.07, 0.38, 0.08, 0.1, 0.02, 0.01],
            gameplay_confidence=[-0.2, -0.1, 0.34, 0.24, 0.97, 0.25, 0.31, -0.08, -0.2],
            scores=[-0.14, -0.04, 0.48, 0.34, 1.16, 0.36, 0.46, -0.03, -0.11],
            duration=68.0,
        )
        metadata = VideoMetadata(duration=68.0, width=1920, height=1080, fps=60.0)

        borderline = extract_borderline_review_segments(timeline, metadata, AnalysisConfig(), protected_segments=[])

        self.assertTrue(borderline)
        self.assertTrue(all(item.note.startswith("Borderline discard candidate:") for item in borderline))
        self.assertTrue(any(item.highlight_time in {16.0, 48.0} for item in borderline))


if __name__ == "__main__":
    unittest.main()
