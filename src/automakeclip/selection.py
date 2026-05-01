from __future__ import annotations

from typing import List

from .types import Segment


def select_global_segments(candidates: List[Segment], target_seconds: float, intro_seconds: float) -> List[Segment]:
    budget = max(8.0, target_seconds - intro_seconds)
    selected: List[Segment] = []
    silly_used = False

    for candidate in sorted(candidates, key=lambda segment: segment.score, reverse=True):
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
