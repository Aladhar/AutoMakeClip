import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from automakeclip.memory import (
    apply_memory_to_candidates,
    empty_review_memory,
    feedback_directives,
    load_review_memory,
    record_review_clip,
    save_review_memory,
)
from automakeclip.types import Segment


class ReviewMemoryTests(unittest.TestCase):
    def test_review_memory_round_trip_records_decision_feedback_and_trim(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            memory_path = Path(temp_root) / "review_memory.json"
            memory = empty_review_memory()
            record_review_clip(
                memory,
                _clip(
                    clip_id="clip-001",
                    source_path="/tmp/source.mp4",
                    start=10.0,
                    end=18.0,
                    note="SteelSeries multi-kill sequence.",
                ),
                decision="yes",
                feedback="more like this",
                edit={"start": 8.5, "end": 19.25},
                session_dir=Path(temp_root) / "session",
            )

            save_review_memory(memory, memory_path)
            loaded = load_review_memory(memory_path)
            entries = list(loaded["clips"].values())

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["decision"], "yes")
        self.assertEqual(entries[0]["feedback"], "more like this")
        self.assertEqual(entries[0]["feedback_directives"]["more_like_this"], True)
        self.assertEqual(entries[0]["edit"], {"start": 8.5, "end": 19.25})
        self.assertEqual(entries[0]["bucket"], "highlight:steelseries_multi_kill")

    def test_memory_boosts_accepted_buckets_and_downranks_rejected_buckets(self) -> None:
        memory = empty_review_memory()
        record_review_clip(
            memory,
            _clip("clip-001", "/tmp/liked.mp4", 1.0, 6.0, label="highlight", note="Generic kill-heavy peak window."),
            decision="yes",
        )
        record_review_clip(
            memory,
            _clip(
                "clip-002",
                "/tmp/rejected.mp4",
                1.0,
                6.0,
                label="fight",
                note="High-action fallback window without a logged kill event.",
            ),
            decision="no",
        )

        adjusted = apply_memory_to_candidates(
            [
                Segment(
                    20.0,
                    25.0,
                    10.0,
                    "fight",
                    "High-action fallback window without a logged kill event.",
                    "/tmp/fallback.mp4",
                ),
                Segment(
                    30.0,
                    35.0,
                    8.0,
                    "highlight",
                    "Generic kill-heavy peak window.",
                    "/tmp/highlight.mp4",
                ),
            ],
            memory=memory,
        )
        by_source = {segment.source_path: segment for segment in adjusted}

        self.assertLess(by_source["/tmp/fallback.mp4"].score, 10.0)
        self.assertIn("Memory-downranked", by_source["/tmp/fallback.mp4"].note)
        self.assertGreater(by_source["/tmp/highlight.mp4"].score, 8.0)
        self.assertIn("Memory-boosted", by_source["/tmp/highlight.mp4"].note)

    def test_memory_applies_learned_trim_offsets_to_similar_future_clips(self) -> None:
        memory = empty_review_memory()
        record_review_clip(
            memory,
            _clip(
                "clip-001",
                "/tmp/reviewed.mp4",
                10.0,
                18.0,
                note="SteelSeries multi-kill sequence.",
                source_duration=90.0,
            ),
            decision="yes",
            edit={"start": 8.5, "end": 20.0},
        )

        adjusted = apply_memory_to_candidates(
            [
                Segment(
                    30.0,
                    38.0,
                    12.0,
                    "highlight",
                    "SteelSeries multi-kill sequence.",
                    "/tmp/new_source.mp4",
                    highlight_time=34.0,
                )
            ],
            source_durations={"/tmp/new_source.mp4": 39.0},
            memory=memory,
        )

        self.assertEqual(adjusted[0].start, 28.5)
        self.assertEqual(adjusted[0].end, 39.0)
        self.assertEqual(adjusted[0].highlight_time, 34.0)
        self.assertIn("Memory-trimmed", adjusted[0].note)

    def test_clip_notes_can_adjust_future_trims_without_manual_edit(self) -> None:
        memory = empty_review_memory()
        record_review_clip(
            memory,
            _clip(
                "clip-001",
                "/tmp/reviewed.mp4",
                10.0,
                18.0,
                note="SteelSeries multi-kill sequence.",
                source_duration=90.0,
            ),
            feedback="too long, start later and end sooner",
        )

        adjusted = apply_memory_to_candidates(
            [
                Segment(
                    30.0,
                    38.0,
                    12.0,
                    "highlight",
                    "SteelSeries multi-kill sequence.",
                    "/tmp/new_source.mp4",
                    highlight_time=34.0,
                )
            ],
            source_durations={"/tmp/new_source.mp4": 90.0},
            memory=memory,
        )

        self.assertEqual(adjusted[0].start, 31.2)
        self.assertEqual(adjusted[0].end, 36.8)
        self.assertEqual(adjusted[0].highlight_time, 34.0)
        self.assertIn("Memory-adjusted from prior clip notes", adjusted[0].note)

    def test_session_notes_can_prefer_multikills_and_downrank_generic_filler(self) -> None:
        memory = empty_review_memory()
        record_review_clip(
            memory,
            _clip("clip-001", "/tmp/reviewed.mp4", 1.0, 6.0, note="SteelSeries multi-kill sequence."),
            session_feedback="more multi-kill clips, no generic filler or dead air",
        )

        adjusted = apply_memory_to_candidates(
            [
                Segment(
                    20.0,
                    25.0,
                    10.0,
                    "highlight",
                    "SteelSeries multi-kill sequence.",
                    "/tmp/multi.mp4",
                ),
                Segment(
                    30.0,
                    35.0,
                    10.0,
                    "highlight",
                    "Generic kill-heavy peak window.",
                    "/tmp/generic.mp4",
                ),
            ],
            memory=memory,
        )
        by_source = {segment.source_path: segment for segment in adjusted}

        self.assertGreater(by_source["/tmp/multi.mp4"].score, 10.0)
        self.assertLess(by_source["/tmp/generic.mp4"].score, 10.0)
        self.assertIn("Memory-boosted from prior clip notes", by_source["/tmp/multi.mp4"].note)
        self.assertIn("Memory-downranked from prior clip notes", by_source["/tmp/generic.mp4"].note)

    def test_feedback_directives_parse_common_editor_notes(self) -> None:
        directives = feedback_directives("too much before, more triple kills, no silly filler")

        self.assertTrue(directives["start_later"])
        self.assertTrue(directives["prefer_multi_kill"])
        self.assertTrue(directives["avoid_silly"])


def _clip(
    clip_id: str,
    source_path: str,
    start: float,
    end: float,
    label: str = "highlight",
    note: str = "test clip",
    source_duration: float = 60.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        clip_id=clip_id,
        source_path=source_path,
        start=start,
        end=end,
        score=6.0,
        label=label,
        note=note,
        filename=f"{clip_id}.mp4",
        highlight_time=(start + end) / 2.0,
        source_duration=source_duration,
    )


if __name__ == "__main__":
    unittest.main()
