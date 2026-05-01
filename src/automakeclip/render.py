import json
import tempfile
from pathlib import Path
from typing import Dict, List

from .config import AnalysisConfig, RenderConfig
from .ffmpeg import probe_video, run_ffmpeg
from .types import MontagePlan, MusicTrack, Segment


def snap_segments_to_beats(segments: List[Segment], bpm: float, duration_by_source: Dict[str, float], tolerance: float) -> List[Segment]:
    if bpm <= 0:
        return segments

    beat = 60.0 / bpm
    snapped: List[Segment] = []
    for segment in segments:
        if segment.label == "silly":
            snapped.append(segment)
            continue

        current = segment.duration
        beat_options = [beat * multiple for multiple in range(4, 17)]
        target = min(beat_options, key=lambda option: abs(option - current))
        if abs(target - current) > tolerance:
            snapped.append(segment)
            continue

        delta = target - current
        segment_duration_cap = duration_by_source.get(segment.source_path or "", segment.end + delta)
        end = min(segment_duration_cap, segment.end + delta)
        start = max(0.0, segment.start - max(0.0, delta) * 0.15)
        snapped.append(
            Segment(
                start=start,
                end=end,
                score=segment.score,
                label=segment.label,
                note=segment.note,
                source_path=segment.source_path,
                highlight_time=segment.highlight_time,
            )
        )
    return snapped


def render_montage(plan: MontagePlan, analysis_config: AnalysisConfig, render_config: RenderConfig, keep_temp: bool) -> Path:
    output_path = plan.output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="automakeclip_") as temp_root:
        temp_dir = Path(temp_root)
        intro_path = temp_dir / "intro.mp4"
        stitched_path = temp_dir / "stitched.mp4"

        _create_intro(intro_path, plan, render_config, analysis_config.intro_seconds)

        segment_paths: List[Path] = []
        for index, segment in enumerate(plan.segments, start=1):
            segment_path = temp_dir / f"segment_{index:02d}.mp4"
            _render_segment(segment, segment_path, render_config)
            segment_paths.append(segment_path)

        concat_entries = [intro_path] + segment_paths
        concat_file = temp_dir / "concat.txt"
        concat_file.write_text("".join(f"file '{path.as_posix()}'\n" for path in concat_entries), encoding="utf-8")

        run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c:v",
                render_config.video_codec,
                "-preset",
                render_config.preset,
                "-crf",
                str(render_config.crf),
                "-c:a",
                render_config.audio_codec,
                "-b:a",
                render_config.audio_bitrate,
                str(stitched_path),
            ]
        )

        if plan.music and plan.music.local_path:
            _mix_music(stitched_path, plan, output_path, render_config, analysis_config.intro_seconds)
        else:
            run_ffmpeg(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(stitched_path),
                    "-c:v",
                    "copy",
                    "-c:a",
                    "copy",
                    str(output_path),
                ]
            )

        if keep_temp:
            kept = output_path.parent / f"{output_path.stem}_temp"
            kept.mkdir(parents=True, exist_ok=True)
            for item in temp_dir.iterdir():
                target = kept / item.name
                target.write_bytes(item.read_bytes())

    _write_sidecars(plan)
    return output_path


def _create_intro(output_path: Path, plan: MontagePlan, config: RenderConfig, intro_seconds: float = 2.4) -> None:
    lead_source_path = Path(plan.segments[0].source_path) if plan.segments and plan.segments[0].source_path else plan.input_paths[0]
    lead_source_duration = plan.source_durations.get(str(lead_source_path), plan.source_duration)
    lead_time = max(0.0, min(plan.segments[0].start if plan.segments else 0.0, max(lead_source_duration - 0.1, 0.0)))
    still_path = output_path.with_suffix(".jpg")

    run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-ss",
            f"{lead_time:.3f}",
            "-i",
            str(lead_source_path),
            "-frames:v",
            "1",
            str(still_path),
        ]
    )

    filter_complex = (
        f"scale={config.width}:{config.height}:force_original_aspect_ratio=increase,"
        f"crop={config.width}:{config.height},"
        f"fps={config.fps},"
        "boxblur=18:2,"
        "eq=contrast=1.08:saturation=1.15,"
        "drawbox=x=0:y=0:w=iw:h=ih:color=black@0.42:t=fill,"
        "drawbox=x=100:y=720:w=1720:h=180:color=black@0.22:t=fill,"
        "fade=t=in:st=0:d=0.35,"
        f"fade=t=out:st={max(0.0, intro_seconds - 0.55):.3f}:d=0.45"
    )

    run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-loop",
            "1",
            "-t",
            f"{intro_seconds:.3f}",
            "-i",
            str(still_path),
            "-f",
            "lavfi",
            "-t",
            f"{intro_seconds:.3f}",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-vf",
            filter_complex,
            "-shortest",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(config.fps),
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


def _render_segment(segment: Segment, output_path: Path, config: RenderConfig) -> None:
    if not segment.source_path:
        raise RuntimeError("Segment is missing its source_path.")

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
            "-r",
            str(config.fps),
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


def compute_music_start_offset(plan: MontagePlan, intro_seconds: float, montage_duration: float) -> float:
    music = plan.music
    if music is None or not music.drop_times:
        return 0.0

    anchors = _montage_anchor_points(plan.segments, intro_seconds)
    if not anchors:
        return 0.0

    candidate_offsets = {0.0}
    usable_drops = sorted(drop_time for drop_time in music.drop_times if drop_time >= 0.0)
    for drop_time in usable_drops[:8]:
        for anchor_time, _, _ in anchors[:6]:
            offset = drop_time - anchor_time
            if offset >= 0.0:
                candidate_offsets.add(round(offset, 3))

    best_offset = 0.0
    best_error = float("inf")
    for offset in candidate_offsets:
        error = _music_alignment_error(anchors, usable_drops, offset, montage_duration)
        if error < best_error:
            best_error = error
            best_offset = offset
    return max(0.0, best_offset)


def _mix_music(stitched_path: Path, plan: MontagePlan, output_path: Path, config: RenderConfig, intro_seconds: float) -> None:
    music = plan.music
    if music is None:
        raise RuntimeError("Music mixing was requested without a selected track.")
    montage_duration = probe_video(stitched_path).duration
    fade_out_start = max(0.0, montage_duration - 1.4)
    music_path = music.local_path
    if music_path is None:
        raise RuntimeError("Music track was selected without a local file.")
    music_start_offset = compute_music_start_offset(plan, intro_seconds=intro_seconds, montage_duration=montage_duration)
    music_end = music_start_offset + montage_duration + 0.25

    filter_complex = _build_music_mix_filter(
        config=config,
        music_start_offset=music_start_offset,
        music_end=music_end,
        fade_out_start=fade_out_start,
    )

    run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(stitched_path),
            "-stream_loop",
            "-1",
            "-i",
            str(music_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            config.audio_codec,
            "-b:a",
            config.audio_bitrate,
            str(output_path),
        ]
    )


def _build_music_mix_filter(
    config: RenderConfig,
    music_start_offset: float,
    music_end: float,
    fade_out_start: float,
) -> str:
    return (
        f"[0:a]volume={config.game_audio_gain},highpass=f=120,aresample=48000[game];"
        f"[1:a]atrim=start={music_start_offset:.3f}:end={music_end:.3f},asetpts=PTS-STARTPTS,volume={config.music_gain},"
        f"afade=t=in:st=0:d=0.6,afade=t=out:st={fade_out_start:.3f}:d=1.1,aresample=48000[music];"
        "[game][music]sidechaincompress=threshold=0.08:ratio=10:attack=15:release=250:makeup=1.0[ducked_game];"
        "[ducked_game][music]amix=inputs=2:weights=0.85 1.0:normalize=0,alimiter=limit=0.95[a]"
    )


def _write_sidecars(plan: MontagePlan) -> None:
    plan_path = plan.output_path.with_suffix(".plan.json")
    credits_path = plan.output_path.with_suffix(".credits.txt")
    plan_path.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")

    lines = [
        f"Title: {plan.title}",
        f"Subtitle: {plan.subtitle}",
        f"Mood: {plan.mood}",
        f"Target BPM: {plan.target_bpm}",
    ]
    if plan.music:
        lines.extend(
            [
                "",
                "Music Attribution",
                f"Track: {plan.music.title}",
                f"Artist: {plan.music.artist}",
                f"License: {plan.music.license_name}",
                f"Source Page: {plan.music.page_url}",
                f"Download URL: {plan.music.download_url}",
            ]
        )
    credits_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _montage_anchor_points(segments: List[Segment], intro_seconds: float) -> List[tuple[float, float, str]]:
    anchors: List[tuple[float, float, str]] = []
    current_time = intro_seconds
    for segment in segments:
        local_highlight = segment.highlight_time if segment.highlight_time is not None else (segment.start + segment.end) / 2.0
        local_offset = min(max(local_highlight - segment.start, 0.0), segment.duration)
        anchor_time = current_time + local_offset
        weight = 1.8 if segment.label == "highlight" else 1.0 if segment.label == "fight" else 0.45
        anchors.append((anchor_time, weight, segment.label))
        current_time += segment.duration
    return anchors


def _music_alignment_error(
    anchors: List[tuple[float, float, str]],
    drop_times: List[float],
    music_start_offset: float,
    montage_duration: float,
) -> float:
    shifted_drops = [
        drop_time - music_start_offset
        for drop_time in drop_times
        if 0.0 <= drop_time - music_start_offset <= montage_duration + 6.0
    ]
    if not shifted_drops:
        return 9999.0

    error = 0.0
    for index, (anchor_time, weight, label) in enumerate(anchors[:6]):
        nearest = min(abs(anchor_time - drop_time) for drop_time in shifted_drops)
        emphasis = 1.4 if index == 0 and label == "highlight" else 1.0
        error += min(nearest, 6.0) * weight * emphasis
    return error + music_start_offset * 0.02
