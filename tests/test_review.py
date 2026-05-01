import tempfile
import unittest
from pathlib import Path

from automakeclip.inputs import looks_like_non_gameplay_source
from automakeclip.review import (
    ReviewClip,
    ReviewState,
    _load_current_render_payload,
    _load_labels,
    _review_html,
    _review_clip_thinking,
    _select_finish_segments,
    _select_review_clips_for_finish,
    _store_labels,
)
from automakeclip.selection import candidate_tier
from automakeclip.types import MontagePlan, Segment


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
            self.assertEqual(payload["clip_edits"], {})
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

    def test_review_ui_keeps_feedback_on_current_clip(self) -> None:
        html = _review_html()
        self.assertIn("async function loadSession(options = {})", html)
        self.assertIn("await loadSession({ clipId: clip.clip_id });", html)
        self.assertIn("findIndex((clip) => clip.clip_id === options.clipId)", html)

    def test_review_ui_has_finish_action(self) -> None:
        html = _review_html()
        self.assertIn("Finish Review", html)
        self.assertIn("async function finishReview()", html)
        self.assertIn("await fetch('/api/finish'", html)
        self.assertIn("renderFinishStatus", html)

    def test_review_ui_has_editor_timeline_and_precise_trim_controls(self) -> None:
        html = _review_html()
        self.assertIn("AutoMakeClip Editor", html)
        self.assertIn('id="timelineTrack"', html)
        self.assertIn('id="trimIn"', html)
        self.assertIn('id="trimOut"', html)
        self.assertIn("async function saveClipEdit()", html)
        self.assertIn("await fetch('/api/clip-edit'", html)

    def test_review_ui_auto_refreshes_latest_render_status(self) -> None:
        html = _review_html()
        self.assertIn("async function refreshFinishStatus()", html)
        self.assertIn("window.setInterval(refreshFinishStatus, 5000)", html)

    def test_review_ui_shows_clip_readout_panel(self) -> None:
        html = _review_html()
        self.assertIn('id="thinkingMeta"', html)
        self.assertIn('id="thinkingReasons"', html)
        self.assertIn("function renderThinking(clip)", html)

    def test_review_clip_thinking_explains_memory_and_notes_without_claiming_neural_ml(self) -> None:
        thinking = _review_clip_thinking(
            _clip("clip-001", note="SteelSeries multi-kill sequence. Memory-boosted from prior clip notes."),
            decision="yes",
            feedback="shorter",
            session_feedback="more multi-kills",
            edit={"start": 1.25, "end": 3.75},
        )
        self.assertIn("No neural ML model", thinking["ml_status"])
        self.assertIn("prefer multi-kills", thinking["understood_notes"])
        self.assertTrue(any("Memory applied" in reason for reason in thinking["reasons"]))

    def test_finish_uses_yes_clips_when_available(self) -> None:
        clips = [
            _clip("clip-001"),
            _clip("clip-002"),
            _clip("clip-003"),
        ]
        selected = _select_review_clips_for_finish(clips, {"clip-001": "skip", "clip-002": "yes", "clip-003": "no"})
        self.assertEqual([clip.clip_id for clip in selected], ["clip-002"])

    def test_finish_without_yes_excludes_no_clips(self) -> None:
        clips = [
            _clip("clip-001"),
            _clip("clip-002"),
            _clip("clip-003"),
        ]
        selected = _select_review_clips_for_finish(clips, {"clip-001": "skip", "clip-002": "no"})
        self.assertEqual([clip.clip_id for clip in selected], ["clip-001", "clip-003"])

    def test_finish_selects_from_full_candidate_pool_not_only_review_clips(self) -> None:
        candidates = [
            _segment("/tmp/reviewed.mp4", 1.0, 4.0, 7.0),
            _segment("/tmp/unreviewed.mp4", 10.0, 13.0, 6.5),
        ]
        selected = _select_finish_segments(
            candidates,
            [_clip("clip-001", source_path="/tmp/reviewed.mp4", start=1.0, end=4.0)],
            {},
            target_seconds=12.0,
            intro_seconds=0.0,
        )
        self.assertIn("/tmp/unreviewed.mp4", {segment.source_path for segment in selected})

    def test_finish_excludes_review_rejected_candidate(self) -> None:
        candidates = [
            _segment("/tmp/rejected.mp4", 1.0, 4.0, 20.0),
            _segment("/tmp/kept.mp4", 10.0, 13.0, 6.5),
        ]
        selected = _select_finish_segments(
            candidates,
            [_clip("clip-001", source_path="/tmp/rejected.mp4", start=1.0, end=4.0)],
            {"clip-001": "no"},
            target_seconds=12.0,
            intro_seconds=0.0,
        )
        self.assertNotIn("/tmp/rejected.mp4", {segment.source_path for segment in selected})
        self.assertIn("/tmp/kept.mp4", {segment.source_path for segment in selected})

    def test_review_approved_candidates_get_top_selection_tier(self) -> None:
        selected = _select_finish_segments(
            [_segment("/tmp/approved.mp4", 1.0, 4.0, 1.0)],
            [_clip("clip-001", source_path="/tmp/approved.mp4", start=1.0, end=4.0)],
            {"clip-001": "yes"},
            target_seconds=8.0,
            intro_seconds=0.0,
        )
        self.assertEqual(candidate_tier(selected[0]), 7)

    def test_finish_uses_precise_saved_clip_trim(self) -> None:
        selected = _select_finish_segments(
            [_segment("/tmp/approved.mp4", 1.0, 8.0, 9.0)],
            [_clip("clip-001", source_path="/tmp/approved.mp4", start=1.0, end=8.0)],
            {"clip-001": "yes"},
            target_seconds=12.0,
            intro_seconds=0.0,
            clip_edits={"clip-001": {"start": 0.25, "end": 11.5}},
        )
        self.assertEqual(selected[0].start, 0.25)
        self.assertEqual(selected[0].end, 11.5)

    def test_detects_non_gameplay_source_names(self) -> None:
        self.assertTrue(looks_like_non_gameplay_source(Path("/tmp/zoom_0.mp4")))
        self.assertFalse(looks_like_non_gameplay_source(Path("/tmp/Overwatch__2026-03-06__20-04-22.mp4")))

    def test_review_session_uses_newer_global_mp4_render(self) -> None:
        from automakeclip.latest_render import record_latest_render

        with tempfile.TemporaryDirectory() as temp_root:
            import os

            root = Path(temp_root)
            previous_cwd = Path.cwd()
            try:
                os.chdir(root)
                session_dir = root / "review_sessions" / "session"
                clips_dir = session_dir / "clips"
                clips_dir.mkdir(parents=True)
                labels_path = session_dir / "labels.json"
                output_path = root / "latest-output" / "new.mp4"
                output_path.parent.mkdir()
                output_path.write_bytes(b"mp4")

                (session_dir / "finish.json").write_text(
                    '{"rendered_at": "2026-01-01T00:00:00", "output_path": "old.mp4"}',
                    encoding="utf-8",
                )
                record_latest_render(
                    MontagePlan(
                        input_paths=[Path("/tmp/input.mp4")],
                        output_path=output_path,
                        title="test",
                        subtitle="test",
                        target_seconds=10.0,
                        source_duration=10.0,
                        source_durations={"/tmp/input.mp4": 10.0},
                        segments=[_segment("/tmp/input.mp4", 1.0, 4.0, 7.0)],
                        mood="balanced",
                        target_bpm=126.0,
                    )
                )

                payload = _load_current_render_payload(
                    ReviewState(
                        session_dir=session_dir,
                        clips_dir=clips_dir,
                        clips=[],
                        candidates=[],
                        labels_path=labels_path,
                        target_seconds=10.0,
                    )
                )
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(payload["output_path"], str(output_path.resolve()))
        self.assertEqual(payload["video_url"], "/latest-render/video")


def _clip(
    clip_id: str,
    source_path: str = "/tmp/source.mp4",
    start: float = 1.0,
    end: float = 4.0,
    note: str = "test clip",
) -> ReviewClip:
    return ReviewClip(
        clip_id=clip_id,
        source_path=source_path,
        start=start,
        end=end,
        score=1.0,
        label="highlight",
        note=note,
        filename=f"{clip_id}.mp4",
        source_duration=60.0,
    )


def _segment(source_path: str, start: float, end: float, score: float) -> Segment:
    return Segment(
        start=start,
        end=end,
        score=score,
        label="highlight",
        note="test candidate",
        source_path=source_path,
        highlight_time=(start + end) / 2.0,
    )


if __name__ == "__main__":
    unittest.main()
