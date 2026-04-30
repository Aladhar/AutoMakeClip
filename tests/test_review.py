import unittest

from automakeclip.review import select_review_samples
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


if __name__ == "__main__":
    unittest.main()
