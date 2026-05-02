from __future__ import annotations

from pathlib import Path
from typing import Optional

from .config import RenderConfig
from .ffmpeg import probe_video, run_ffmpeg, FFmpegError


def _safe_duration(value: Optional[float]) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def merge_with_transition(prev_path: Path, next_path: Path, out_path: Path, config: RenderConfig, style: str = "smooth") -> None:
    """Merge two already-rendered segment files into a single output using
    an FFmpeg `xfade` transition plus optional overlay asset when available.

    Both inputs are expected to already be scaled/padded to the final render
    resolution (as produced by `_render_segment`). The function computes a
    safe transition duration based on the inputs and falls back to a simple
    concat if a transition cannot be applied.
    """
    prev_meta = probe_video(prev_path)
    next_meta = probe_video(next_path)
    prev_dur = _safe_duration(prev_meta.duration)
    next_dur = _safe_duration(next_meta.duration)

    # Determine usable transition duration
    D = min(config.transition_duration, prev_dur / 2.0 if prev_dur > 0 else 0.0, next_dur / 2.0 if next_dur > 0 else 0.0)
    if D <= 0.0:
        # Fallback: simple concat (copies streams)
        run_ffmpeg([
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            "-",
            "-c:v",
            "copy",
            "-c:a",
            "copy",
            str(out_path),
        ])
        return

    offset = max(0.0, prev_dur - D)

    fire_asset = Path(config.transition_asset_dir) / "fire.webm"

    # Map requested style to an xfade transition name when appropriate.
    transition_name = "wipeleft"
    if style == "smooth":
        transition_name = "fade"
    elif style != "fire":
        transition_name = style

    # Build a simple xfade + acrossfade command. If a fire asset exists, try
    # to overlay/blend it on top of the transition region; if overlay fails,
    # fall back to plain xfade.
    def _run_xfade(with_fire: bool) -> None:
        try:
            if with_fire and fire_asset.exists():
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(prev_path),
                    "-i",
                    str(next_path),
                    "-i",
                    str(fire_asset),
                    "-filter_complex",
                    (
                        f"[0:v][1:v]xfade=transition={transition_name}:duration={D:.3f}:offset={offset:.3f},format=rgba[vxf];"
                        f"[2:v]trim=duration={D:.3f},setpts=PTS-STARTPTS,format=rgba[fire];"
                        f"[vxf][fire]blend=all_mode=screen:all_opacity=0.9,format=yuv420p[vout];"
                        f"[0:a]afade=t=out:st={offset:.3f}:d={D:.3f}[a0];"
                        f"[1:a]afade=t=in:st=0:d={D:.3f}[a1];"
                        f"[a0][a1]acrossfade=d={D:.3f}[aout]"
                    ),
                    "-map",
                    "[vout]",
                    "-map",
                    "[aout]",
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
                    str(out_path),
                ]
            else:
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(prev_path),
                    "-i",
                    str(next_path),
                    "-filter_complex",
                    (
                        f"[0:v][1:v]xfade=transition={transition_name}:duration={D:.3f}:offset={offset:.3f},format=yuv420p[v];"
                        f"[0:a][1:a]acrossfade=d={D:.3f}[a]"
                    ),
                    "-map",
                    "[v]",
                    "-map",
                    "[a]",
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
                    str(out_path),
                ]
            run_ffmpeg(cmd)
        except FFmpegError:
            if with_fire:
                # Retry without fire overlay
                _run_xfade(False)
            else:
                raise

    # Try style-specific renderers: support 'smooth' (soft fade),
    # 'fire' (overlay blended), or fallback to the basic xfade.
    if style == "fire":
        _run_xfade(True)
    elif style == "smooth":
        # prefer a softer cross-dissolve fade transition
        try:
            cmd = [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(prev_path),
                "-i",
                str(next_path),
                "-filter_complex",
                (
                    f"[0:v][1:v]xfade=transition=fade:duration={D:.3f}:offset={offset:.3f},format=yuv420p[v];"
                    f"[0:a][1:a]acrossfade=d={D:.3f}[a]"
                ),
                "-map",
                "[v]",
                "-map",
                "[a]",
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
                str(out_path),
            ]
            run_ffmpeg(cmd)
        except FFmpegError:
            # fallback to the basic xfade implementation
            _run_xfade(False)
    else:
        _run_xfade(False)
