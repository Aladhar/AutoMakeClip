import unittest
from pathlib import Path
import tempfile

from automakeclip.review import _load_labels, _store_labels, select_review_samples
from automakeclip.types import Segment


class ReviewTests(unittest.TestCase):
    def test_select_review_samples_respects_limit_and_diversity(self) -> None:
        candidates = [
            Segment(start=0.0, end=5.0, score=9.0 - index, label="slay", note="", source_path=f"/tmp/source_{index // 2}.mp4")
            for index in range(8)
        ]
        selected = select_review_samples(candidates, 4)
        self.assertEqual(len(selected), 4)
        self.assertLessEqual(len({item.source_path for item in selected}), 4)

    def test_select_review_samples_prefers_uncertain_generic_fights(self) -> None:
        candidates = [
            Segment(start=10.0, end=15.0, score=8.8, label="slay", note="SteelSeries multi-kill sequence.", source_path="/tmp/a.mp4"),
            Segment(start=20.0, end=25.0, score=5.2, label="fight", note="Generic high-activity fight window.", source_path="/tmp/b.mp4"),
            Segment(start=30.0, end=35.0, score=5.0, label="fight", note="Generic kill-heavy peak window.", source_path="/tmp/c.mp4"),
            Segment(start=40.0, end=45.0, score=1.1, label="fight", note="SteelSeries kill event window.", source_path="/tmp/d.mp4"),
        ]
        selected = select_review_samples(candidates, 2)
        notes = [item.note for item in selected]
        self.assertTrue(any("Generic" in note for note in notes))

    def test_feedback_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            labels_path = Path(temp_root) / "labels.json"
            _store_labels(
                labels_path,
                decisions={"clip-001": "yes"},
                clip_feedback={"clip-001": "great kill confirm"},
                session_feedback="more clips like this",
            )
            payload = _load_labels(labels_path)
            self.assertEqual(payload["decisions"]["clip-001"], "yes")
            self.assertEqual(payload["clip_feedback"]["clip-001"], "great kill confirm")
            self.assertEqual(payload["session_feedback"], "more clips like this")


if __name__ == "__main__":
    unittest.main()
