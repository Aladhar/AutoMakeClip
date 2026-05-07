from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

from .config import AppConfig
from .inputs import InputResolutionError, looks_like_non_gameplay_source, resolve_inputs
from .memory import apply_memory_to_candidates
from .selection import select_global_segments, sequence_segments
from .types import Segment
from .types import MontagePlan


def _add_candidate_segments(
    candidate_segments: List[Segment],
    source_durations: Dict[str, float],
    input_path: Path,
    metadata,
    timeline,
    config: AppConfig,
    include_silly: bool,
) -> None:
    from .analysis import collect_candidate_segments

    file_segments = collect_candidate_segments(
        timeline,
        metadata,
        config.analysis,
        include_silly=include_silly,
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
                highlight_time=segment.highlight_time,
            )
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Turn a long Overwatch MP4 into a compact highlight montage.")
    parser.add_argument(
        "--input",
        nargs="+",
        action="append",
        help="One or more source gameplay MP4 files or folders containing MP4 files.",
    )
    parser.add_argument(
        "--from-cache",
        action="store_true",
        help="Build the candidate pool from valid .automakeclip_cache entries instead of rescanning input videos.",
    )
    parser.add_argument("--output", required=True, help="Output highlight MP4.")
    parser.add_argument("--title", default="OVERWATCH HIGHLIGHTS", help="Intro title text.")
    parser.add_argument("--subtitle", default="big plays, chaos, and one goofy moment", help="Intro subtitle text.")
    parser.add_argument("--target-seconds", type=float, default=42.0, help="Target runtime for the final montage.")
    parser.add_argument("--no-music", action="store_true", help="Skip automatic music selection and mixing.")
    parser.add_argument(
        "--music-source",
        choices=("youtube", "spotify", "library", "auto", "ccmixter", "generated"),
        default=None,
        help="Choose where soundtrack music comes from. Defaults to YouTube playlist entries in the music manifest.",
    )
    parser.add_argument("--music-manifest", default=None, help="Path to the local music manifest JSON.")
    parser.add_argument(
        "--allow-generated-fallback",
        action="store_true",
        help="Only use this if you want the synthetic backup track when no real song is available.",
    )
    parser.add_argument("--music-gain", type=float, default=None, help="Override soundtrack loudness mix gain.")
    parser.add_argument("--game-audio-gain", type=float, default=None, help="Override gameplay audio mix gain.")
    parser.add_argument("--no-silly", action="store_true", help="Skip the comedy/chaos bridge segment.")
    parser.add_argument("--no-cache", action="store_true", help="Disable per-video analysis cache reads and writes.")
    parser.add_argument("--keep-temp", action="store_true", help="Keep intermediate rendered files.")
    parser.add_argument("--dry-run", action="store_true", help="Analyze and write the plan without rendering.")
    parser.add_argument(
        "--youtube-playlist-limit",
        type=int,
        default=0,
        help="When an input is a YouTube /streams page, cap how many recent stream VODs are pulled in. Use 0 for no limit.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.from_cache and not args.input:
        parser.error("--input is required unless --from-cache is used.")
    config = AppConfig()
    if args.music_source is not None:
        config.music.source = args.music_source
    if args.music_manifest is not None:
        config.music.library_manifest = args.music_manifest
    if args.allow_generated_fallback:
        config.music.allow_generated_fallback = True
    if args.music_gain is not None:
        config.render.music_gain = args.music_gain
    if args.game_audio_gain is not None:
        config.render.game_audio_gain = args.game_audio_gain
    try:
        from .analysis import analyze_gameplay, infer_montage_profile
        from .cache import load_analysis_cache, load_cached_analysis_entries, store_analysis_cache
        from .ffmpeg import FFmpegError, ensure_ffmpeg, probe_video
        from .music import MusicSelectionError, select_music_track
        from .render import render_montage, snap_segments_to_beats
    except ModuleNotFoundError as error:
        print(
            f"Missing Python dependency: {error.name}. Run `python3 -m pip install -e .` first.",
            file=sys.stderr,
        )
        return 2

    input_paths = []
    if not args.from_cache:
        input_paths = resolve_inputs(
            args.input,
            download_root=Path.cwd() / ".automakeclip_downloads",
            youtube_playlist_limit=args.youtube_playlist_limit if args.youtube_playlist_limit > 0 else None,
        )
    output_path = Path(args.output).expanduser().resolve()
    missing_paths = [path for path in input_paths if not path.exists()]
    if missing_paths:
        print(f"Input file not found: {missing_paths[0]}", file=sys.stderr)
        return 2

    try:
        ensure_ffmpeg()
        candidate_segments: List[Segment] = []
        source_durations = {}
        cache_dir = Path.cwd() / config.cache.directory_name
        use_cache = config.cache.enabled and not args.no_cache

        if args.from_cache:
            cached_entries = [
                entry
                for entry in load_cached_analysis_entries(cache_dir, config.analysis)
                if not looks_like_non_gameplay_source(entry[0])
            ]
            input_paths = [source_path for source_path, _, _ in cached_entries]
            if not cached_entries:
                raise RuntimeError("No valid analysis cache entries were found.")
            for index, (input_path, metadata, timeline) in enumerate(cached_entries, start=1):
                print(f"[{index}/{len(cached_entries)}] cache plan {input_path.name}", file=sys.stderr)
                _add_candidate_segments(
                    candidate_segments,
                    source_durations,
                    input_path,
                    metadata,
                    timeline,
                    config,
                    include_silly=not args.no_silly,
                )
        else:
            total_inputs = len(input_paths)
            for index, input_path in enumerate(input_paths, start=1):
                prefix = f"[{index}/{total_inputs}]"
                if looks_like_non_gameplay_source(input_path):
                    print(f"{prefix} skip non-gameplay source {input_path.name}", file=sys.stderr)
                    continue
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
                _add_candidate_segments(
                    candidate_segments,
                    source_durations,
                    input_path,
                    metadata,
                    timeline,
                    config,
                    include_silly=not args.no_silly,
                )

        print(f"Analyzed {len(input_paths)} inputs and found {len(candidate_segments)} candidate segments.", file=sys.stderr)

        if not candidate_segments:
            raise RuntimeError("No highlight segments were detected. Try lowering the threshold or using a more action-heavy take.")

        candidate_segments = apply_memory_to_candidates(candidate_segments, source_durations)
        selected = select_global_segments(
            candidate_segments,
            target_seconds=args.target_seconds,
            intro_seconds=config.analysis.intro_seconds,
        )
        if not selected:
            raise RuntimeError("No highlight segments survived multi-clip selection.")

        mood, target_bpm = infer_montage_profile(selected)
        snapped = snap_segments_to_beats(
            sequence_segments(selected),
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
            print(
                f"Using music: {plan.music.title} - {plan.music.artist} [{plan.music.source_kind or 'unknown'}]",
                file=sys.stderr,
            )

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

if __name__ == "__main__":
    raise SystemExit(main())
