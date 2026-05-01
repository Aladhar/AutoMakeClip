from __future__ import annotations

from typing import Dict, List

from .types import Segment


def select_global_segments(candidates: List[Segment], target_seconds: float, intro_seconds: float) -> List[Segment]:
    budget = max(8.0, target_seconds - intro_seconds)
    selected: List[Segment] = []
    silly_used = False
    source_max_scores = _source_max_scores(candidates)

    for candidate in sorted(candidates, key=lambda segment: candidate_priority(segment, source_max_scores), reverse=True):
        if candidate.label == "silly" and silly_used:
            continue
        if any(same_source_overlap(candidate, existing) > 0.60 for existing in selected):
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


def candidate_priority(segment: Segment, source_max_scores: Dict[str, float]) -> tuple:
    return (
        candidate_tier(segment),
        normalized_source_score(segment, source_max_scores),
        segment.score,
    )


def candidate_tier(segment: Segment) -> int:
    note = (segment.note or "").lower()
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
