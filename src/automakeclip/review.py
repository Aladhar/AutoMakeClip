from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import unquote, urlparse

from .config import AppConfig, RenderConfig
from .inputs import InputResolutionError, looks_like_non_gameplay_source, resolve_inputs
from .memory import apply_memory_to_candidates, clip_feature_bucket, feedback_directives, flush_review_session_memory, persist_review_clip
from .selection import candidate_tier, select_global_segments, sequence_segments
from .types import MontagePlan, Segment


@dataclass
class ReviewClip:
    clip_id: str
    source_path: str
    start: float
    end: float
    score: float
    label: str
    note: str
    filename: str
    highlight_time: Optional[float] = None
    source_duration: float = 0.0


@dataclass
class ReviewState:
    session_dir: Path
    clips_dir: Path
    clips: List[ReviewClip]
    candidates: List[Segment]
    labels_path: Path
    target_seconds: float


def _add_review_candidates(
    candidates: List[Segment],
    input_path: Path,
    metadata,
    timeline,
    config: AppConfig,
    target_seconds: float,
    include_silly: bool,
) -> None:
    from .analysis import pick_segments

    file_segments, _ = pick_segments(
        timeline,
        metadata,
        config.analysis,
        target_seconds=target_seconds,
        include_silly=include_silly,
    )
    for segment in file_segments:
        candidates.append(
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
    parser = argparse.ArgumentParser(description="Review the actual clips selected for the final montage.")
    parser.add_argument(
        "--input",
        nargs="+",
        action="append",
        help="Source MP4 files, folders, or YouTube URLs.",
    )
    parser.add_argument(
        "--from-cache",
        action="store_true",
        help="Build the review candidate pool from valid .automakeclip_cache entries instead of rescanning input videos.",
    )
    parser.add_argument("--samples", type=int, default=0, help="Optional cap on how many final selected clips to review.")
    parser.add_argument("--port", type=int, default=8765, help="Port for the local review UI.")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface for the local review UI.")
    parser.add_argument("--session-dir", default="", help="Optional review session directory.")
    parser.add_argument("--no-cache", action="store_true", help="Disable per-video analysis cache.")
    parser.add_argument("--target-seconds", type=float, default=42.0, help="Match the final montage target runtime.")
    parser.add_argument("--no-silly", action="store_true", help="Skip the comedy/chaos bridge segment.")
    parser.add_argument(
        "--youtube-playlist-limit",
        type=int,
        default=24,
        help="When an input is a YouTube /streams page, limit how many recent stream VODs are pulled in.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.from_cache and not args.input:
        parser.error("--input is required unless --from-cache is used.")
    config = AppConfig()
    try:
        from .analysis import analyze_gameplay
        from .cache import load_analysis_cache, load_cached_analysis_entries, store_analysis_cache
        from .ffmpeg import FFmpegError, ensure_ffmpeg, probe_video, run_ffmpeg
    except ModuleNotFoundError as error:
        print(
            f"Missing Python dependency: {error.name}. Run `python3 -m pip install -e .` first.",
            file=sys.stderr,
        )
        return 2

    session_dir = _session_dir(args.session_dir)
    clips_dir = session_dir / "clips"
    labels_path = session_dir / "labels.json"
    session_path = session_dir / "session.json"
    try:
        ensure_ffmpeg()
        input_paths = []
        if not args.from_cache:
            input_paths = resolve_inputs(
                args.input,
                download_root=Path.cwd() / ".automakeclip_downloads",
                youtube_playlist_limit=max(1, args.youtube_playlist_limit),
            )
        cache_dir = Path.cwd() / config.cache.directory_name
        use_cache = config.cache.enabled and not args.no_cache

        candidates: List[Segment] = []
        source_durations: Dict[str, float] = {}
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
                print(f"[{index}/{len(cached_entries)}] cache review {input_path.name}", file=sys.stderr)
                source_durations[str(input_path)] = metadata.duration
                _add_review_candidates(
                    candidates,
                    input_path,
                    metadata,
                    timeline,
                    config,
                    target_seconds=args.target_seconds,
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
                source_durations[str(input_path)] = metadata.duration
                _add_review_candidates(
                    candidates,
                    input_path,
                    metadata,
                    timeline,
                    config,
                    target_seconds=args.target_seconds,
                    include_silly=not args.no_silly,
                )

        if not candidates:
            raise RuntimeError("No highlight clips were selected for review.")

        candidates = apply_memory_to_candidates(candidates, source_durations)
        final_candidates = select_global_segments(
            candidates,
            target_seconds=args.target_seconds,
            intro_seconds=config.analysis.intro_seconds,
        )
        selected = sequence_segments(final_candidates)
        if args.samples > 0:
            selected = selected[: args.samples]
        if not selected:
            raise RuntimeError("No final montage clips survived selection.")

        clips_dir.mkdir(parents=True, exist_ok=True)
        preview_render = RenderConfig(width=1280, height=720, fps=30, crf=22, preset="veryfast")
        review_clips: List[ReviewClip] = []
        for index, segment in enumerate(selected, start=1):
            clip_id = f"clip-{index:03d}"
            filename = f"{clip_id}.mp4"
            output_path = clips_dir / filename
            _render_review_clip(run_ffmpeg, segment, output_path, preview_render)
            review_clips.append(
                ReviewClip(
                    clip_id=clip_id,
                    source_path=segment.source_path or "",
                    start=segment.start,
                    end=segment.end,
                    score=segment.score,
                    label=segment.label,
                    note=segment.note,
                    filename=filename,
                    highlight_time=segment.highlight_time,
                    source_duration=source_durations.get(segment.source_path or "", 0.0),
                )
            )

        state = ReviewState(
            session_dir=session_dir,
            clips_dir=clips_dir,
            clips=review_clips,
            candidates=candidates,
            labels_path=labels_path,
            target_seconds=args.target_seconds,
        )
        _ensure_labels_file(labels_path)
        session_payload = {
            "created_at": datetime.now().isoformat(),
            "session_dir": str(session_dir),
            "clips": [asdict(clip) for clip in review_clips],
            "candidate_count": len(candidates),
            "candidates": [segment.to_dict() for segment in candidates],
            "target_seconds": args.target_seconds,
        }
        session_path.write_text(json.dumps(session_payload, indent=2), encoding="utf-8")
        server = ThreadingHTTPServer((args.host, args.port), _build_handler(state))
        print(f"Review UI: http://{args.host}:{args.port}", file=sys.stderr)
        print(f"Session Dir: {session_dir}", file=sys.stderr)
        server.serve_forever()
        return 0
    except KeyboardInterrupt:
        print("Review UI stopped.", file=sys.stderr)
        return 0
    except (FFmpegError, InputResolutionError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1


def _render_review_clip(run_ffmpeg, segment: Segment, output_path: Path, config: RenderConfig) -> None:
    if not segment.source_path:
        raise RuntimeError("Review segment is missing its source_path.")
    run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-ss",
            f"{segment.start:.3f}",
            "-to",
            f"{segment.end:.3f}",
            "-i",
            segment.source_path,
            "-vf",
            f"fps={config.fps},scale={config.width}:{config.height}:force_original_aspect_ratio=decrease,pad={config.width}:{config.height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
            "-af",
            "aresample=48000",
            "-c:v",
            config.video_codec,
            "-preset",
            config.preset,
            "-crf",
            str(config.crf),
            "-c:a",
            config.audio_codec,
            "-b:a",
            config.audio_bitrate,
            str(output_path),
        ]
    )


def _session_dir(raw_value: str) -> Path:
    if raw_value:
        return Path(raw_value).expanduser().resolve()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (Path.cwd() / "review_sessions" / timestamp).resolve()


def _ensure_labels_file(labels_path: Path) -> None:
    labels_path.parent.mkdir(parents=True, exist_ok=True)
    if not labels_path.exists():
        labels_path.write_text(
            json.dumps({"decisions": {}, "clip_feedback": {}, "clip_edits": {}, "session_feedback": ""}, indent=2),
            encoding="utf-8",
        )


def _load_labels(labels_path: Path) -> Dict[str, object]:
    try:
        payload = json.loads(labels_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {"decisions": {}, "clip_feedback": {}, "clip_edits": {}, "session_feedback": ""}
    decisions = payload.get("decisions")
    clip_feedback = payload.get("clip_feedback")
    clip_edits = payload.get("clip_edits")
    session_feedback = payload.get("session_feedback")
    if not isinstance(decisions, dict):
        decisions = {}
    if not isinstance(clip_feedback, dict):
        clip_feedback = {}
    if not isinstance(clip_edits, dict):
        clip_edits = {}
    normalized_edits = {
        str(clip_id): edit
        for clip_id, edit in clip_edits.items()
        if isinstance(edit, dict) and isinstance(edit.get("start"), (int, float)) and isinstance(edit.get("end"), (int, float))
    }
    if not isinstance(session_feedback, str):
        session_feedback = ""
    return {
        "decisions": decisions,
        "clip_feedback": clip_feedback,
        "clip_edits": normalized_edits,
        "session_feedback": session_feedback,
    }


def _store_labels(
    labels_path: Path,
    decisions: Dict[str, str],
    clip_feedback: Dict[str, str],
    session_feedback: str,
    clip_edits: Optional[Dict[str, Dict[str, float]]] = None,
) -> None:
    labels_path.write_text(
        json.dumps(
            {
                "decisions": decisions,
                "clip_feedback": clip_feedback,
                "clip_edits": clip_edits or {},
                "session_feedback": session_feedback,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _select_review_clips_for_finish(clips: List[ReviewClip], decisions: Dict[str, str]) -> List[ReviewClip]:
    accepted = [clip for clip in clips if decisions.get(clip.clip_id) == "yes"]
    if accepted:
        return accepted
    return [clip for clip in clips if decisions.get(clip.clip_id) != "no"]


def _review_clip_to_segment(clip: ReviewClip) -> Segment:
    return Segment(
        start=clip.start,
        end=clip.end,
        score=clip.score,
        label=clip.label,
        note=clip.note,
        source_path=clip.source_path,
        highlight_time=clip.highlight_time if clip.highlight_time is not None else (clip.start + clip.end) / 2.0,
    )


def _clip_edit_for_segment(segment: Segment, review_clips: List[ReviewClip], clip_edits: Dict[str, Dict[str, float]]) -> Dict[str, float] | None:
    key = _segment_key(segment)
    for clip in review_clips:
        if _review_clip_key(clip) != key:
            continue
        return _validated_clip_edit(clip, clip_edits.get(clip.clip_id))
    return None


def _validated_clip_edit(clip: ReviewClip, edit: object) -> Dict[str, float] | None:
    if not isinstance(edit, dict):
        return None
    try:
        start = float(edit.get("start"))
        end = float(edit.get("end"))
    except (TypeError, ValueError):
        return None
    upper = clip.source_duration if clip.source_duration > 0 else max(clip.end, end)
    start = max(0.0, min(start, upper))
    end = max(0.0, min(end, upper))
    if end - start < 0.35:
        return None
    return {"start": round(start, 3), "end": round(end, 3)}


def _clip_default_edit(clip: ReviewClip) -> Dict[str, float]:
    return {"start": round(clip.start, 3), "end": round(clip.end, 3)}


def _review_clip_thinking(
    clip: ReviewClip,
    decision: str = "",
    feedback: str = "",
    session_feedback: str = "",
    edit: Optional[Dict[str, float]] = None,
) -> Dict[str, object]:
    note = clip.note or ""
    note_lower = note.lower()
    reasons: List[str] = []

    if "steelseries multi-kill sequence" in note_lower:
        reasons.append("SteelSeries metadata marked a clustered multi-kill window.")
    elif "steelseries kill event window" in note_lower:
        reasons.append("SteelSeries metadata marked a kill event near this trim.")
    elif "generic kill-heavy peak window" in note_lower or "kill-feed heavy" in note_lower:
        reasons.append("Frame analysis saw kill-feed activity and high action around the peak.")
    elif "generic high-activity fight window" in note_lower or "active team-fight" in note_lower:
        reasons.append("Frame and audio analysis saw a high-activity fight window.")
    elif "fallback" in note_lower:
        reasons.append("Fallback scoring picked this as an action-heavy moment without a logged kill.")
    elif clip.label == "silly":
        reasons.append("This was tagged as a short chaos/comedy bridge clip.")
    else:
        reasons.append("Detector score and label put this clip into the review pool.")

    memory_reasons = []
    if "memory-trimmed" in note_lower:
        memory_reasons.append("prior manual trim edits changed this trim")
    if "memory-adjusted" in note_lower:
        memory_reasons.append("prior clip notes changed this trim")
    if "memory-boosted" in note_lower:
        memory_reasons.append("review memory boosted this score")
    if "memory-downranked" in note_lower:
        memory_reasons.append("review memory downranked this score")
    if memory_reasons:
        reasons.append("Memory applied: " + ", ".join(memory_reasons) + ".")

    if decision == "yes":
        reasons.append("You marked it Keep, so matching clips get preference.")
    elif decision == "no":
        reasons.append("You marked it Cut, so matching clips get penalized.")
    elif decision == "skip":
        reasons.append("You skipped it, so the memory impact is weaker.")

    edit_payload = edit or _clip_default_edit(clip)
    detected_duration = max(0.0, clip.end - clip.start)
    trim_duration = max(0.0, float(edit_payload["end"]) - float(edit_payload["start"]))
    if abs(float(edit_payload["start"]) - clip.start) >= 0.05 or abs(float(edit_payload["end"]) - clip.end) >= 0.05:
        reasons.append(f"Current trim is {trim_duration:.2f}s, changed from the detected {detected_duration:.2f}s window.")
    else:
        reasons.append(f"Current trim matches the detected {detected_duration:.2f}s window.")

    directives = feedback_directives(feedback, session_feedback)
    understood_notes = _directive_labels(directives)
    if understood_notes:
        reasons.append("Understood notes: " + ", ".join(understood_notes) + ".")

    return {
        "mode": "Heuristics + review memory",
        "ml_status": "No neural ML model is training here; it is deterministic scoring plus saved review feedback.",
        "score": round(float(clip.score), 3),
        "tier": candidate_tier(_review_clip_to_segment(clip)),
        "bucket": clip_feature_bucket(clip),
        "duration": round(trim_duration, 3),
        "reasons": reasons,
        "understood_notes": understood_notes,
    }


def _review_clip_payload(
    clip: ReviewClip,
    labels: Dict[str, str],
    clip_feedback: Dict[str, str],
    clip_edits: Dict[str, Dict[str, float]],
    session_feedback: str,
) -> Dict[str, object]:
    decision = str(labels.get(clip.clip_id, ""))
    feedback = str(clip_feedback.get(clip.clip_id, ""))
    edit = _validated_clip_edit(clip, clip_edits.get(clip.clip_id)) or _clip_default_edit(clip)
    return {
        **asdict(clip),
        "video_url": f"/clips/{clip.filename}",
        "source_video_url": f"/sources/{clip.clip_id}",
        "decision": decision,
        "feedback": feedback,
        "edit": edit,
        "thinking": _review_clip_thinking(
            clip,
            decision=decision,
            feedback=feedback,
            session_feedback=session_feedback,
            edit=edit,
        ),
    }


def _directive_labels(directives: Dict[str, bool]) -> List[str]:
    labels_by_key = {
        "more_like_this": "more like this",
        "less_like_this": "less like this",
        "shorter": "shorter",
        "start_later": "start later",
        "end_sooner": "end sooner",
        "more_lead_in": "more lead-in",
        "more_after": "more aftermath",
        "prefer_multi_kill": "prefer multi-kills",
        "prefer_kill_event": "prefer kills",
        "prefer_action": "prefer action",
        "avoid_silly": "avoid silly/filler",
        "avoid_fallback": "avoid fallback",
        "avoid_generic": "avoid generic",
        "avoid_dead_air": "avoid dead air",
        "avoid_training": "avoid training/practice",
    }
    return [label for key, label in labels_by_key.items() if directives.get(key)]


def _segment_key(segment: Segment) -> tuple[str, float, float]:
    return (segment.source_path or "", round(segment.start, 3), round(segment.end, 3))


def _review_clip_key(clip: ReviewClip) -> tuple[str, float, float]:
    return (clip.source_path, round(clip.start, 3), round(clip.end, 3))


def _review_decision_keys(clips: List[ReviewClip], decisions: Dict[str, str]) -> tuple[set[tuple[str, float, float]], set[tuple[str, float, float]]]:
    yes_keys = {_review_clip_key(clip) for clip in clips if decisions.get(clip.clip_id) == "yes"}
    no_keys = {_review_clip_key(clip) for clip in clips if decisions.get(clip.clip_id) == "no"}
    return yes_keys, no_keys


def _remember_review_clip(
    clip: ReviewClip,
    decision: str,
    feedback: str,
    edit: Optional[Dict[str, float]],
    session_dir: Path,
) -> None:
    try:
        persist_review_clip(clip, decision=decision, feedback=feedback, edit=edit, session_dir=session_dir)
    except OSError as error:
        print(f"Review memory write failed: {error}", file=sys.stderr)


def _remember_review_session(
    state: ReviewState,
    decisions: Dict[str, str],
    clip_feedback: Dict[str, str],
    clip_edits: Dict[str, Dict[str, float]],
    session_feedback: str = "",
) -> None:
    try:
        flush_review_session_memory(
            state.clips,
            decisions,
            clip_feedback,
            clip_edits,
            session_feedback=session_feedback,
            session_dir=state.session_dir,
        )
    except OSError as error:
        print(f"Review memory write failed: {error}", file=sys.stderr)


def _select_finish_segments(
    candidates: List[Segment],
    review_clips: List[ReviewClip],
    decisions: Dict[str, str],
    target_seconds: float,
    intro_seconds: float,
    clip_edits: Optional[Dict[str, Dict[str, float]]] = None,
) -> List[Segment]:
    clip_edits = clip_edits or {}
    source_candidates = list(candidates) if candidates else [_review_clip_to_segment(clip) for clip in review_clips]
    if not source_candidates:
        return []

    yes_keys, no_keys = _review_decision_keys(review_clips, decisions)
    max_score = max((abs(candidate.score) for candidate in source_candidates), default=1.0)
    adjusted: List[Segment] = []
    for candidate in source_candidates:
        key = _segment_key(candidate)
        if key in no_keys:
            continue
        review_approved = key in yes_keys
        edit = _clip_edit_for_segment(candidate, review_clips, clip_edits)
        start = edit["start"] if edit else candidate.start
        end = edit["end"] if edit else candidate.end
        highlight_time = candidate.highlight_time
        if highlight_time is not None:
            highlight_time = min(max(highlight_time, start), end)
        adjusted.append(
            Segment(
                start=start,
                end=end,
                score=candidate.score + max_score * 2.0 + 1.0 if review_approved else candidate.score,
                label=candidate.label,
                note=f"Review-approved clip. {candidate.note}" if review_approved else candidate.note,
                source_path=candidate.source_path,
                highlight_time=highlight_time,
            )
        )

    return sequence_segments(
        select_global_segments(
            adjusted,
            target_seconds=target_seconds,
            intro_seconds=intro_seconds,
        )
    )


def _finish_review_session(
    state: ReviewState,
    labels_payload: Dict[str, object],
    config: Optional[AppConfig] = None,
) -> Dict[str, object]:
    from .analysis import infer_montage_profile
    from .ffmpeg import probe_video
    from .music import MusicSelectionError, select_music_track
    from .render import render_montage, snap_segments_to_beats

    app_config = config or AppConfig()
    decisions = labels_payload.get("decisions")
    if not isinstance(decisions, dict):
        decisions = {}
    clip_edits = labels_payload.get("clip_edits")
    if not isinstance(clip_edits, dict):
        clip_edits = {}

    segments = _select_finish_segments(
        state.candidates,
        state.clips,
        decisions,
        target_seconds=state.target_seconds,
        intro_seconds=app_config.analysis.intro_seconds,
        clip_edits=clip_edits,
    )
    if not segments:
        raise RuntimeError("No clips are available to render from this review session.")

    input_paths: List[Path] = []
    seen_sources = set()
    for segment in segments:
        if not segment.source_path:
            continue
        source_path = Path(segment.source_path)
        if source_path in seen_sources:
            continue
        input_paths.append(source_path)
        seen_sources.add(source_path)
    if not input_paths:
        raise RuntimeError("The selected review clips do not have source video paths.")

    source_durations = {str(path): probe_video(path).duration for path in input_paths}
    mood, target_bpm = infer_montage_profile(segments)
    snapped = snap_segments_to_beats(
        segments,
        bpm=target_bpm,
        duration_by_source=source_durations,
        tolerance=app_config.render.beat_snap_tolerance_seconds,
    )

    output_path = state.session_dir / "final_review_montage.mp4"
    plan = MontagePlan(
        input_paths=input_paths,
        output_path=output_path,
        title="OVERWATCH HIGHLIGHTS",
        subtitle="review-approved montage",
        target_seconds=round(sum(segment.duration for segment in snapped) + app_config.analysis.intro_seconds, 3),
        source_duration=round(sum(source_durations.values()), 3),
        source_durations=source_durations,
        segments=snapped,
        mood=mood,
        target_bpm=target_bpm,
    )

    music_error = ""
    try:
        plan.music = select_music_track(
            plan.mood,
            plan.target_bpm,
            state.session_dir / "music_cache",
            app_config.music,
        )
    except MusicSelectionError as error:
        music_error = str(error)

    render_montage(plan=plan, analysis_config=app_config.analysis, render_config=app_config.render, keep_temp=False)

    result = {
        "rendered_at": datetime.now().isoformat(),
        "output_path": str(output_path),
        "video_url": f"/final/{output_path.name}",
        "plan_path": str(output_path.with_suffix(".plan.json")),
        "credits_path": str(output_path.with_suffix(".credits.txt")),
        "clip_count": len(snapped),
        "music": plan.music.to_dict() if plan.music else None,
        "music_error": music_error,
    }
    (state.session_dir / "finish.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def _load_finish_payload(session_dir: Path) -> Dict[str, object]:
    finish_path = session_dir / "finish.json"
    if not finish_path.exists():
        return {}
    try:
        payload = json.loads(finish_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_current_render_payload(state: ReviewState) -> Dict[str, object]:
    from .latest_render import load_latest_render

    payloads = [
        payload
        for payload in (_load_finish_payload(state.session_dir), load_latest_render())
        if _render_output_exists(payload)
    ]
    if not payloads:
        return {}
    current = max(payloads, key=_rendered_timestamp)
    return _render_payload_for_review(state, current)


def _rendered_timestamp(payload: Dict[str, object]) -> datetime:
    rendered_at = str(payload.get("rendered_at") or "")
    try:
        return datetime.fromisoformat(rendered_at)
    except ValueError:
        return datetime.min


def _render_output_exists(payload: Dict[str, object]) -> bool:
    output_value = payload.get("output_path")
    if not output_value:
        return False
    return Path(str(output_value)).expanduser().is_file()


def _render_payload_for_review(state: ReviewState, payload: Dict[str, object]) -> Dict[str, object]:
    result = dict(payload)
    output_value = result.get("output_path")
    if not output_value:
        return result
    output_path = Path(str(output_value)).expanduser().resolve()
    result["output_path"] = str(output_path)
    if output_path.exists():
        if output_path.parent == state.session_dir.resolve():
            result["video_url"] = f"/final/{output_path.name}"
        else:
            result["video_url"] = "/latest-render/video"
    return result


def _latest_render_path_for_route(kind: str) -> Path | None:
    from .latest_render import load_latest_render

    key_by_kind = {
        "video": "output_path",
        "plan": "plan_path",
        "credits": "credits_path",
    }
    key = key_by_kind.get(kind)
    if key is None:
        return None
    payload = load_latest_render()
    path_value = payload.get(key)
    if not path_value:
        return None
    return Path(str(path_value)).expanduser().resolve()


def _content_type_for_path(path: Path) -> str:
    if path.suffix.lower() == ".mp4":
        return "video/mp4"
    if path.suffix.lower() == ".json":
        return "application/json; charset=utf-8"
    if path.suffix.lower() == ".txt":
        return "text/plain; charset=utf-8"
    return "application/octet-stream"


def _build_handler(state: ReviewState):
    class ReviewHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_html(_review_html())
                return
            if parsed.path == "/api/session":
                labels_payload = _load_labels(state.labels_path)
                labels = labels_payload["decisions"]
                clip_feedback = labels_payload["clip_feedback"]
                clip_edits = labels_payload["clip_edits"]
                payload = {
                    "session_dir": str(state.session_dir),
                    "clips": [
                        _review_clip_payload(clip, labels, clip_feedback, clip_edits, labels_payload["session_feedback"])
                        for clip in state.clips
                    ],
                    "decisions": labels,
                    "clip_feedback": clip_feedback,
                    "clip_edits": clip_edits,
                    "session_feedback": labels_payload["session_feedback"],
                    "finish": _load_current_render_payload(state),
                }
                self._send_json(payload)
                return
            if parsed.path.startswith("/latest-render/"):
                kind = unquote(parsed.path.removeprefix("/latest-render/"))
                latest_path = _latest_render_path_for_route(kind)
                if latest_path is None or not latest_path.is_file():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                body = latest_path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", _content_type_for_path(latest_path))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path.startswith("/final/"):
                relative = unquote(parsed.path.removeprefix("/final/"))
                final_path = (state.session_dir / relative).resolve()
                if final_path.parent != state.session_dir or not final_path.exists():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self._send_file(final_path)
                return
            if parsed.path.startswith("/sources/"):
                clip_id = unquote(parsed.path.removeprefix("/sources/"))
                clip = next((item for item in state.clips if item.clip_id == clip_id), None)
                if clip is None or not clip.source_path:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                source_path = Path(clip.source_path).expanduser().resolve()
                if not source_path.exists():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self._send_file(source_path)
                return
            if parsed.path.startswith("/clips/"):
                relative = unquote(parsed.path.removeprefix("/clips/"))
                clip_path = (state.clips_dir / relative).resolve()
                if clip_path.parent != state.clips_dir or not clip_path.exists():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self._send_file(clip_path)
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                content_length = 0
            raw_body = self.rfile.read(content_length)
            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except json.JSONDecodeError:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return

            labels_payload = _load_labels(state.labels_path)
            decisions = labels_payload["decisions"]
            clip_feedback = labels_payload["clip_feedback"]
            clip_edits = labels_payload["clip_edits"]
            session_feedback = str(labels_payload["session_feedback"])

            if parsed.path == "/api/label":
                clip_id = str(payload.get("clip_id") or "")
                decision = str(payload.get("decision") or "")
                clip = next((item for item in state.clips if item.clip_id == clip_id), None)
                if clip is None:
                    self.send_error(HTTPStatus.BAD_REQUEST, "Unknown clip_id")
                    return
                if decision not in {"yes", "no", "skip"}:
                    self.send_error(HTTPStatus.BAD_REQUEST, "decision must be yes, no, or skip")
                    return

                decisions[clip_id] = decision
                _store_labels(state.labels_path, decisions, clip_feedback, session_feedback, clip_edits)
                _remember_review_clip(
                    clip,
                    decision,
                    str(clip_feedback.get(clip_id, "")),
                    clip_edits.get(clip_id),
                    state.session_dir,
                )
                self._send_json({"ok": True, "clip_id": clip_id, "decision": decision})
                return

            if parsed.path == "/api/clip-feedback":
                clip_id = str(payload.get("clip_id") or "")
                feedback = str(payload.get("feedback") or "").strip()
                clip = next((item for item in state.clips if item.clip_id == clip_id), None)
                if clip is None:
                    self.send_error(HTTPStatus.BAD_REQUEST, "Unknown clip_id")
                    return
                clip_feedback[clip_id] = feedback
                _store_labels(state.labels_path, decisions, clip_feedback, session_feedback, clip_edits)
                _remember_review_clip(
                    clip,
                    str(decisions.get(clip_id, "")),
                    feedback,
                    clip_edits.get(clip_id),
                    state.session_dir,
                )
                self._send_json({"ok": True, "clip_id": clip_id, "feedback": feedback})
                return

            if parsed.path == "/api/clip-edit":
                clip_id = str(payload.get("clip_id") or "")
                clip = next((item for item in state.clips if item.clip_id == clip_id), None)
                if clip is None:
                    self._send_json({"ok": False, "error": "Unknown clip_id"}, status=HTTPStatus.BAD_REQUEST)
                    return
                edit = _validated_clip_edit(clip, payload)
                if edit is None:
                    self._send_json({"ok": False, "error": "Invalid trim range"}, status=HTTPStatus.BAD_REQUEST)
                    return
                clip_edits[clip_id] = edit
                _store_labels(state.labels_path, decisions, clip_feedback, session_feedback, clip_edits)
                _remember_review_clip(
                    clip,
                    str(decisions.get(clip_id, "")),
                    str(clip_feedback.get(clip_id, "")),
                    edit,
                    state.session_dir,
                )
                self._send_json({"ok": True, "clip_id": clip_id, "edit": edit})
                return

            if parsed.path == "/api/session-feedback":
                session_feedback = str(payload.get("feedback") or "").strip()
                _store_labels(state.labels_path, decisions, clip_feedback, session_feedback, clip_edits)
                self._send_json({"ok": True, "session_feedback": session_feedback})
                return

            if parsed.path == "/api/finish":
                try:
                    _remember_review_session(state, decisions, clip_feedback, clip_edits, session_feedback)
                    result = _finish_review_session(state, labels_payload)
                except RuntimeError as error:
                    self._send_json({"ok": False, "error": str(error)}, status=HTTPStatus.BAD_REQUEST)
                    return
                self._send_json({"ok": True, **result})
                return

            self.send_error(HTTPStatus.NOT_FOUND)
            return

        def log_message(self, format: str, *args) -> None:
            return

        def _send_json(self, payload: Dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_file(self, path: Path) -> None:
            file_size = path.stat().st_size
            range_header = self.headers.get("Range", "")
            start = 0
            end = file_size - 1
            status = HTTPStatus.OK
            if range_header.startswith("bytes="):
                start_value, _, end_value = range_header.removeprefix("bytes=").partition("-")
                try:
                    start = int(start_value) if start_value else 0
                    end = int(end_value) if end_value else file_size - 1
                    start = max(0, min(start, file_size - 1))
                    end = max(start, min(end, file_size - 1))
                    status = HTTPStatus.PARTIAL_CONTENT
                except ValueError:
                    start = 0
                    end = file_size - 1
                    status = HTTPStatus.OK
            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", _content_type_for_path(path))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if status == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.end_headers()
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(1024 * 512, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

    return ReviewHandler


def _review_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AutoMakeClip Editor</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #101214;
      --panel: #181b1f;
      --panel-2: #20242a;
      --line: #343a42;
      --text: #f2f4f5;
      --muted: #9da8b3;
      --accent: #48c78e;
      --blue: #6aa8ff;
      --danger: #ff806c;
      --warn: #f2b84b;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ui-sans-serif, system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      overflow-x: hidden;
    }
    button, input, textarea {
      font: inherit;
    }
    .app {
      min-height: 100vh;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 12px 16px;
      border-bottom: 1px solid var(--line);
      background: #14171a;
      min-height: 56px;
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 750;
      letter-spacing: 0;
    }
    .workspace {
      display: grid;
      grid-template-columns: minmax(220px, 280px) minmax(0, 1fr) minmax(260px, 340px);
      gap: 1px;
      min-height: 0;
      background: var(--line);
    }
    .panel {
      min-width: 0;
      min-height: 0;
      background: var(--panel);
    }
    .panel-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      min-height: 42px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
      text-transform: uppercase;
    }
    .clip-bin {
      overflow: auto;
      padding: 8px;
    }
    .clip-item {
      width: 100%;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      align-items: center;
      border: 1px solid transparent;
      border-radius: 8px;
      padding: 10px;
      margin: 0 0 8px;
      background: #20242a;
      color: var(--text);
      text-align: left;
      cursor: pointer;
    }
    .clip-item.active {
      border-color: var(--blue);
      box-shadow: inset 0 0 0 1px rgba(106, 168, 255, 0.35);
    }
    .clip-item.keep { border-left: 4px solid var(--accent); }
    .clip-item.cut { border-left: 4px solid var(--danger); }
    .clip-name {
      overflow: hidden;
      white-space: nowrap;
      text-overflow: ellipsis;
      font-size: 13px;
      font-weight: 700;
    }
    .clip-meta, .small {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .viewer {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      min-height: 0;
    }
    .viewer-stage {
      display: grid;
      place-items: center;
      padding: 14px;
      min-height: 0;
      background: #0b0d0f;
    }
    video {
      width: 100%;
      max-height: calc(100vh - 320px);
      aspect-ratio: 16 / 9;
      background: black;
      border: 1px solid #272c33;
      border-radius: 8px;
    }
    .transport, .inspector-actions, .header-actions {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
    }
    button {
      border: 1px solid transparent;
      border-radius: 7px;
      padding: 8px 11px;
      background: #2a3038;
      color: var(--text);
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
    }
    button:hover { border-color: #52606e; }
    .yes { background: #1f5f44; color: #e7fff3; }
    .no { background: #693026; color: #fff0ed; }
    .skip { background: #343b45; color: #edf2f7; }
    .finish { background: var(--blue); color: #071527; }
    button:disabled {
      opacity: 0.58;
      cursor: wait;
    }
    a {
      color: #a6d2ff;
      font-weight: 700;
    }
    .source-rail {
      padding: 12px 14px 14px;
      border-top: 1px solid var(--line);
      background: #14171a;
    }
    .rail {
      position: relative;
      height: 34px;
      border-radius: 7px;
      background: #252a31;
      overflow: hidden;
      border: 1px solid #39414b;
    }
    .detected-range, .edit-range, .playhead {
      position: absolute;
      top: 0;
      bottom: 0;
    }
    .detected-range { background: rgba(242, 184, 75, 0.25); }
    .edit-range { background: rgba(106, 168, 255, 0.38); border-left: 2px solid var(--blue); border-right: 2px solid var(--blue); }
    .playhead { width: 2px; background: #ffffff; opacity: 0.85; }
    .range-stack {
      position: relative;
      height: 34px;
      margin-top: 10px;
    }
    .range-stack input[type="range"] {
      position: absolute;
      inset: 0;
      width: 100%;
      pointer-events: none;
      appearance: none;
      background: transparent;
    }
    input[type="range"]::-webkit-slider-thumb {
      pointer-events: auto;
      appearance: none;
      width: 14px;
      height: 26px;
      border-radius: 5px;
      background: var(--blue);
      border: 2px solid #d7e8ff;
      cursor: ew-resize;
    }
    input[type="range"]::-webkit-slider-runnable-track {
      height: 4px;
      background: #3b424c;
      border-radius: 999px;
    }
    input[type="range"]::-moz-range-thumb {
      pointer-events: auto;
      width: 14px;
      height: 26px;
      border-radius: 5px;
      background: var(--blue);
      border: 2px solid #d7e8ff;
      cursor: ew-resize;
    }
    input[type="range"]::-moz-range-track {
      height: 4px;
      background: #3b424c;
      border-radius: 999px;
    }
    .inspector {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      min-height: 0;
    }
    .inspector-body {
      overflow: auto;
      padding: 12px;
      display: grid;
      gap: 14px;
      align-content: start;
    }
    .field-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    label {
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
    }
    input[type="number"], textarea {
      width: 100%;
      border: 1px solid #39414b;
      border-radius: 7px;
      background: #111417;
      color: var(--text);
      padding: 9px;
    }
    textarea {
      min-height: 92px;
      resize: vertical;
    }
    .status-line {
      min-height: 18px;
      color: var(--muted);
      font-size: 12px;
    }
    .timeline-panel {
      border-top: 1px solid var(--line);
      background: #14171a;
      min-height: 148px;
      padding: 10px 12px 14px;
    }
    .timeline-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 9px;
      font-weight: 700;
      text-transform: uppercase;
    }
    .timeline-track {
      display: flex;
      align-items: stretch;
      gap: 4px;
      min-height: 82px;
      overflow-x: auto;
      padding-bottom: 8px;
    }
    .timeline-clip {
      position: relative;
      min-width: 80px;
      border: 1px solid #39414b;
      background: #252a31;
      border-radius: 7px;
      padding: 8px;
      cursor: pointer;
      overflow: hidden;
    }
    .timeline-clip.active {
      border-color: var(--blue);
      background: #26354a;
    }
    .timeline-clip.keep { box-shadow: inset 0 3px 0 var(--accent); }
    .timeline-clip.cut { opacity: 0.48; box-shadow: inset 0 3px 0 var(--danger); }
    .timeline-title {
      font-size: 12px;
      font-weight: 750;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .pill-row {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 3px 8px;
      border-radius: 999px;
      background: #29313a;
      color: #dce4eb;
      font-size: 12px;
      font-weight: 700;
    }
    .thinking-panel {
      position: sticky;
      bottom: 0;
      border: 1px solid #39414b;
      border-radius: 8px;
      background: #14171a;
      padding: 10px;
      box-shadow: 0 -12px 24px rgba(0, 0, 0, 0.18);
    }
    .thinking-header {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      margin-bottom: 8px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      text-transform: uppercase;
    }
    .thinking-mode {
      color: #dce4eb;
      text-transform: none;
      font-weight: 700;
      text-align: right;
    }
    .thinking-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 6px;
      margin-bottom: 8px;
    }
    .thinking-cell {
      min-width: 0;
      border: 1px solid #29313a;
      border-radius: 7px;
      padding: 7px;
      background: #111417;
    }
    .thinking-label {
      color: var(--muted);
      font-size: 10px;
      font-weight: 800;
      text-transform: uppercase;
    }
    .thinking-value {
      margin-top: 2px;
      overflow-wrap: anywhere;
      color: var(--text);
      font-size: 12px;
      font-weight: 750;
    }
    .thinking-list {
      margin: 0;
      padding-left: 18px;
      color: #dce4eb;
      font-size: 12px;
      line-height: 1.45;
    }
    .thinking-list li {
      margin-bottom: 5px;
    }
    @media (max-width: 980px) {
      .workspace {
        grid-template-columns: 1fr;
        grid-template-rows: auto auto auto;
      }
      video { max-height: 45vh; }
      .clip-bin { max-height: 220px; }
    }
  </style>
</head>
<body>
  <main class="app">
    <header>
      <h1>AutoMakeClip Editor</h1>
      <div class="header-actions">
        <div class="small" id="finishStatus">No final montage created yet.</div>
        <button type="button" class="finish" id="finishButton" onclick="finishReview()">Finish Review</button>
      </div>
    </header>
    <section class="workspace">
      <aside class="panel">
        <div class="panel-header">
          <span>Clips</span>
          <span id="progress">Loading...</span>
        </div>
        <div class="clip-bin" id="clipBin"></div>
      </aside>
      <section class="panel viewer">
        <div class="panel-header">
          <span id="viewerTitle">Source</span>
          <div class="transport">
            <button type="button" class="skip" onclick="move(-1)">Prev</button>
            <button type="button" class="skip" onclick="togglePlayback()">Play</button>
            <button type="button" class="skip" onclick="move(1)">Next</button>
          </div>
        </div>
        <div class="viewer-stage">
          <video id="player" controls></video>
        </div>
        <div class="source-rail">
          <div class="rail" id="sourceRail">
            <div class="detected-range" id="detectedRange"></div>
            <div class="edit-range" id="editRange"></div>
            <div class="playhead" id="playhead"></div>
          </div>
          <div class="range-stack">
            <input id="trimIn" type="range" min="0" max="1" step="0.01" oninput="updateTrimFromSlider('start', this.value)">
            <input id="trimOut" type="range" min="0" max="1" step="0.01" oninput="updateTrimFromSlider('end', this.value)">
          </div>
        </div>
      </section>
      <aside class="panel inspector">
        <div class="panel-header">
          <span>Inspector</span>
          <span id="trimStatus">Trim saved.</span>
        </div>
        <div class="inspector-body">
          <div class="pill-row" id="pills"></div>
          <div class="clip-meta" id="source"></div>
          <div class="clip-meta" id="note"></div>
          <div class="inspector-actions">
            <button type="button" class="yes" onclick="label('yes')">Keep</button>
            <button type="button" class="no" onclick="label('no')">Cut</button>
            <button type="button" class="skip" onclick="label('skip')">Skip</button>
          </div>
          <div class="field-grid">
            <label>In
              <input id="trimInValue" type="number" step="0.01" onchange="updateTrimFromNumber('start', this.value)">
            </label>
            <label>Out
              <input id="trimOutValue" type="number" step="0.01" onchange="updateTrimFromNumber('end', this.value)">
            </label>
          </div>
          <div class="field-grid">
            <button type="button" class="skip" onclick="markFromPlayhead('start')">Set In</button>
            <button type="button" class="skip" onclick="markFromPlayhead('end')">Set Out</button>
          </div>
          <button type="button" class="finish" onclick="saveClipEdit()">Save Trim</button>
          <div>
            <label>Clip Notes</label>
            <textarea id="clipFeedback"></textarea>
            <div class="status-line" id="clipFeedbackStatus">No clip feedback saved yet.</div>
            <button type="button" class="skip" onclick="saveClipFeedback()">Save Clip Notes</button>
          </div>
          <div>
            <label>Session Notes</label>
            <textarea id="sessionFeedback"></textarea>
            <div class="status-line" id="sessionFeedbackStatus">No session feedback saved yet.</div>
            <button type="button" class="skip" onclick="saveSessionFeedback()">Save Session Notes</button>
          </div>
          <section class="thinking-panel">
            <div class="thinking-header">
              <span>Readout</span>
              <span class="thinking-mode" id="thinkingMode">Heuristics</span>
            </div>
            <div class="thinking-grid" id="thinkingMeta"></div>
            <ul class="thinking-list" id="thinkingReasons"></ul>
            <div class="clip-meta" id="thinkingStatus"></div>
          </section>
        </div>
      </aside>
    </section>
    <section class="timeline-panel">
      <div class="timeline-header">
        <span>Timeline</span>
        <span id="timelineDuration">0.00s</span>
      </div>
      <div class="timeline-track" id="timelineTrack"></div>
    </section>
  </main>
  <script>
    let session = { clips: [] };
    let index = 0;
    let pendingEdit = null;
    let trimSaveTimer = 0;

    async function loadSession(options = {}) {
      const response = await fetch('/api/session');
      session = await response.json();
      if (Number.isInteger(options.index)) {
        index = options.index;
      }
      if (options.clipId) {
        const clipIndex = session.clips.findIndex((clip) => clip.clip_id === options.clipId);
        if (clipIndex >= 0) {
          index = clipIndex;
        }
      }
      render();
    }

    async function refreshFinishStatus() {
      const response = await fetch('/api/session');
      const nextSession = await response.json();
      session.finish = nextSession.finish;
      renderFinishStatus(session.finish);
    }

    function currentClip() {
      if (!session.clips.length) {
        return null;
      }
      index = Math.max(0, Math.min(index, session.clips.length - 1));
      return session.clips[index];
    }

    function clipDuration(clip) {
      const edit = clip.edit || { start: clip.start, end: clip.end };
      return Math.max(0.35, edit.end - edit.start);
    }

    function sourceDuration(clip) {
      return Math.max(clip.source_duration || clip.end || 1, clip.end || 1, 1);
    }

    function useSourceVideo(clip) {
      const path = (clip.source_path || '').toLowerCase();
      return path.endsWith('.mp4') || path.endsWith('.webm') || path.endsWith('.mov');
    }

    function activeEdit(clip) {
      if (pendingEdit && pendingEdit.clipId === clip.clip_id) {
        return { start: pendingEdit.start, end: pendingEdit.end };
      }
      const edit = clip.edit || { start: clip.start, end: clip.end };
      return { start: Number(edit.start), end: Number(edit.end) };
    }

    function clampEdit(clip, edit, changed) {
      const maxTime = sourceDuration(clip);
      let start = Math.max(0, Math.min(Number(edit.start), maxTime));
      let end = Math.max(0, Math.min(Number(edit.end), maxTime));
      if (end - start < 0.35) {
        if (changed === 'start') {
          start = Math.max(0, end - 0.35);
        } else {
          end = Math.min(maxTime, start + 0.35);
        }
      }
      if (start > end) {
        const swap = start;
        start = end;
        end = swap;
      }
      return { clipId: clip.clip_id, start: Number(start.toFixed(3)), end: Number(end.toFixed(3)) };
    }

    function render() {
      const clip = currentClip();
      if (!clip) {
        document.getElementById('progress').textContent = 'No clips found.';
        return;
      }
      const labeled = Object.entries(session.decisions || {});
      const yesCount = labeled.filter(([, value]) => value === 'yes').length;
      const noCount = labeled.filter(([, value]) => value === 'no').length;
      const skipCount = labeled.filter(([, value]) => value === 'skip').length;
      document.getElementById('progress').textContent =
        `${index + 1} / ${session.clips.length}`;
      document.getElementById('viewerTitle').textContent = `${clip.label} | keep ${yesCount} | cut ${noCount} | skip ${skipCount}`;
      const player = document.getElementById('player');
      const sourceMode = useSourceVideo(clip);
      const videoUrl = sourceMode ? clip.source_video_url : clip.video_url;
      if (player.dataset.clipId !== clip.clip_id || player.dataset.videoUrl !== videoUrl) {
        player.src = videoUrl;
        player.dataset.clipId = clip.clip_id;
        player.dataset.videoUrl = videoUrl;
        player.dataset.sourceMode = sourceMode ? '1' : '0';
        player.addEventListener('loadedmetadata', seekToEditStart, { once: true });
        player.load();
      }
      document.getElementById('pills').innerHTML =
        `<span class="pill">${clip.label}</span><span class="pill">score ${clip.score.toFixed(2)}</span><span class="pill">${clip.decision || 'pending'}</span>`;
      document.getElementById('source').textContent =
        `${clip.source_path} | detected ${clip.start.toFixed(2)}s - ${clip.end.toFixed(2)}s`;
      document.getElementById('note').textContent = clip.note || '';
      document.getElementById('clipFeedback').value = clip.feedback || '';
      document.getElementById('clipFeedbackStatus').textContent =
        clip.feedback ? 'Clip feedback saved.' : 'No clip feedback saved yet.';
      document.getElementById('sessionFeedback').value = session.session_feedback || '';
      document.getElementById('sessionFeedbackStatus').textContent =
        session.session_feedback ? 'Session feedback saved.' : 'No session feedback saved yet.';
      renderThinking(clip);
      renderFinishStatus(session.finish);
      renderClipBin();
      renderTimeline();
      renderTrimControls();
      renderSourceRail();
    }

    function renderClipBin() {
      const bin = document.getElementById('clipBin');
      bin.innerHTML = session.clips.map((clip, clipIndex) => {
        const decisionClass = clip.decision === 'yes' ? 'keep' : clip.decision === 'no' ? 'cut' : '';
        const activeClass = clipIndex === index ? 'active' : '';
        return `<button type="button" class="clip-item ${activeClass} ${decisionClass}" onclick="selectClip(${clipIndex})">
          <span>
            <span class="clip-name">${clipIndex + 1}. ${escapeHtml(shortName(clip.source_path))}</span>
            <span class="clip-meta">${clipDuration(clip).toFixed(2)}s | ${clip.label}</span>
          </span>
          <span class="pill">${clip.decision || 'open'}</span>
        </button>`;
      }).join('');
    }

    function renderTimeline() {
      const track = document.getElementById('timelineTrack');
      const keptClips = session.clips.filter((clip) => clip.decision !== 'no');
      const total = keptClips.reduce((sum, clip) => sum + clipDuration(clip), 0);
      document.getElementById('timelineDuration').textContent = `${total.toFixed(2)}s`;
      track.innerHTML = session.clips.map((clip, clipIndex) => {
        const duration = clipDuration(clip);
        const basis = Math.max(80, Math.min(260, duration * 18));
        const activeClass = clipIndex === index ? 'active' : '';
        const decisionClass = clip.decision === 'yes' ? 'keep' : clip.decision === 'no' ? 'cut' : '';
        return `<div class="timeline-clip ${activeClass} ${decisionClass}" style="flex: 0 0 ${basis}px" onclick="selectClip(${clipIndex})">
          <div class="timeline-title">${clipIndex + 1}. ${escapeHtml(shortName(clip.source_path))}</div>
          <div class="clip-meta">${duration.toFixed(2)}s</div>
        </div>`;
      }).join('');
    }

    function renderTrimControls() {
      const clip = currentClip();
      if (!clip) {
        return;
      }
      const edit = activeEdit(clip);
      const maxTime = sourceDuration(clip);
      const trimIn = document.getElementById('trimIn');
      const trimOut = document.getElementById('trimOut');
      for (const slider of [trimIn, trimOut]) {
        slider.min = '0';
        slider.max = String(maxTime.toFixed(3));
        slider.step = '0.01';
      }
      trimIn.value = String(edit.start);
      trimOut.value = String(edit.end);
      document.getElementById('trimInValue').min = '0';
      document.getElementById('trimInValue').max = String(maxTime.toFixed(3));
      document.getElementById('trimOutValue').min = '0';
      document.getElementById('trimOutValue').max = String(maxTime.toFixed(3));
      document.getElementById('trimInValue').value = edit.start.toFixed(2);
      document.getElementById('trimOutValue').value = edit.end.toFixed(2);
    }

    function renderSourceRail() {
      const clip = currentClip();
      if (!clip) {
        return;
      }
      const edit = activeEdit(clip);
      const duration = sourceDuration(clip);
      setRangeStyle('detectedRange', clip.start, clip.end, duration);
      setRangeStyle('editRange', edit.start, edit.end, duration);
      updatePlayhead();
    }

    function setRangeStyle(id, start, end, duration) {
      const element = document.getElementById(id);
      const left = Math.max(0, Math.min(100, (start / duration) * 100));
      const right = Math.max(0, Math.min(100, (end / duration) * 100));
      element.style.left = `${left}%`;
      element.style.width = `${Math.max(0.2, right - left)}%`;
    }

    function updatePlayhead() {
      const clip = currentClip();
      if (!clip) {
        return;
      }
      const player = document.getElementById('player');
      const duration = sourceDuration(clip);
      const sourceMode = player.dataset.sourceMode === '1';
      const sourceTime = sourceMode ? player.currentTime : clip.start + player.currentTime;
      const left = Math.max(0, Math.min(100, (sourceTime / duration) * 100));
      document.getElementById('playhead').style.left = `${left}%`;
    }

    function renderFinishStatus(finish) {
      const status = document.getElementById('finishStatus');
      if (!finish || !finish.output_path) {
        status.textContent = 'No final montage created yet.';
        return;
      }
      const clipText = finish.clip_count === 1 ? '1 clip' : `${finish.clip_count} clips`;
      const musicText = finish.music && finish.music.title ? ` | music ${finish.music.title}` : '';
      status.innerHTML = `Created final montage from ${clipText}${musicText}: <a href="${finish.video_url}" target="_blank" rel="noreferrer">Open Video</a>`;
    }

    function renderThinking(clip) {
      const thinking = clip.thinking || {};
      document.getElementById('thinkingMode').textContent = thinking.mode || 'Heuristics';
      const meta = [
        ['Score', formatNumber(thinking.score ?? clip.score, 2)],
        ['Tier', thinking.tier ?? '-'],
        ['Bucket', thinking.bucket || clip.label || '-'],
        ['Trim', `${formatNumber(thinking.duration ?? clipDuration(clip), 2)}s`],
      ];
      document.getElementById('thinkingMeta').innerHTML = meta.map(([label, value]) => `
        <div class="thinking-cell">
          <div class="thinking-label">${escapeHtml(label)}</div>
          <div class="thinking-value">${escapeHtml(value)}</div>
        </div>
      `).join('');

      const reasons = Array.isArray(thinking.reasons) ? thinking.reasons : [];
      document.getElementById('thinkingReasons').innerHTML = reasons.length
        ? reasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join('')
        : '<li>No extra readout available for this clip.</li>';
      document.getElementById('thinkingStatus').textContent =
        thinking.ml_status || 'Deterministic scorer with review memory.';
    }

    function clearButtonFocus() {
      if (document.activeElement instanceof HTMLButtonElement) {
        document.activeElement.blur();
      }
    }

    function isEditableTarget(target) {
      return target instanceof HTMLTextAreaElement || target instanceof HTMLInputElement || target.isContentEditable;
    }

    async function label(decision) {
      const clip = session.clips[index];
      const nextIndex = Math.min(index + 1, Math.max(session.clips.length - 1, 0));
      await fetch('/api/label', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ clip_id: clip.clip_id, decision })
      });
      await loadSession({ index: nextIndex });
      clearButtonFocus();
    }

    function updateTrimFromSlider(which, value) {
      updatePendingEdit(which, Number(value), true);
    }

    function updateTrimFromNumber(which, value) {
      updatePendingEdit(which, Number(value), true);
    }

    function updatePendingEdit(which, value, seek) {
      const clip = currentClip();
      if (!clip || !Number.isFinite(value)) {
        return;
      }
      const edit = activeEdit(clip);
      edit[which] = value;
      pendingEdit = clampEdit(clip, edit, which);
      document.getElementById('trimStatus').textContent = 'Trim changed.';
      renderTrimControls();
      renderSourceRail();
      renderTimeline();
      if (seek) {
        seekToEditStart();
      }
      window.clearTimeout(trimSaveTimer);
      trimSaveTimer = window.setTimeout(saveClipEdit, 650);
    }

    function markFromPlayhead(which) {
      const clip = currentClip();
      if (!clip) {
        return;
      }
      const player = document.getElementById('player');
      const sourceMode = player.dataset.sourceMode === '1';
      const sourceTime = sourceMode ? player.currentTime : clip.start + player.currentTime;
      updatePendingEdit(which, sourceTime, false);
      clearButtonFocus();
    }

    async function saveClipEdit() {
      const clip = currentClip();
      if (!clip) {
        return;
      }
      const edit = activeEdit(clip);
      const response = await fetch('/api/clip-edit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ clip_id: clip.clip_id, start: edit.start, end: edit.end })
      });
      const result = await response.json();
      if (!response.ok || !result.ok) {
        document.getElementById('trimStatus').textContent = result.error || 'Trim not saved.';
        return;
      }
      clip.edit = result.edit;
      pendingEdit = null;
      document.getElementById('trimStatus').textContent = 'Trim saved.';
      renderTrimControls();
      renderSourceRail();
      renderTimeline();
      clearButtonFocus();
    }

    async function saveClipFeedback() {
      const clip = session.clips[index];
      const feedback = document.getElementById('clipFeedback').value;
      await fetch('/api/clip-feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ clip_id: clip.clip_id, feedback })
      });
      await loadSession({ clipId: clip.clip_id });
      document.getElementById('clipFeedbackStatus').textContent = 'Clip feedback saved.';
      clearButtonFocus();
    }

    async function saveSessionFeedback() {
      const clip = session.clips[index];
      const feedback = document.getElementById('sessionFeedback').value;
      await fetch('/api/session-feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ feedback })
      });
      await loadSession({ clipId: clip ? clip.clip_id : '' });
      document.getElementById('sessionFeedbackStatus').textContent = 'Session feedback saved.';
      clearButtonFocus();
    }

    async function finishReview() {
      const button = document.getElementById('finishButton');
      const status = document.getElementById('finishStatus');
      button.disabled = true;
      status.textContent = 'Creating final montage...';
      try {
        const response = await fetch('/api/finish', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({})
        });
        const result = await response.json();
        if (!response.ok || !result.ok) {
          throw new Error(result.error || 'Unable to create final montage.');
        }
        session.finish = result;
        renderFinishStatus(result);
      } catch (error) {
        status.textContent = error instanceof Error ? error.message : 'Unable to create final montage.';
      } finally {
        button.disabled = false;
        clearButtonFocus();
      }
    }

    function move(delta) {
      index += delta;
      pendingEdit = null;
      render();
      clearButtonFocus();
    }

    function selectClip(nextIndex) {
      index = nextIndex;
      pendingEdit = null;
      render();
      clearButtonFocus();
    }

    function togglePlayback() {
      const player = document.getElementById('player');
      if (player.paused) {
        seekIntoRange();
        player.play().catch(() => {});
      } else {
        player.pause();
      }
      clearButtonFocus();
    }

    function seekToEditStart() {
      const clip = currentClip();
      if (!clip) {
        return;
      }
      const player = document.getElementById('player');
      const sourceMode = player.dataset.sourceMode === '1';
      const edit = activeEdit(clip);
      player.currentTime = sourceMode ? edit.start : Math.max(0, edit.start - clip.start);
      updatePlayhead();
    }

    function seekIntoRange() {
      const clip = currentClip();
      if (!clip) {
        return;
      }
      const player = document.getElementById('player');
      const sourceMode = player.dataset.sourceMode === '1';
      const edit = activeEdit(clip);
      const sourceTime = sourceMode ? player.currentTime : clip.start + player.currentTime;
      if (sourceTime < edit.start || sourceTime > edit.end) {
        seekToEditStart();
      }
    }

    function enforceTrimWindow() {
      const clip = currentClip();
      if (!clip) {
        return;
      }
      const player = document.getElementById('player');
      const sourceMode = player.dataset.sourceMode === '1';
      const edit = activeEdit(clip);
      const sourceTime = sourceMode ? player.currentTime : clip.start + player.currentTime;
      if (sourceTime > edit.end) {
        const wasPlaying = !player.paused;
        seekToEditStart();
        if (wasPlaying) {
          player.play().catch(() => {});
        }
      }
      updatePlayhead();
    }

    function shortName(path) {
      return String(path || '').split(/[\\\\/]/).pop() || 'clip';
    }

    function formatNumber(value, digits) {
      const number = Number(value);
      return Number.isFinite(number) ? number.toFixed(digits) : '-';
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
    }

    document.addEventListener('keydown', (event) => {
      if (isEditableTarget(event.target)) {
        return;
      }
      if (event.altKey || event.ctrlKey || event.metaKey) {
        return;
      }
      if ((event.key === ' ' || event.code === 'Space') && event.target instanceof HTMLButtonElement) {
        event.preventDefault();
        return;
      }
      if (event.key === 'ArrowLeft') {
        event.preventDefault();
        move(-1);
      }
      if (event.key === 'ArrowRight') {
        event.preventDefault();
        move(1);
      }
    });

    document.getElementById('player').addEventListener('timeupdate', enforceTrimWindow);
    loadSession();
    window.setInterval(refreshFinishStatus, 5000);
  </script>
</body>
</html>"""
if __name__ == "__main__":
    raise SystemExit(main())
