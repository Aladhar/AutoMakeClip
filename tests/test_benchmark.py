"""Tests for the Eklipse parity benchmark comparison engine."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automakeclip.benchmark import (
    EklipseClip,
    EventGroupResult,
    ExportWindowMatch,
    IOU_THRESHOLD,
    ANCHOR_DISTANCE_THRESHOLD_SEC,
    LocalClip,
    ParityReport,
    _compute_event_group_results,
    _compute_iou,
    _fmt_time,
    _match_clips,
    generate_parity_report,
    load_eklipse_clips,
    load_local_plan,
    render_report_markdown,
)


class IoUTests(unittest.TestCase):
    """Tests for intersection-over-union calculation."""

    def test_identical_intervals(self) -> None:
        self.assertAlmostEqual(_compute_iou(10.0, 20.0, 10.0, 20.0), 1.0)

    def test_no_overlap(self) -> None:
        self.assertAlmostEqual(_compute_iou(10.0, 15.0, 20.0, 25.0), 0.0)

    def test_partial_overlap(self) -> None:
        # [10, 20] vs [15, 25] -> intersection=5, union=15
        self.assertAlmostEqual(_compute_iou(10.0, 20.0, 15.0, 25.0), 5.0 / 15.0)

    def test_contained_interval(self) -> None:
        # [10, 30] vs [15, 20] -> intersection=5, union=20
        self.assertAlmostEqual(_compute_iou(10.0, 30.0, 15.0, 20.0), 5.0 / 20.0)

    def test_zero_duration(self) -> None:
        self.assertAlmostEqual(_compute_iou(10.0, 10.0, 10.0, 10.0), 0.0)

    def test_touching_boundary(self) -> None:
        # [10, 15] vs [15, 20] -> intersection=0, union=10
        self.assertAlmostEqual(_compute_iou(10.0, 15.0, 15.0, 20.0), 0.0)


class ExactMatchTests(unittest.TestCase):
    """Tests for exact matching clips."""

    def test_exact_time_match(self) -> None:
        ec = EklipseClip(start_sec=100.0, end_sec=120.0, label="test")
        lc = LocalClip(start=100.0, end=120.0, label="test")
        matches = _match_clips([ec], [lc])
        self.assertEqual(len(matches), 1)
        self.assertTrue(matches[0].matched)
        self.assertAlmostEqual(matches[0].iou, 1.0)
        self.assertAlmostEqual(matches[0].anchor_distance, 0.0)

    def test_iou_at_threshold(self) -> None:
        # IoU exactly at threshold
        # [10, 20] vs [15, 25] -> intersection=5, union=15, IoU=0.333
        # [10, 20] vs [13, 20] -> intersection=7, union=17, IoU=0.412
        # Need to construct intervals with IoU >= 0.50
        # [10, 30] vs [15, 30] -> intersection=15, union=20, IoU=0.75
        ec = EklipseClip(start_sec=10.0, end_sec=30.0, label="test")
        lc = LocalClip(start=15.0, end=30.0, label="test")
        matches = _match_clips([ec], [lc])
        self.assertTrue(matches[0].matched)
        self.assertGreaterEqual(matches[0].iou, IOU_THRESHOLD)

    def test_iou_below_threshold(self) -> None:
        # [10, 20] vs [50, 60] -> no overlap, no anchor match
        ec = EklipseClip(start_sec=10.0, end_sec=20.0, label="test")
        lc = LocalClip(start=50.0, end=60.0, label="test")
        matches = _match_clips([ec], [lc])
        self.assertFalse(matches[0].matched)

    def test_exact_start_end(self) -> None:
        ec = EklipseClip(start_sec=300.0, end_sec=315.0, event_type="multikill")
        lc = LocalClip(start=300.0, end=315.0, score=0.9)
        matches = _match_clips([ec], [lc])
        self.assertTrue(matches[0].matched)
        self.assertAlmostEqual(matches[0].iou, 1.0)


class SlightlyOffsetMatchTests(unittest.TestCase):
    """Tests for clips with small timing offsets."""

    def test_anchor_within_threshold(self) -> None:
        # ec anchor = 110.0, lc anchor = 110.5 -> distance = 0.5 <= 2.0
        ec = EklipseClip(start_sec=100.0, end_sec=120.0)
        lc = LocalClip(start=100.5, end=120.5)
        matches = _match_clips([ec], [lc])
        self.assertTrue(matches[0].matched)
        self.assertAlmostEqual(matches[0].anchor_distance, 0.5)

    def test_anchor_at_threshold(self) -> None:
        # ec anchor = 110.0, lc anchor = 112.0 -> distance = 2.0 <= 2.0
        ec = EklipseClip(start_sec=100.0, end_sec=120.0)
        lc = LocalClip(start=102.0, end=122.0)
        matches = _match_clips([ec], [lc])
        self.assertTrue(matches[0].matched)
        self.assertAlmostEqual(matches[0].anchor_distance, 2.0)

    def test_anchor_just_beyond_threshold(self) -> None:
        # Use non-overlapping intervals so IoU is also below threshold
        # ec anchor = 110.0, lc anchor = 114.0 -> distance = 4.0 > 2.0
        # IoU: intersection=[112,112]=0, so IoU=0 < 0.50
        ec = EklipseClip(start_sec=100.0, end_sec=112.0)
        lc = LocalClip(start=116.0, end=112.0)
        matches = _match_clips([ec], [lc])
        self.assertFalse(matches[0].matched)

    def test_highlight_time_anchor(self) -> None:
        # local clip has highlight_time that matches eklipse anchor
        ec = EklipseClip(start_sec=100.0, end_sec=120.0)  # anchor = 110.0
        lc = LocalClip(start=95.0, end=125.0, highlight_time=110.0)  # anchor = 110.0
        matches = _match_clips([ec], [lc])
        self.assertTrue(matches[0].matched)
        self.assertAlmostEqual(matches[0].anchor_distance, 0.0)


class MissedClipTests(unittest.TestCase):
    """Tests for missed Eklipse clips."""

    def test_no_local_clips(self) -> None:
        ec = EklipseClip(start_sec=10.0, end_sec=20.0)
        matches = _match_clips([ec], [])
        self.assertEqual(len(matches), 1)
        self.assertFalse(matches[0].matched)
        self.assertIsNone(matches[0].local_clip)

    def test_no_matching_local_clips(self) -> None:
        ec1 = EklipseClip(start_sec=10.0, end_sec=20.0)
        ec2 = EklipseClip(start_sec=100.0, end_sec=120.0)
        lc = LocalClip(start=500.0, end=520.0)
        matches = _match_clips([ec1, ec2], [lc])
        self.assertEqual(len(matches), 2)
        self.assertFalse(matches[0].matched)
        self.assertFalse(matches[1].matched)


class ExtraClipTests(unittest.TestCase):
    """Tests for extra AutoMakeClip clips not in Eklipse."""

    def test_extra_local_clips_in_report(self) -> None:
        ec = EklipseClip(start_sec=10.0, end_sec=20.0, label="good")
        lc1 = LocalClip(start=10.0, end=20.0, label="good", score=0.9)
        lc2 = LocalClip(start=100.0, end=120.0, label="extra", score=0.5)
        report = generate_parity_report([ec], [lc1, lc2])
        self.assertEqual(report.extra_clips, 1)
        self.assertEqual(len(report.extra_locals), 1)
        self.assertEqual(report.extra_locals[0].label, "extra")


class TimingErrorTests(unittest.TestCase):
    """Tests for timing error calculation."""

    def test_start_time_error(self) -> None:
        ec = EklipseClip(start_sec=100.0, end_sec=120.0)
        lc = LocalClip(start=102.0, end=120.0)
        report = generate_parity_report([ec], [lc])
        self.assertEqual(report.mean_start_error, 2.0)
        self.assertEqual(report.start_errors, [-2.0])

    def test_end_time_error(self) -> None:
        ec = EklipseClip(start_sec=100.0, end_sec=120.0)
        lc = LocalClip(start=100.0, end=118.0)
        report = generate_parity_report([ec], [lc])
        # error = eklipse.end - local.end = 120 - 118 = 2.0
        self.assertEqual(report.mean_end_error, 2.0)
        self.assertEqual(report.end_errors, [2.0])

    def test_bidirectional_errors(self) -> None:
        ec1 = EklipseClip(start_sec=100.0, end_sec=120.0)
        ec2 = EklipseClip(start_sec=200.0, end_sec=220.0)
        lc1 = LocalClip(start=103.0, end=120.0)  # start late by 3
        lc2 = LocalClip(start=200.0, end=217.0)  # end early by 3
        report = generate_parity_report([ec1, ec2], [lc1, lc2])
        self.assertAlmostEqual(report.mean_start_error, 1.5)
        self.assertAlmostEqual(report.mean_end_error, 1.5)

    def test_no_matched_clips_no_timing_error(self) -> None:
        ec = EklipseClip(start_sec=10.0, end_sec=20.0)
        lc = LocalClip(start=100.0, end=120.0)  # No match
        report = generate_parity_report([ec], [lc])
        self.assertEqual(report.mean_start_error, 0.0)
        self.assertEqual(report.mean_end_error, 0.0)


class DuplicateMatchingPreventionTests(unittest.TestCase):
    """Tests that one local clip cannot satisfy multiple Eklipse clips."""

    def test_one_local_matches_only_one_eklipse(self) -> None:
        ec1 = EklipseClip(start_sec=100.0, end_sec=120.0, label="event_a")
        ec2 = EklipseClip(start_sec=100.0, end_sec=120.0, label="event_b")
        lc = LocalClip(start=100.0, end=120.0, label="detected")
        matches = _match_clips([ec1, ec2], [lc])
        matched_count = sum(1 for m in matches if m.matched)
        self.assertEqual(matched_count, 1)

    def test_two_locals_match_two_eklipse_separately(self) -> None:
        ec1 = EklipseClip(start_sec=100.0, end_sec=120.0, label="event_a")
        ec2 = EklipseClip(start_sec=200.0, end_sec=220.0, label="event_b")
        lc1 = LocalClip(start=100.0, end=120.0, label="detected_a")
        lc2 = LocalClip(start=200.0, end=220.0, label="detected_b")
        matches = _match_clips([ec1, ec2], [lc1, lc2])
        matched_count = sum(1 for m in matches if m.matched)
        self.assertEqual(matched_count, 2)
        # Each Eklipse clip matches a different local clip
        self.assertEqual(matches[0].local_clip.label, "detected_a")
        self.assertEqual(matches[1].local_clip.label, "detected_b")


class EventGroupTests(unittest.TestCase):
    """Tests for unique-event-group comparison with overlapping exports."""

    def test_overlapping_exports_same_group_counted_once(self) -> None:
        """Four overlapping sextuple_kill exports in same group = one detected event."""
        ec1 = EklipseClip(start_sec=25.0, end_sec=35.0, event_type="sextuple_kill", event_group="sextuple_001")
        ec2 = EklipseClip(start_sec=28.0, end_sec=38.0, event_type="sextuple_kill", event_group="sextuple_001")
        ec3 = EklipseClip(start_sec=31.0, end_sec=41.0, event_type="sextuple_kill", event_group="sextuple_001")
        ec4 = EklipseClip(start_sec=34.0, end_sec=44.0, event_type="sextuple_kill", event_group="sextuple_001")
        lc = LocalClip(start=27.0, end=40.0, label="multikill")
        report = generate_parity_report([ec1, ec2, ec3, ec4], [lc])

        # Export-window: at most 1 can match (one local clip, greedy matching)
        self.assertGreaterEqual(report.matched_clips, 1)
        # Event-group: only 1 unique group, and it is matched
        self.assertEqual(report.unique_event_group_count, 1)
        self.assertEqual(report.matched_event_groups, 1)
        self.assertAlmostEqual(report.unique_event_recall, 1.0)

    def test_multiple_groups_partial_match(self) -> None:
        ec1 = EklipseClip(start_sec=10.0, end_sec=20.0, event_type="kill", event_group="grp_a")
        ec2 = EklipseClip(start_sec=100.0, end_sec=120.0, event_type="ult", event_group="grp_b")
        ec3 = EklipseClip(start_sec=300.0, end_sec=320.0, event_type="clutch", event_group="grp_c")
        lc = LocalClip(start=10.0, end=20.0, label="kill")  # matches grp_a only
        report = generate_parity_report([ec1, ec2, ec3], [lc])
        self.assertEqual(report.unique_event_group_count, 3)
        self.assertEqual(report.matched_event_groups, 1)
        self.assertEqual(report.missed_event_groups, 2)
        self.assertAlmostEqual(report.unique_event_recall, 1.0 / 3.0)

    def test_all_groups_matched(self) -> None:
        ec1 = EklipseClip(start_sec=10.0, end_sec=20.0, event_group="a")
        ec2 = EklipseClip(start_sec=100.0, end_sec=120.0, event_group="b")
        lc1 = LocalClip(start=10.0, end=20.0)
        lc2 = LocalClip(start=100.0, end=120.0)
        report = generate_parity_report([ec1, ec2], [lc1, lc2])
        self.assertAlmostEqual(report.unique_event_recall, 1.0)

    def test_no_groups_missed(self) -> None:
        ec1 = EklipseClip(start_sec=10.0, end_sec=20.0, event_group="a")
        report = generate_parity_report([ec1], [])
        self.assertEqual(report.unique_event_group_count, 1)
        self.assertEqual(report.matched_event_groups, 0)
        self.assertEqual(report.missed_event_groups, 1)
        self.assertAlmostEqual(report.unique_event_recall, 0.0)

    def test_empty_event_group_treated_as_unique(self) -> None:
        """Clips with empty event_group get individual group keys."""
        ec1 = EklipseClip(start_sec=10.0, end_sec=20.0, event_group="")
        ec2 = EklipseClip(start_sec=100.0, end_sec=120.0, event_group="")
        lc = LocalClip(start=10.0, end=20.0)
        report = generate_parity_report([ec1, ec2], [lc])
        self.assertEqual(report.unique_event_group_count, 2)
        self.assertEqual(report.matched_event_groups, 1)


class NullRankTests(unittest.TestCase):
    """Tests for unknown/null rank handling."""

    def test_all_null_ranks(self) -> None:
        ec1 = EklipseClip(start_sec=10.0, end_sec=20.0, rank=None)
        ec2 = EklipseClip(start_sec=100.0, end_sec=120.0, rank=None)
        lc1 = LocalClip(start=10.0, end=20.0, score=0.9)
        lc2 = LocalClip(start=100.0, end=120.0, score=0.7)
        report = generate_parity_report([ec1, ec2], [lc1, lc2])
        self.assertFalse(report.rank_available)
        self.assertEqual(report.rank_agreement_count, 0)
        self.assertEqual(report.rank_agreement_total, 0)

    def test_some_null_ranks(self) -> None:
        ec1 = EklipseClip(start_sec=10.0, end_sec=20.0, rank=1)
        ec2 = EklipseClip(start_sec=100.0, end_sec=120.0, rank=None)
        report = generate_parity_report([ec1, ec2], [])
        # At least one rank is present
        self.assertTrue(report.rank_available)
        # But only 1 matched clip with rank, so no pairwise comparison possible
        self.assertEqual(report.rank_agreement_total, 0)

    def test_rank_agreement_with_two_clips(self) -> None:
        ec1 = EklipseClip(start_sec=10.0, end_sec=20.0, rank=1)
        ec2 = EklipseClip(start_sec=100.0, end_sec=120.0, rank=2)
        lc1 = LocalClip(start=10.0, end=20.0, score=0.9)  # should be ranked higher
        lc2 = LocalClip(start=100.0, end=120.0, score=0.5)  # should be ranked lower
        report = generate_parity_report([ec1, ec2], [lc1, lc2])
        self.assertTrue(report.rank_available)
        self.assertEqual(report.rank_agreement_total, 1)
        self.assertEqual(report.rank_agreement_count, 1)

    def test_rank_disagreement(self) -> None:
        ec1 = EklipseClip(start_sec=10.0, end_sec=20.0, rank=1)
        ec2 = EklipseClip(start_sec=100.0, end_sec=120.0, rank=2)
        # Local assigns higher score to the clip Eklipse ranked lower
        lc1 = LocalClip(start=10.0, end=20.0, score=0.3)
        lc2 = LocalClip(start=100.0, end=120.0, score=0.9)
        report = generate_parity_report([ec1, ec2], [lc1, lc2])
        self.assertTrue(report.rank_available)
        self.assertEqual(report.rank_agreement_total, 1)
        self.assertEqual(report.rank_agreement_count, 0)


class RecallPrecisionTests(unittest.TestCase):
    """Tests for recall and precision calculation."""

    def test_perfect_recall_and_precision(self) -> None:
        ec = EklipseClip(start_sec=10.0, end_sec=20.0)
        lc = LocalClip(start=10.0, end=20.0)
        report = generate_parity_report([ec], [lc])
        self.assertAlmostEqual(report.export_recall, 1.0)
        self.assertAlmostEqual(report.export_precision, 1.0)

    def test_zero_recall(self) -> None:
        ec = EklipseClip(start_sec=10.0, end_sec=20.0)
        lc = LocalClip(start=500.0, end=520.0)
        report = generate_parity_report([ec], [lc])
        self.assertAlmostEqual(report.export_recall, 0.0)

    def test_zero_precision(self) -> None:
        ec = EklipseClip(start_sec=10.0, end_sec=20.0)
        lc = LocalClip(start=500.0, end=520.0)
        report = generate_parity_report([ec], [lc])
        # Local clip is not matched, so 0 matched / 1 local = 0.0
        self.assertAlmostEqual(report.export_precision, 0.0)

    def test_partial_recall(self) -> None:
        ec1 = EklipseClip(start_sec=10.0, end_sec=20.0)
        ec2 = EklipseClip(start_sec=100.0, end_sec=120.0)
        lc = LocalClip(start=10.0, end=20.0)
        report = generate_parity_report([ec1, ec2], [lc])
        self.assertAlmostEqual(report.export_recall, 0.5)

    def test_empty_eklipse(self) -> None:
        lc = LocalClip(start=10.0, end=20.0)
        report = generate_parity_report([], [lc])
        self.assertAlmostEqual(report.export_recall, 0.0)
        self.assertAlmostEqual(report.export_precision, 0.0)


class LoaderTests(unittest.TestCase):
    """Tests for JSON loading functions."""

    def test_load_eklipse_list_format(self) -> None:
        data = [
            {"start_sec": 10.0, "end_sec": 20.0, "label": "a"},
            {"start_sec": 30.0, "end_sec": 40.0, "label": "b"},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        try:
            clips = load_eklipse_clips(path)
            self.assertEqual(len(clips), 2)
            self.assertAlmostEqual(clips[0].start_sec, 10.0)
            self.assertEqual(clips[1].label, "b")
        finally:
            path.unlink()

    def test_load_eklipse_wrapper_format(self) -> None:
        data = {
            "vod_id": "test",
            "clips": [
                {"start_sec": 100.0, "end_sec": 120.0, "event_type": "kill", "rank": 1},
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        try:
            clips = load_eklipse_clips(path)
            self.assertEqual(len(clips), 1)
            self.assertEqual(clips[0].rank, 1)
            self.assertEqual(clips[0].event_type, "kill")
        finally:
            path.unlink()

    def test_load_eklipse_with_event_group(self) -> None:
        data = [
            {"start_sec": 10.0, "end_sec": 20.0, "event_group": "sextuple_001"},
            {"start_sec": 12.0, "end_sec": 22.0, "event_group": "sextuple_001"},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        try:
            clips = load_eklipse_clips(path)
            self.assertEqual(clips[0].event_group, "sextuple_001")
            self.assertEqual(clips[1].event_group, "sextuple_001")
        finally:
            path.unlink()

    def test_load_local_plan_dict_format(self) -> None:
        data = {
            "segments": [
                {"start": 100.0, "end": 120.0, "score": 0.85, "label": "multikill"},
                {"start": 200.0, "end": 220.0, "score": 0.70, "label": "teamfight"},
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        try:
            clips = load_local_plan(path)
            self.assertEqual(len(clips), 2)
            self.assertAlmostEqual(clips[0].score, 0.85)
            self.assertEqual(clips[1].label, "teamfight")
        finally:
            path.unlink()

    def test_load_local_plan_list_format(self) -> None:
        data = [
            {"start": 50.0, "end": 60.0, "score": 0.9},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        try:
            clips = load_local_plan(path)
            self.assertEqual(len(clips), 1)
        finally:
            path.unlink()

    def test_load_local_plan_with_highlight_time(self) -> None:
        data = {"segments": [{"start": 10.0, "end": 20.0, "highlight_time": 15.0}]}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        try:
            clips = load_local_plan(path)
            self.assertEqual(clips[0].highlight_time, 15.0)
        finally:
            path.unlink()


class TimeFormatTests(unittest.TestCase):
    """Tests for time formatting."""

    def test_zero_seconds(self) -> None:
        self.assertEqual(_fmt_time(0.0), "00:00:00")

    def test_minutes_only(self) -> None:
        self.assertEqual(_fmt_time(125.0), "00:02:05")

    def test_hours_minutes_seconds(self) -> None:
        self.assertEqual(_fmt_time(4719.0), "01:18:39")

    def test_exact_hour(self) -> None:
        self.assertEqual(_fmt_time(3600.0), "01:00:00")


class MarkdownReportTests(unittest.TestCase):
    """Tests for markdown report generation."""

    def test_report_contains_required_sections(self) -> None:
        ec = EklipseClip(start_sec=10.0, end_sec=20.0, label="test")
        lc = LocalClip(start=10.0, end=20.0, label="test")
        report = generate_parity_report([ec], [lc], vod_id="test_vod")
        md = render_report_markdown(report)

        self.assertIn("Parity Report: test_vod", md)
        self.assertIn("Export-Window Comparison", md)
        self.assertIn("Unique-Event-Group Comparison", md)
        self.assertIn("Rank Agreement", md)
        self.assertIn("Matched Clip Details", md)
        self.assertIn("Event-Group Details", md)

    def test_report_shows_recall_precision(self) -> None:
        ec = EklipseClip(start_sec=10.0, end_sec=20.0)
        lc = LocalClip(start=10.0, end=20.0)
        report = generate_parity_report([ec], [lc])
        md = render_report_markdown(report)
        self.assertIn("100.0%", md)

    def test_report_shows_rank_omitted_when_null(self) -> None:
        ec = EklipseClip(start_sec=10.0, end_sec=20.0, rank=None)
        lc = LocalClip(start=10.0, end=20.0)
        report = generate_parity_report([ec], [lc])
        md = render_report_markdown(report)
        self.assertIn("Rank values not present", md)

    def test_report_shows_missed_clips(self) -> None:
        ec = EklipseClip(start_sec=10.0, end_sec=20.0, label="missed_event", notes="important")
        lc = LocalClip(start=500.0, end=520.0)
        report = generate_parity_report([ec], [lc])
        md = render_report_markdown(report)
        self.assertIn("Missed Eklipse Exports", md)
        self.assertIn("missed_event", md)
        self.assertIn("important", md)

    def test_report_shows_extra_clips(self) -> None:
        ec = EklipseClip(start_sec=10.0, end_sec=20.0)
        lc = LocalClip(start=500.0, end=520.0, label="false_positive")
        report = generate_parity_report([ec], [lc])
        md = render_report_markdown(report)
        self.assertIn("Extra AutoMakeClip Clips", md)
        self.assertIn("false_positive", md)

    def test_report_event_group_table(self) -> None:
        ec1 = EklipseClip(start_sec=10.0, end_sec=20.0, event_type="kill", event_group="g1")
        ec2 = EklipseClip(start_sec=100.0, end_sec=120.0, event_type="ult", event_group="g2")
        lc = LocalClip(start=10.0, end=20.0)
        report = generate_parity_report([ec1, ec2], [lc])
        md = render_report_markdown(report)
        self.assertIn("g1", md)
        self.assertIn("g2", md)

    def test_empty_report(self) -> None:
        report = generate_parity_report([], [], vod_id="empty")
        md = render_report_markdown(report)
        self.assertIn("Parity Report: empty", md)
        self.assertIn("Eklipse exported clip count | 0", md)


class IntegrationTests(unittest.TestCase):
    """End-to-end integration tests with temporary JSON files."""

    def test_full_round_trip(self) -> None:
        eklipse_data = [
            {"start_sec": 10.0, "end_sec": 25.0, "event_type": "kill", "event_group": "grp1", "rank": 1, "label": "First kill"},
            {"start_sec": 50.0, "end_sec": 65.0, "event_type": "multikill", "event_group": "grp2", "rank": 2, "label": "Multi kill"},
            {"start_sec": 100.0, "end_sec": 115.0, "event_type": "clutch", "event_group": "grp3", "rank": None, "label": "Clutch play"},
        ]
        local_data = {
            "segments": [
                {"start": 11.0, "end": 26.0, "score": 0.95, "label": "detected_kill"},
                {"start": 52.0, "end": 67.0, "score": 0.80, "label": "detected_multi"},
                {"start": 200.0, "end": 220.0, "score": 0.60, "label": "extra_filler"},
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            eklipse_path = Path(tmpdir) / "ref.json"
            local_path = Path(tmpdir) / "plan.json"
            report_path = Path(tmpdir) / "report.md"

            eklipse_path.write_text(json.dumps(eklipse_data))
            local_path.write_text(json.dumps(local_data))

            eklipse_clips = load_eklipse_clips(eklipse_path)
            local_clips = load_local_plan(local_path)
            report = generate_parity_report(
                eklipse_clips, local_clips,
                vod_id="integration_test",
                eklipse_file=str(eklipse_path),
                local_file=str(local_path),
            )

            # Export-window metrics
            self.assertEqual(report.eklipse_clip_count, 3)
            self.assertEqual(report.local_clip_count, 3)
            self.assertGreaterEqual(report.matched_clips, 2)
            self.assertLessEqual(report.missed_clips, 1)
            self.assertEqual(report.extra_clips, 1)

            # Event-group metrics
            self.assertEqual(report.unique_event_group_count, 3)

            # Write and verify report
            md = render_report_markdown(report)
            report_path.write_text(md)
            self.assertTrue(report_path.exists())
            self.assertIn("Parity Report: integration_test", report_path.read_text())


if __name__ == "__main__":
    unittest.main()