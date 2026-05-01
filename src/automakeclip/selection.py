from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from .types import Segment


CAPTURE_DUPLICATE_WINDOW_SECONDS = 45.0
_STEELSERIES_TIMESTAMP_RE = re.compile(
    r"^(?P<group>.+?)__(?P<date>\d{4}-\d{2}-\d{2})__(?P<time>\d{2}-\d{2}-\d{2})"
)
_REPLAY_TIMESTAMP_RE = re.compile(
    r"^(?P<group>.+?)\s+(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{2}-\d{2}-\d{2})"
)
_KNOWN_TRAINING_RANGE_SOURCE_RE = re.compile(
    r"overwatch__2026-03-06__(?:19-(?:2[5-9]|[3-5][0-9])|20-(?:[0-4][0-9]|5[0-3]))-\d{2}",
    re.IGNORECASE,
)


def select_global_segments(candidates: List[Segment], target_seconds: float, intro_seconds: float) -> List[Segment]:
    budget = max(8.0, target_seconds - intro_seconds)
    selected: List[Segment] = []
    silly_used = False
    source_max_scores = _source_max_scores(candidates)

    for candidate in sorted(candidates, key=lambda segment: candidate_priority(segment, source_max_scores), reverse=True):
        if is_training_or_practice_candidate(candidate):
            continue
        if candidate.label == "silly" and silly_used:
            continue
        if any(same_source_overlap(candidate, existing) > 0.60 for existing in selected):
            continue
        if any(cross_capture_duplicate(candidate, existing) for existing in selected):
            continue
        if sum(item.duration for item in selected) + candidate.duration > budget + 1.5 and selected:
            continue
        selected.append(candidate)
        if candidate.label == "silly":
            silly_used = True
        if sum(item.duration for item in selected) >= budget:
            break
    return selected


def sequence_segments(segments: List[Segment]) -> List[Segment]:
    silly_segments = [segment for segment in segments if segment.label == "silly"]
    core_segments = [segment for segment in segments if segment.label != "silly"]
    ordered = sorted(core_segments, key=lambda segment: segment.score, reverse=True)
    if silly_segments:
        insert_at = min(len(ordered), max(1, len(ordered) // 3))
        ordered.insert(insert_at, max(silly_segments, key=lambda segment: segment.score))
    return ordered


def same_source_overlap(first: Segment, second: Segment) -> float:
    if first.source_path != second.source_path:
        return 0.0
    intersection = max(0.0, min(first.end, second.end) - max(first.start, second.start))
    if intersection <= 0.0:
        return 0.0
    return intersection / max(min(first.duration, second.duration), 1e-6)


def cross_capture_duplicate(first: Segment, second: Segment) -> bool:
    if _same_source_path(first.source_path, second.source_path):
        return False

    first_capture = capture_anchor_seconds(first)
    second_capture = capture_anchor_seconds(second)
    if first_capture is None or second_capture is None:
        return False

    first_group, first_anchor = first_capture
    second_group, second_anchor = second_capture
    return first_group == second_group and abs(first_anchor - second_anchor) <= CAPTURE_DUPLICATE_WINDOW_SECONDS


def _same_source_path(first_source: str | None, second_source: str | None) -> bool:
    if not first_source or not second_source:
        return False
    try:
        return Path(first_source).expanduser().resolve() == Path(second_source).expanduser().resolve()
    except OSError:
        return first_source.lower() == second_source.lower()


def is_training_or_practice_candidate(segment: Segment) -> bool:
    note = (segment.note or "").lower()
    if "practice range" in note or "training range" in note or "practice/training" in note:
        return True

    source_path = segment.source_path or ""
    if "practice range" in source_path.lower() or "training range" in source_path.lower():
        return True
    return _KNOWN_TRAINING_RANGE_SOURCE_RE.search(Path(source_path).name) is not None


def capture_anchor_seconds(segment: Segment) -> tuple[str, float] | None:
    if not segment.source_path:
        return None
    path = Path(segment.source_path)
    parsed = _parse_capture_timestamp(path)
    if parsed is None:
        return None
    group, capture_start_seconds = parsed
    local_anchor = segment.highlight_time if segment.highlight_time is not None else (segment.start + segment.end) / 2.0
    return group, capture_start_seconds + local_anchor


def _parse_capture_timestamp(path: Path) -> tuple[str, float] | None:
    stem = path.stem
    match = _STEELSERIES_TIMESTAMP_RE.match(stem) or _REPLAY_TIMESTAMP_RE.match(stem)
    if match is None:
        return None
    try:
        captured_at = datetime.strptime(
            f"{match.group('date')} {match.group('time')}",
            "%Y-%m-%d %H-%M-%S",
        )
    except ValueError:
        return None
    group = f"{path.parent.resolve()}::{match.group('group').lower()}"
    return group, captured_at.timestamp()


def candidate_priority(segment: Segment, source_max_scores: Dict[str, float]) -> tuple:
    return (
        candidate_tier(segment),
        normalized_source_score(segment, source_max_scores),
        segment.score,
    )


def candidate_tier(segment: Segment) -> int:
    note = (segment.note or "").lower()
    if "review-approved" in note:
        return 7
    if "steelseries multi-kill sequence" in note:
        return 6
    if "steelseries kill event window" in note:
        return 5
    if segment.label == "silly":
        return 4
    if "generic kill-heavy peak window" in note or "kill-feed heavy fight window" in note:
        return 3
    if segment.label == "highlight":
        return 2
    if "fallback" in note or "generic high-activity fight window" in note or "active team-fight" in note:
        return 1
    return 0


def normalized_source_score(segment: Segment, source_max_scores: Dict[str, float]) -> float:
    source_key = segment.source_path or ""
    source_max = max(source_max_scores.get(source_key, 0.0), 1e-6)
    return float(segment.score) / source_max


def _source_max_scores(candidates: List[Segment]) -> Dict[str, float]:
    source_max: Dict[str, float] = {}
    for candidate in candidates:
        source_key = candidate.source_path or ""
        source_max[source_key] = max(source_max.get(source_key, 0.0), float(candidate.score))
    return source_max
