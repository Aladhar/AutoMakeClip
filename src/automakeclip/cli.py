from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

from .config import AppConfig
from .inputs import InputResolutionError, resolve_inputs
from .types import Segment
from .types import MontagePlan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Turn a long Overwatch MP4 into a compact highlight montage.")
    parser.add_argument(
        "--input",
        required=True,
        nargs="+",
        action="append",
        help="One or more source gameplay MP4 files or folders containing MP4 files.",
    )
    parser.add_argument("--output", required=True, help="Output highlight MP4.")
    parser.add_argument("--title", default="OVERWATCH HIGHLIGHTS", help="Intro title text.")
    parser.add_argument("--subtitle", default="slay, chaos, and one goofy moment", help="Intro subtitle text.")
    parser.add_argument("--target-seconds", type=float, default=42.0, help="Target runtime for the final montage.")
    parser.add_argument("--no-music", action="store_true", help="Skip automatic music selection and mixing.")
    parser.add_argument("--no-silly", action="store_true", help="Skip the comedy/chaos bridge segment.")
    parser.add_argument("--no-cache", action="store_true", help="Disable per-video analysis cache reads and writes.")
    parser.add_argument("--keep-temp", action="store_true", help="Keep intermediate rendered files.")
    parser.add_argument("--dry-run", action="store_true", help="Analyze and write the plan without rendering.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = AppConfig()
    try:
        from .analysis import analyze_gameplay, infer_montage_profile, pick_segments
        from .cache import load_analysis_cache, store_analysis_cache
        from .ffmpeg import FFmpegError, ensure_ffmpeg, probe_video
        from .music import MusicSelectionError, select_music_track
        from .render import render_montage, snap_segments_to_beats
    except ModuleNotFoundError as error:
        print(
            f"Missing Python dependency: {error.name}. Run `python3 -m pip install -e .` first.",
            file=sys.stderr,
        )
        return 2

    input_paths = resolve_inputs(args.input, download_root=Path.cwd() / ".automakeclip_downloads")
    output_path = Path(args.output).expanduser().resolve()
    missing_paths = [path for path in input_paths if not path.exists()]
    if missing_paths:
        print(f"Input file not found: {missing_paths[0]}", file=sys.stderr)
        return 2

    try:
        ensure_ffmpeg()
        candidate_segments: List[Segment] = []
        source_durations = {}
        total_inputs = len(input_paths)
        cache_dir = Path.cwd() / config.cache.directory_name
        use_cache = config.cache.enabled and not args.no_cache

        for index, input_path in enumerate(input_paths, start=1):
            prefix = f"[{index}/{total_inputs}]"
            cached = load_analysis_cache(cache_dir, input_path, config.analysis) if use_cache else None
            if cached is not None:
                metadata, timeline = cached
                print(f"{prefix} cache hit {input_path.name}", file=sys.stderr)
            else:
                print(f"{prefix} cache miss {input_path.name}", file=sys.stderr)
                metadata = probe_video(input_path)
                timeline = analyze_gameplay(input_path, metadata, config.analysis)
                if use_cache:
                    store_analysis_cache(cache_dir, input_path, config.analysis, metadata, timeline)
            file_segments, _ = pick_segments(
                timeline,
                metadata,
                config.analysis,
                target_seconds=args.target_seconds,
                include_silly=not args.no_silly,
            )
            source_durations[str(input_path)] = metadata.duration
            for segment in file_segments:
                candidate_segments.append(
                    Segment(
                        start=segment.start,
                        end=segment.end,
                        score=segment.score,
                        label=segment.label,
                        note=segment.note,
                        source_path=str(input_path),
                    )
                )

        print(f"Analyzed {total_inputs} inputs and found {len(candidate_segments)} candidate segments.", file=sys.stderr)

        if not candidate_segments:
            raise RuntimeError("No highlight segments were detected. Try lowering the threshold or using a more action-heavy take.")

        selected = _select_global_segments(
            candidate_segments,
            target_seconds=args.target_seconds,
            intro_seconds=config.analysis.intro_seconds,
        )
        if not selected:
            raise RuntimeError("No highlight segments survived multi-clip selection.")

        mood, target_bpm = infer_montage_profile(selected)
        snapped = snap_segments_to_beats(
            _sequence_segments(selected),
            bpm=target_bpm,
            duration_by_source=source_durations,
            tolerance=config.render.beat_snap_tolerance_seconds,
        )

        plan = MontagePlan(
            input_paths=input_paths,
            output_path=output_path,
            title=args.title,
            subtitle=args.subtitle,
            target_seconds=args.target_seconds,
            source_duration=round(sum(source_durations.values()), 3),
            source_durations=source_durations,
            segments=snapped,
            mood=mood,
            target_bpm=target_bpm,
        )

        if not args.no_music:
            music_dir = output_path.parent / "music_cache"
            print(f"Selecting music for mood={plan.mood} bpm={plan.target_bpm}", file=sys.stderr)
            plan.music = select_music_track(plan.mood, plan.target_bpm, music_dir, config.music)

        if args.dry_run:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.with_suffix(".plan.json").write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
            if plan.music:
                output_path.with_suffix(".credits.txt").write_text(
                    "\n".join(
                        [
                            f"Track: {plan.music.title}",
                            f"Artist: {plan.music.artist}",
                            f"License: {plan.music.license_name}",
                            f"Source Page: {plan.music.page_url}",
                            f"Download URL: {plan.music.download_url}",
                            "",
                        ]
                    ),
                    encoding="utf-8",
                )
            print(json.dumps(plan.to_dict(), indent=2))
            return 0

        render_montage(plan=plan, analysis_config=config.analysis, render_config=config.render, keep_temp=args.keep_temp)
        print(f"Created highlight reel: {output_path}")
        return 0
    except (FFmpegError, MusicSelectionError, InputResolutionError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1


def _select_global_segments(candidates: List[Segment], target_seconds: float, intro_seconds: float) -> List[Segment]:
    budget = max(8.0, target_seconds - intro_seconds)
    selected: List[Segment] = []
    silly_used = False

    for candidate in sorted(candidates, key=lambda segment: segment.score, reverse=True):
        if candidate.label == "silly" and silly_used:
            continue
        if any(_same_source_overlap(candidate, existing) > 0.60 for existing in selected):
            continue
        if sum(item.duration for item in selected) + candidate.duration > budget + 1.5 and selected:
            continue
        selected.append(candidate)
        if candidate.label == "silly":
            silly_used = True
        if sum(item.duration for item in selected) >= budget:
            break
    return selected


def _sequence_segments(segments: List[Segment]) -> List[Segment]:
    silly_segments = [segment for segment in segments if segment.label == "silly"]
    core_segments = [segment for segment in segments if segment.label != "silly"]
    ordered = sorted(core_segments, key=lambda segment: segment.score, reverse=True)
    if silly_segments:
        insert_at = min(len(ordered), max(1, len(ordered) // 3))
        ordered.insert(insert_at, max(silly_segments, key=lambda segment: segment.score))
    return ordered


def _same_source_overlap(first: Segment, second: Segment) -> float:
    if first.source_path != second.source_path:
        return 0.0
    intersection = max(0.0, min(first.end, second.end) - max(first.start, second.start))
    if intersection <= 0.0:
        return 0.0
    return intersection / max(min(first.duration, second.duration), 1e-6)


if __name__ == "__main__":
    raise SystemExit(main())
