#!/usr/bin/env python3
"""Generate Experiment A plan and candidate diagnostics for vod_001.

This script enables the sustained multi-kill candidate branch only, then writes
both the selected plan and a diagnostic JSON file with candidate source metrics.
"""

import json
from pathlib import Path
from typing import Dict, List

from automakeclip.analysis import analyze_gameplay, collect_candidate_segments
from automakeclip.cache import load_analysis_cache, load_cached_analysis_entries, store_analysis_cache
from automakeclip.config import AppConfig
from automakeclip.ffmpeg import probe_video
from automakeclip.selection import (
    _generic_score_floor,
    _is_low_quality_filler,
    candidate_priority,
    cross_capture_duplicate,
    same_source_overlap,
    select_global_segments,
    _source_max_scores,
    is_training_or_practice_candidate,
)
from automakeclip.types import Segment


def candidate_status_map(candidates: List[Segment], selected: List[Segment], target_seconds: float) -> List[Dict[str, object]]:
    budget = max(8.0, target_seconds)
    status_items: List[Dict[str, object]] = []
    source_max_scores = _source_max_scores(candidates)
    generic_floor = _generic_score_floor(candidates)
    chosen: List[Segment] = []
    silly_used = False

    for candidate in sorted(candidates, key=lambda segment: candidate_priority(segment, source_max_scores), reverse=True):
        status = "selected"
        reason = ""
        if candidate in selected:
            if is_training_or_practice_candidate(candidate):
                status = "rejected"
                reason = "training_or_practice"
            elif candidate.label == "silly" and silly_used:
                status = "rejected"
                reason = "duplicate_silly"
            elif _is_low_quality_filler(candidate, chosen, generic_floor):
                status = "rejected"
                reason = "low_quality_filler"
            elif any(same_source_overlap(candidate, existing) > 0.60 for existing in chosen):
                status = "rejected"
                reason = "same_source_overlap"
            elif any(cross_capture_duplicate(candidate, existing) for existing in chosen):
                status = "rejected"
                reason = "cross_capture_duplicate"
            elif sum(item.duration for item in chosen) + candidate.duration > budget + 1.5 and chosen:
                status = "rejected"
                reason = "budget_exceeded"
            else:
                chosen.append(candidate)
                if candidate.label == "silly":
                    silly_used = True
                status = "selected"
                reason = "accepted"
        else:
            if is_training_or_practice_candidate(candidate):
                reason = "training_or_practice"
            elif candidate.label == "silly" and silly_used:
                reason = "duplicate_silly"
            elif _is_low_quality_filler(candidate, chosen, generic_floor):
                reason = "low_quality_filler"
            elif any(same_source_overlap(candidate, existing) > 0.60 for existing in chosen):
                reason = "same_source_overlap"
            elif any(cross_capture_duplicate(candidate, existing) for existing in chosen):
                reason = "cross_capture_duplicate"
            elif sum(item.duration for item in chosen) + candidate.duration > budget + 1.5 and chosen:
                reason = "budget_exceeded"
            else:
                reason = "not_selected"
            if candidate.label == "silly" and candidate not in chosen and silly_used:
                status = "rejected"
            else:
                status = "rejected"

        status_items.append(
            {
                "start": candidate.start,
                "end": candidate.end,
                "duration": candidate.duration,
                "score": candidate.score,
                "label": candidate.label,
                "note": candidate.note,
                "candidate_type": candidate.candidate_type,
                "highlight_time": candidate.highlight_time,
                "status": status,
                "reason": reason,
            }
        )
    return status_items


def write_plan(path: Path, input_path: Path, segments: List[Segment], source_duration: float) -> None:
    payload = {
        "input_paths": [str(input_path)],
        "output_path": str(path.with_suffix(".mp4")),
        "title": "OVERWATCH HIGHLIGHTS",
        "subtitle": "big plays, chaos, and one goofy moment",
        "target_seconds": 420.0,
        "source_duration": source_duration,
        "source_durations": {str(input_path): source_duration},
        "montage_seconds": round(sum(segment.duration for segment in segments), 3),
        "mood": "aggro",
        "target_bpm": 152.0,
        "segments": [segment.to_dict() for segment in segments],
        "music": None,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    source_path = Path("benchmarks/overwatch_eklipse_parity/downloads/vod.mp4").resolve()
    if not source_path.exists():
        print(f"Source VOD not found: {source_path}")
        return 1

    output_plan_path = Path("output/vod_001_experiment_a_sustained_candidates.plan.json").resolve()
    diagnostic_path = Path("output/vod_001_experiment_a_sustained_candidates.diagnostic.json").resolve()
    cache_dir = Path.cwd() / ".automakeclip_cache"

    app_config = AppConfig()
    app_config.analysis.enable_sustained_multikill_candidates = True
    analysis_config = app_config.analysis

    cache_entry = load_analysis_cache(cache_dir, source_path, analysis_config)
    if cache_entry is not None:
        metadata, timeline = cache_entry
        print("Loaded analysis timeline from cache.")
    else:
        metadata = probe_video(source_path)
        timeline = analyze_gameplay(source_path, metadata, analysis_config)
        store_analysis_cache(cache_dir, source_path, analysis_config, metadata, timeline)
        print("Generated and cached analysis timeline.")

    candidates = collect_candidate_segments(timeline, metadata, analysis_config, include_silly=False)
    selected = select_global_segments(candidates, target_seconds=420.0, intro_seconds=analysis_config.intro_seconds)

    write_plan(output_plan_path, source_path, selected, metadata.duration)

    diagnostic = {
        "source_path": str(source_path),
        "plan_path": str(output_plan_path),
        "diagnostic_path": str(diagnostic_path),
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "candidates": candidate_status_map(candidates, selected, target_seconds=420.0),
    }
    diagnostic_path.write_text(json.dumps(diagnostic, indent=2), encoding="utf-8")
    print(f"Wrote experiment plan to {output_plan_path}")
    print(f"Wrote diagnostics to {diagnostic_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
