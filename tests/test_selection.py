import unittest

from automakeclip.selection import candidate_tier, select_global_segments, sequence_segments
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


if __name__ == "__main__":
    unittest.main()
