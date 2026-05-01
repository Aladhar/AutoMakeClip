import tempfile
import unittest
from pathlib import Path

from automakeclip.inputs import looks_like_non_gameplay_source
from automakeclip.review import _load_labels, _review_html, _store_labels


class ReviewTests(unittest.TestCase):
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

    def test_review_ui_uses_arrow_only_navigation_shortcuts(self) -> None:
        html = _review_html()
        self.assertIn("event.key === 'ArrowLeft'", html)
        self.assertIn("event.key === 'ArrowRight'", html)
        self.assertNotIn("event.key === 'y'", html)
        self.assertNotIn("event.key === 'n'", html)
        self.assertNotIn("event.key === 's'", html)

    def test_review_ui_no_longer_shows_uncertainty_buckets(self) -> None:
        html = _review_html()
        self.assertNotIn("uncertainty", html)
        self.assertNotIn("review_bucket", html)

    def test_detects_non_gameplay_source_names(self) -> None:
        self.assertTrue(looks_like_non_gameplay_source(Path("/tmp/zoom_0.mp4")))
        self.assertFalse(looks_like_non_gameplay_source(Path("/tmp/Overwatch__2026-03-06__20-04-22.mp4")))


if __name__ == "__main__":
    unittest.main()
