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
            _mix_music(stitched_path, plan.music, output_path, render_config)
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


def _mix_music(stitched_path: Path, music: MusicTrack, output_path: Path, config: RenderConfig) -> None:
    montage_duration = probe_video(stitched_path).duration
    fade_out_start = max(0.0, montage_duration - 1.4)
    music_path = music.local_path
    if music_path is None:
        raise RuntimeError("Music track was selected without a local file.")

    filter_complex = (
        f"[0:a]volume={config.game_audio_gain},aresample=48000[game];"
        f"[1:a]atrim=0:{montage_duration:.3f},asetpts=N/SR/TB,volume={config.music_gain},"
        f"afade=t=in:st=0:d=0.8,afade=t=out:st={fade_out_start:.3f}:d=1.1,aresample=48000[music];"
        "[music][game]sidechaincompress=threshold=0.04:ratio=8:attack=10:release=220:makeup=1.5[ducked];"
        "[game][ducked]amix=inputs=2:weights=1 1:normalize=0[a]"
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
