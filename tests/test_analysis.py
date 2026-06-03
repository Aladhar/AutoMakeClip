import inspect
import unittest

import numpy as np

from automakeclip.analysis import (
    _inactive_overlay_score,
    _robust_normalize,
    _smooth,
    collect_candidate_segments,
    extract_borderline_review_segments,
    extract_candidate_segments,
)
from automakeclip.config import AnalysisConfig
from automakeclip.types import AnalysisTimeline, VideoMetadata


class AnalysisTests(unittest.TestCase):
    def test_smoothing_preserves_short_signal_length(self) -> None:
        values = np.array([0.2, 0.8], dtype=np.float32)
        smoothed = _smooth(values, window_size=5)
        self.assertEqual(smoothed.shape, values.shape)

    def test_robust_normalize_caps_extreme_sparse_spikes(self) -> None:
        values = np.array([0.0, 0.0, 0.0, 0.009], dtype=np.float32)
        normalized = _robust_normalize(values)

        self.assertLessEqual(float(normalized.max()), 4.0)
        self.assertGreaterEqual(float(normalized.min()), -3.5)

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
        self.assertEqual(best.label, "highlight")
        self.assertLessEqual(best.start, 12.0)
        self.assertGreaterEqual(best.end, 14.5)
        self.assertIn("SteelSeries", best.note)
        self.assertAlmostEqual(best.highlight_time or 0.0, 14.5)
        self.assertFalse(any("Generic" in candidate.note for candidate in candidates))

    def test_event_segments_keep_highlight_inside_clamped_window(self) -> None:
        timeline = AnalysisTimeline(
            times=[float(index) for index in range(70)],
            visual_motion=[0.1] * 70,
            killfeed_motion=[0.2] * 70,
            hud_motion=[0.1] * 70,
            center_motion=[0.1] * 70,
            audio_rms=[0.1] * 70,
            audio_flux=[0.1] * 70,
            scene_change=[0.1] * 70,
            gameplay_confidence=[0.5] * 70,
            scores=[0.2] * 70,
            duration=70.0,
        )
        metadata = VideoMetadata(
            duration=70.0,
            width=1920,
            height=1080,
            fps=60.0,
            kill_events=[
                {"type": "KILL", "timestamp": 44.0, "name": "ELIMINATION"},
                {"type": "KILL", "timestamp": 49.0, "name": "DOUBLE ELIMINATION"},
                {"type": "KILL", "timestamp": 54.5, "name": "TRIPLE ELIMINATION"},
            ],
        )

        candidates = extract_candidate_segments(timeline, metadata, AnalysisConfig())
        event_candidate = max(candidates, key=lambda item: item.score)

        self.assertLessEqual(event_candidate.start, event_candidate.highlight_time or 0.0)
        self.assertGreaterEqual(event_candidate.end, event_candidate.highlight_time or 0.0)
        self.assertLessEqual(event_candidate.duration, AnalysisConfig().max_segment_seconds)

    def test_deprioritizes_repetitive_high_density_kill_streams(self) -> None:
        timeline = AnalysisTimeline(
            times=[float(index) for index in range(61)],
            visual_motion=[0.4] * 61,
            killfeed_motion=[0.8] * 61,
            hud_motion=[0.5] * 61,
            center_motion=[0.5] * 61,
            audio_rms=[0.3] * 61,
            audio_flux=[0.3] * 61,
            scene_change=[0.2] * 61,
            gameplay_confidence=[0.8] * 61,
            scores=[1.0] * 61,
            duration=60.0,
        )
        metadata = VideoMetadata(
            duration=60.0,
            width=2560,
            height=1440,
            fps=60.0,
            kill_events=[
                {"type": "KILL", "timestamp": float(index * 2), "name": "ELIMINATION"}
                for index in range(16)
            ],
            source_game="Overwatch",
            trigger_name="multikill",
        )

        candidates = extract_candidate_segments(timeline, metadata, AnalysisConfig())

        event_candidates = [candidate for candidate in candidates if "high-density kill stream" in candidate.note]
        self.assertTrue(event_candidates)
        self.assertTrue(all(candidate.label == "fight" for candidate in event_candidates))
        self.assertFalse(any("SteelSeries multi-kill sequence" in candidate.note for candidate in event_candidates))

    def test_deprioritizes_short_artificial_kill_bursts(self) -> None:
        timeline = AnalysisTimeline(
            times=[float(index) for index in range(61)],
            visual_motion=[0.4] * 61,
            killfeed_motion=[0.8] * 61,
            hud_motion=[0.5] * 61,
            center_motion=[0.5] * 61,
            audio_rms=[0.3] * 61,
            audio_flux=[0.3] * 61,
            scene_change=[0.2] * 61,
            gameplay_confidence=[0.8] * 61,
            scores=[1.0] * 61,
            duration=60.0,
        )
        metadata = VideoMetadata(
            duration=60.0,
            width=2560,
            height=1440,
            fps=60.0,
            kill_events=[
                {"type": "KILL", "timestamp": 36.0 + float(index) * 1.6, "name": "DOUBLE ELIMINATION"}
                for index in range(10)
            ],
            source_game="Overwatch",
            trigger_name="multikill",
        )

        candidates = extract_candidate_segments(timeline, metadata, AnalysisConfig())

        self.assertTrue(any("high-density kill stream" in candidate.note for candidate in candidates))
        self.assertFalse(any("SteelSeries multi-kill sequence" in candidate.note for candidate in candidates))

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

    def test_sustained_branch_generates_multikill_candidate_for_repeated_bursts(self) -> None:
        timeline = AnalysisTimeline(
            times=[float(index * 2) for index in range(30)],
            visual_motion=[0.1] * 30,
            killfeed_motion=[0.05 if index not in {10, 12, 14} else 0.9 for index in range(30)],
            hud_motion=[0.05 if index not in {10, 12, 14} else 0.8 for index in range(30)],
            center_motion=[0.05 if index not in {10, 12, 14} else 0.7 for index in range(30)],
            audio_rms=[0.02 if index not in {10, 12, 14} else 0.45 for index in range(30)],
            audio_flux=[0.01 if index not in {10, 12, 14} else 0.4 for index in range(30)],
            scene_change=[0.01] * 30,
            gameplay_confidence=[0.2 if index not in {10, 12, 14} else 0.8 for index in range(30)],
            scores=[0.1 if index not in {10, 12, 14} else 1.1 for index in range(30)],
            duration=60.0,
        )
        metadata = VideoMetadata(duration=60.0, width=1920, height=1080, fps=60.0)

        config = AnalysisConfig()
        config.enable_sustained_multikill_candidates = True
        candidates = extract_candidate_segments(timeline, metadata, config)

        sustained = [candidate for candidate in candidates if candidate.candidate_type == "sustained_multikill"]
        self.assertTrue(sustained)
        self.assertTrue(any(candidate.duration > 4.1 for candidate in sustained))

    def test_sustained_branch_rejects_single_isolated_spikes(self) -> None:
        timeline = AnalysisTimeline(
            times=[float(index * 2) for index in range(30)],
            visual_motion=[0.1] * 30,
            killfeed_motion=[0.05 if index != 15 else 1.0 for index in range(30)],
            hud_motion=[0.05 if index != 15 else 0.9 for index in range(30)],
            center_motion=[0.05 if index != 15 else 0.8 for index in range(30)],
            audio_rms=[0.02 if index != 15 else 0.55 for index in range(30)],
            audio_flux=[0.01 if index != 15 else 0.45 for index in range(30)],
            scene_change=[0.01] * 30,
            gameplay_confidence=[0.2 if index != 15 else 0.85 for index in range(30)],
            scores=[0.1 if index != 15 else 1.3 for index in range(30)],
            duration=60.0,
        )
        metadata = VideoMetadata(duration=60.0, width=1920, height=1080, fps=60.0)

        config = AnalysisConfig()
        config.enable_sustained_multikill_candidates = True
        candidates = extract_candidate_segments(timeline, metadata, config)

        sustained = [candidate for candidate in candidates if candidate.candidate_type is not None]
        self.assertFalse(sustained)

    def test_sustained_candidate_anchor_and_wider_window(self) -> None:
        timeline = AnalysisTimeline(
            times=[float(index * 2) for index in range(40)],
            visual_motion=[0.1] * 40,
            killfeed_motion=[0.05 if index not in {12, 14, 16} else 0.85 for index in range(40)],
            hud_motion=[0.05 if index not in {12, 14, 16} else 0.8 for index in range(40)],
            center_motion=[0.05 if index not in {12, 14, 16} else 0.75 for index in range(40)],
            audio_rms=[0.02 if index not in {12, 14, 16} else 0.45 for index in range(40)],
            audio_flux=[0.01 if index not in {12, 14, 16} else 0.35 for index in range(40)],
            scene_change=[0.01] * 40,
            gameplay_confidence=[0.2 if index not in {12, 14, 16} else 0.8 for index in range(40)],
            scores=[0.1 if index not in {12, 14, 16} else 1.1 for index in range(40)],
            duration=80.0,
        )
        metadata = VideoMetadata(duration=80.0, width=1920, height=1080, fps=60.0)

        config = AnalysisConfig()
        config.enable_sustained_multikill_candidates = True
        candidates = extract_candidate_segments(timeline, metadata, config)

        sustained = [candidate for candidate in candidates if candidate.candidate_type == "sustained_multikill"]
        self.assertTrue(sustained)
        self.assertTrue(any(candidate.duration > 3.8 for candidate in sustained))
        self.assertTrue(any(candidate.highlight_time in {24.0, 28.0, 32.0} for candidate in sustained))

    def test_sustained_branch_disabled_by_default_preserves_generic_behavior(self) -> None:
        timeline = AnalysisTimeline(
            times=[float(index * 2) for index in range(30)],
            visual_motion=[0.1] * 30,
            killfeed_motion=[0.05 if index not in {10, 12, 14} else 0.9 for index in range(30)],
            hud_motion=[0.05 if index not in {10, 12, 14} else 0.8 for index in range(30)],
            center_motion=[0.05 if index not in {10, 12, 14} else 0.7 for index in range(30)],
            audio_rms=[0.02 if index not in {10, 12, 14} else 0.45 for index in range(30)],
            audio_flux=[0.01 if index not in {10, 12, 14} else 0.4 for index in range(30)],
            scene_change=[0.01] * 30,
            gameplay_confidence=[0.2 if index not in {10, 12, 14} else 0.8 for index in range(30)],
            scores=[0.1 if index not in {10, 12, 14} else 1.1 for index in range(30)],
            duration=60.0,
        )
        metadata = VideoMetadata(duration=60.0, width=1920, height=1080, fps=60.0)

        candidates = extract_candidate_segments(timeline, metadata, AnalysisConfig())
        self.assertFalse(any(candidate.candidate_type for candidate in candidates))

    def test_analysis_module_contains_no_eklipse_reference(self) -> None:
        import automakeclip.analysis as analysis_module

        self.assertNotIn("vod_001_eklipse", inspect.getsource(analysis_module).lower())
        self.assertNotIn("eklipse_gameplay_only", inspect.getsource(analysis_module).lower())

    def test_rejects_scene_only_spikes_without_fight_consensus(self) -> None:
        timeline = AnalysisTimeline(
            times=[0.0, 4.0, 8.0, 12.0, 16.0, 20.0, 24.0, 28.0],
            visual_motion=[0.02, 0.02, 0.02, 0.02, 0.03, 0.02, 0.02, 0.02],
            killfeed_motion=[0.0] * 8,
            hud_motion=[0.0] * 8,
            center_motion=[0.01] * 8,
            audio_rms=[0.01] * 8,
            audio_flux=[0.0] * 8,
            scene_change=[0.01, 0.01, 0.01, 0.75, 0.95, 0.7, 0.01, 0.01],
            gameplay_confidence=[0.15, 0.16, 0.14, 0.2, 0.22, 0.18, 0.15, 0.14],
            scores=[0.02, 0.03, 0.02, 0.12, 0.18, 0.11, 0.02, 0.02],
            duration=32.0,
        )
        metadata = VideoMetadata(duration=32.0, width=1920, height=1080, fps=60.0)

        candidates = extract_candidate_segments(timeline, metadata, AnalysisConfig())

        self.assertFalse(candidates)

    def test_rejects_menu_like_low_motion_source_even_if_relative_scores_spike(self) -> None:
        timeline = AnalysisTimeline(
            times=[float(index * 4) for index in range(8)],
            visual_motion=[0.00001, 0.00001, 0.00002, 0.0008, 0.0027, 0.0007, 0.00002, 0.00001],
            killfeed_motion=[0.0, 0.0, 0.0001, 0.0004, 0.0034, 0.0005, 0.0, 0.0],
            hud_motion=[0.0, 0.0, 0.0001, 0.0005, 0.0023, 0.0008, 0.0, 0.0],
            center_motion=[0.00001, 0.00002, 0.00003, 0.0008, 0.0033, 0.0010, 0.00002, 0.00001],
            audio_rms=[0.003, 0.004, 0.005, 0.008, 0.015, 0.010, 0.004, 0.003],
            audio_flux=[0.01, 0.02, 0.03, 0.05, 0.19, 0.08, 0.02, 0.01],
            scene_change=[0.0, 0.0, 0.0, 0.002, 0.009, 0.004, 0.0, 0.0],
            gameplay_confidence=[0.2, 0.4, 0.6, 8.0, 30.0, 12.0, 0.3, 0.2],
            scores=[0.3, 0.5, 0.7, 50.0, 620.0, 180.0, 0.4, 0.3],
            duration=32.0,
        )
        metadata = VideoMetadata(duration=32.0, width=1280, height=720, fps=60.0)

        candidates = extract_candidate_segments(timeline, metadata, AnalysisConfig())

        self.assertFalse(candidates)

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

    def test_collect_candidate_segments_keeps_full_source_pool(self) -> None:
        config = AnalysisConfig()
        timeline = AnalysisTimeline(
            times=[float(index * 5) for index in range(10)],
            visual_motion=[0.1, 0.2, 0.85, 0.25, 0.2, 0.88, 0.22, 0.18, 0.92, 0.2],
            killfeed_motion=[0.0, 0.1, 0.9, 0.12, 0.08, 0.95, 0.1, 0.08, 0.98, 0.1],
            hud_motion=[0.08, 0.12, 0.75, 0.14, 0.12, 0.79, 0.13, 0.1, 0.82, 0.12],
            center_motion=[0.07, 0.1, 0.72, 0.12, 0.1, 0.76, 0.11, 0.09, 0.8, 0.1],
            audio_rms=[0.02, 0.04, 0.5, 0.05, 0.04, 0.48, 0.05, 0.03, 0.52, 0.04],
            audio_flux=[0.01, 0.03, 0.55, 0.04, 0.03, 0.53, 0.04, 0.02, 0.57, 0.03],
            scene_change=[0.01, 0.02, 0.25, 0.03, 0.02, 0.27, 0.03, 0.02, 0.29, 0.02],
            gameplay_confidence=[-0.2, 0.0, 0.9, 0.05, 0.0, 0.92, 0.04, -0.05, 0.95, 0.0],
            scores=[-0.1, 0.05, 1.1, 0.08, 0.02, 1.05, 0.07, 0.0, 1.0, 0.03],
            duration=50.0,
        )
        metadata = VideoMetadata(duration=50.0, width=1920, height=1080, fps=60.0)

        full_candidates = collect_candidate_segments(timeline, metadata, config, include_silly=False)

        self.assertGreaterEqual(len(full_candidates), 2)
        self.assertTrue(all("Generic kill-heavy peak window." in candidate.note for candidate in full_candidates))
        self.assertTrue(all(candidate.duration <= 4.2 for candidate in full_candidates))

    # ── Kill-cam rejection experiment tests ──────────────────────────────

    def _make_frame(
        self,
        width: int = 1920,
        height: int = 1080,
        fill_bgr: int = 100,
        roi_bgr: int = 10,
        roi: tuple = (0.10, 0.75, 0.70, 0.15),
    ) -> np.ndarray:
        """Create a synthetic uint8 BGR frame with a uniform fill and a
        differently-coloured rectangular ROI (for testing the kill-cam HUD
        region)."""
        frame = np.full((height, width, 3), fill_bgr, dtype=np.uint8)
        x, y, w, h = roi
        y0 = int(height * y)
        y1 = int(height * (y + h))
        x0 = int(width * x)
        x1 = int(width * (x + w))
        frame[y0:y1, x0:x1] = roi_bgr
        return frame

    def test_dark_bottom_hud_without_overlay_does_not_trigger_inactive_overlay(self) -> None:
        """Dark bottom HUD alone should not produce a strong inactive overlay score."""
        dark_frame = self._make_frame(fill_bgr=160, roi_bgr=5)
        score = _inactive_overlay_score(dark_frame)
        self.assertEqual(score, 0.0)

    def test_active_gameplay_has_low_inactive_overlay_score(self) -> None:
        """Active multi-kill gameplay frame should have low inactive overlay score."""
        active_frame = self._make_frame(fill_bgr=140, roi_bgr=180)
        score = _inactive_overlay_score(active_frame)
        self.assertLess(score, 0.1)

    def test_eventless_generic_path_rejects_sustained_ui_churn_without_kill_burst(self) -> None:
        timeline = AnalysisTimeline(
            times=[float(index * 4) for index in range(8)],
            visual_motion=[0.12, 0.14, 0.16, 0.18, 0.17, 0.16, 0.14, 0.12],
            killfeed_motion=[0.18, 0.19, 0.18, 0.19, 0.18, 0.19, 0.18, 0.18],
            hud_motion=[0.16, 0.17, 0.18, 0.17, 0.18, 0.17, 0.16, 0.16],
            center_motion=[0.15, 0.16, 0.17, 0.17, 0.16, 0.17, 0.16, 0.15],
            audio_rms=[0.01] * 8,
            audio_flux=[0.0] * 8,
            scene_change=[0.22, 0.24, 0.26, 0.28, 0.26, 0.24, 0.22, 0.2],
            gameplay_confidence=[0.35, 0.4, 0.45, 0.5, 0.48, 0.44, 0.4, 0.36],
            scores=[0.2, 0.28, 0.32, 0.36, 0.34, 0.3, 0.25, 0.2],
            duration=32.0,
        )
        metadata = VideoMetadata(duration=32.0, width=1920, height=1080, fps=60.0)

        candidates = extract_candidate_segments(timeline, metadata, AnalysisConfig())

        self.assertFalse(candidates)


if __name__ == "__main__":
    unittest.main()
