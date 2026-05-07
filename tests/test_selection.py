import unittest

from automakeclip.selection import (
    candidate_tier,
    cross_capture_duplicate,
    is_training_or_practice_candidate,
    select_global_segments,
    sequence_segments,
)
from automakeclip.types import Segment


class SelectionTests(unittest.TestCase):
    def test_select_global_segments_respects_overlap_and_budget(self) -> None:
        candidates = [
            Segment(start=0.0, end=5.0, score=9.0, label="highlight", source_path="/tmp/a.mp4"),
            Segment(start=1.0, end=5.5, score=8.5, label="fight", source_path="/tmp/a.mp4"),
            Segment(start=10.0, end=14.0, score=8.0, label="fight", source_path="/tmp/b.mp4"),
            Segment(start=20.0, end=24.0, score=7.9, label="fight", source_path="/tmp/c.mp4"),
        ]
        selected = select_global_segments(candidates, target_seconds=12.0, intro_seconds=2.4)
        self.assertEqual(len(selected), 2)
        self.assertEqual(selected[0].source_path, "/tmp/a.mp4")
        self.assertEqual(selected[1].source_path, "/tmp/b.mp4")

    def test_sequence_segments_inserts_single_silly_beat(self) -> None:
        segments = [
            Segment(start=0.0, end=4.0, score=8.0, label="highlight", source_path="/tmp/a.mp4"),
            Segment(start=5.0, end=8.0, score=6.0, label="silly", source_path="/tmp/b.mp4"),
            Segment(start=9.0, end=13.0, score=7.0, label="fight", source_path="/tmp/c.mp4"),
        ]
        ordered = sequence_segments(segments)
        self.assertEqual([segment.label for segment in ordered], ["highlight", "silly", "fight"])

    def test_select_global_segments_prefers_eklipse_style_events_over_huge_fallback_scores(self) -> None:
        candidates = [
            Segment(
                start=0.0,
                end=5.0,
                score=120000.0,
                label="fight",
                note="High-action fallback window without a logged kill event.",
                source_path="/tmp/a.mp4",
            ),
            Segment(
                start=10.0,
                end=15.0,
                score=90000.0,
                label="fight",
                note="High-action fallback window without a logged kill event.",
                source_path="/tmp/b.mp4",
            ),
            Segment(
                start=20.0,
                end=25.0,
                score=6.2,
                label="highlight",
                note="SteelSeries multi-kill sequence.",
                source_path="/tmp/c.mp4",
            ),
            Segment(
                start=30.0,
                end=35.0,
                score=5.8,
                label="highlight",
                note="SteelSeries multi-kill sequence.",
                source_path="/tmp/d.mp4",
            ),
        ]
        selected = select_global_segments(candidates, target_seconds=14.0, intro_seconds=2.4)
        self.assertEqual([candidate_tier(segment) for segment in selected[:2]], [6, 6])
        self.assertTrue(all("SteelSeries" in segment.note for segment in selected[:2]))

    def test_suppresses_duplicate_steelseries_captures_from_same_moment(self) -> None:
        candidates = [
            Segment(
                start=25.0,
                end=32.5,
                score=43.0,
                label="highlight",
                note="SteelSeries multi-kill sequence.",
                source_path="C:/Videos/SteelSeries Moments/Overwatch__2026-03-08__14-27-37.mp4",
                highlight_time=55.5,
            ),
            Segment(
                start=26.0,
                end=33.5,
                score=42.0,
                label="highlight",
                note="SteelSeries multi-kill sequence.",
                source_path="C:/Videos/SteelSeries Moments/Overwatch__2026-03-08__14-27-32.mp4",
                highlight_time=58.8,
            ),
            Segment(
                start=10.0,
                end=15.0,
                score=20.0,
                label="highlight",
                note="SteelSeries multi-kill sequence.",
                source_path="C:/Videos/SteelSeries Moments/Overwatch__2026-03-08__15-40-58.mp4",
                highlight_time=12.0,
            ),
        ]

        selected = select_global_segments(candidates, target_seconds=20.0, intro_seconds=2.4)

        self.assertEqual(len(selected), 2)
        self.assertEqual(selected[0].source_path, "C:/Videos/SteelSeries Moments/Overwatch__2026-03-08__14-27-37.mp4")
        self.assertEqual(selected[1].source_path, "C:/Videos/SteelSeries Moments/Overwatch__2026-03-08__15-40-58.mp4")
        self.assertTrue(cross_capture_duplicate(candidates[0], candidates[1]))

    def test_allows_multiple_distinct_clips_from_the_same_video(self) -> None:
        candidates = [
            Segment(
                start=5.0,
                end=10.0,
                score=20.0,
                label="highlight",
                note="SteelSeries multi-kill sequence.",
                source_path="C:/Videos/SteelSeries Moments/Overwatch__2026-03-08__14-27-37.mp4",
                highlight_time=8.0,
            ),
            Segment(
                start=30.0,
                end=35.0,
                score=19.0,
                label="highlight",
                note="SteelSeries multi-kill sequence.",
                source_path="C:/Videos/SteelSeries Moments/Overwatch__2026-03-08__14-27-37.mp4",
                highlight_time=33.0,
            ),
        ]

        selected = select_global_segments(candidates, target_seconds=14.0, intro_seconds=0.0)

        self.assertEqual(selected, candidates)
        self.assertFalse(cross_capture_duplicate(candidates[0], candidates[1]))

    def test_does_not_fill_budget_with_weak_generic_filler(self) -> None:
        candidates = [
            Segment(0.0, 5.0, 15.0, "highlight", "Generic kill-heavy peak window.", "/tmp/a.mp4"),
            Segment(10.0, 15.0, 13.0, "highlight", "Generic kill-heavy peak window.", "/tmp/b.mp4"),
            Segment(20.0, 25.0, 10.0, "highlight", "Generic kill-heavy peak window.", "/tmp/c.mp4"),
            Segment(30.0, 35.0, 8.0, "highlight", "Generic kill-heavy peak window.", "/tmp/d.mp4"),
        ]

        selected = select_global_segments(candidates, target_seconds=24.0, intro_seconds=0.0)

        self.assertEqual(selected, candidates[:2])

    def test_suppresses_same_rolling_capture_cluster_even_when_event_anchors_drift(self) -> None:
        candidates = [
            Segment(
                start=50.8,
                end=58.3,
                score=43.0,
                label="highlight",
                note="SteelSeries multi-kill sequence.",
                source_path="C:/Videos/SteelSeries Moments/Overwatch__2026-03-08__14-27-37.mp4",
                highlight_time=55.5,
            ),
            Segment(
                start=52.7,
                end=58.8,
                score=25.5,
                label="highlight",
                note="SteelSeries multi-kill sequence.",
                source_path="C:/Videos/SteelSeries Moments/Overwatch__2026-03-08__14-27-13.mp4",
                highlight_time=57.5,
            ),
            Segment(
                start=31.3,
                end=38.8,
                score=13.6,
                label="highlight",
                note="SteelSeries multi-kill sequence.",
                source_path="C:/Videos/SteelSeries Moments/Overwatch__2026-03-08__15-47-42.mp4",
                highlight_time=36.1,
            ),
        ]

        selected = select_global_segments(candidates, target_seconds=20.0, intro_seconds=2.4)

        self.assertEqual(len(selected), 2)
        self.assertEqual(selected[0].source_path, "C:/Videos/SteelSeries Moments/Overwatch__2026-03-08__14-27-37.mp4")
        self.assertEqual(selected[1].source_path, "C:/Videos/SteelSeries Moments/Overwatch__2026-03-08__15-47-42.mp4")
        self.assertTrue(cross_capture_duplicate(candidates[0], candidates[1]))

    def test_skips_known_training_range_source_patterns(self) -> None:
        training_clip = Segment(
            start=30.0,
            end=37.5,
            score=99.0,
            label="highlight",
            note="SteelSeries multi-kill sequence.",
            source_path="C:/Videos/SteelSeries Moments/Overwatch__2026-03-06__20-25-24.mp4",
            highlight_time=35.0,
        )
        match_clip = Segment(
            start=10.0,
            end=17.5,
            score=8.0,
            label="highlight",
            note="SteelSeries multi-kill sequence.",
            source_path="C:/Videos/SteelSeries Moments/Overwatch__2026-03-08__14-54-56.mp4",
            highlight_time=15.0,
        )

        selected = select_global_segments([training_clip, match_clip], target_seconds=12.0, intro_seconds=2.4)

        self.assertTrue(is_training_or_practice_candidate(training_clip))
        self.assertFalse(is_training_or_practice_candidate(match_clip))
        self.assertEqual(selected, [match_clip])

        self.assertTrue(
            is_training_or_practice_candidate(
                Segment(0.0, 5.0, 1.0, "highlight", source_path="Overwatch__2026-03-06__19-25-01.mp4")
            )
        )
        self.assertTrue(
            is_training_or_practice_candidate(
                Segment(0.0, 5.0, 1.0, "highlight", source_path="Overwatch__2026-03-06__20-53-57.mp4")
            )
        )
        self.assertFalse(
            is_training_or_practice_candidate(
                Segment(0.0, 5.0, 1.0, "highlight", source_path="Overwatch__2026-03-06__21-01-06.mp4")
            )
        )

    def test_skips_training_range_notes_even_when_score_is_high(self) -> None:
        training_clip = Segment(
            start=0.0,
            end=7.5,
            score=500.0,
            label="fight",
            note="Deprioritized high-density kill stream; possible repeated practice/training clip.",
            source_path="C:/Videos/SteelSeries Moments/Overwatch__2026-01-01__10-00-00.mp4",
        )
        match_clip = Segment(
            start=10.0,
            end=17.5,
            score=5.0,
            label="highlight",
            note="SteelSeries multi-kill sequence.",
            source_path="C:/Videos/SteelSeries Moments/Overwatch__2026-03-08__14-54-56.mp4",
        )

        selected = select_global_segments([training_clip, match_clip], target_seconds=12.0, intro_seconds=2.4)

        self.assertTrue(is_training_or_practice_candidate(training_clip))
        self.assertEqual(selected, [match_clip])


if __name__ == "__main__":
    unittest.main()
