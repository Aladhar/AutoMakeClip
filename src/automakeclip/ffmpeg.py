from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any, Dict, Iterator, Tuple

import numpy as np

from .types import VideoMetadata


class FFmpegError(RuntimeError):
    """Raised when ffmpeg or ffprobe fails."""


def ensure_ffmpeg() -> None:
    missing = [binary for binary in ("ffmpeg", "ffprobe") if shutil.which(binary) is None]
    if missing:
        raise FFmpegError(
            "Missing required binaries: {}. Install ffmpeg so both ffmpeg and ffprobe are available.".format(
                ", ".join(missing)
            )
        )


def run_ffmpeg(args: list[str]) -> subprocess.CompletedProcess:
    process = subprocess.run(
        args,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if process.returncode != 0:
        raise FFmpegError(process.stderr.strip() or "ffmpeg command failed")
    return process


def probe_video(path: Path) -> VideoMetadata:
    process = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if process.returncode != 0:
        raise FFmpegError(process.stderr.strip() or "ffprobe failed")

    payload = json.loads(process.stdout)
    video_stream = next((stream for stream in payload.get("streams", []) if stream.get("codec_type") == "video"), None)
    audio_stream = next((stream for stream in payload.get("streams", []) if stream.get("codec_type") == "audio"), None)
    if video_stream is None:
        raise FFmpegError(f"No video stream found in {path}")

    duration = float(payload.get("format", {}).get("duration") or video_stream.get("duration") or 0.0)
    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    fps = _parse_fraction(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "0/1")
    sample_rate = int(audio_stream.get("sample_rate") or 0) if audio_stream else 0
    steelseries_meta = _parse_steelseries_metadata(payload.get("format", {}).get("tags", {}))
    return VideoMetadata(
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        sample_rate=sample_rate,
        kill_events=_extract_kill_events(steelseries_meta),
        source_game=str(steelseries_meta.get("last_game_name") or ""),
        trigger_name=str(steelseries_meta.get("trigger_name") or ""),
    )


def extract_audio_samples(path: Path, sample_rate: int) -> np.ndarray:
    try:
        process = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(sample_rate),
                "-f",
                "f32le",
                "-acodec",
                "pcm_f32le",
                "-",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        if path.suffix.lower() == ".wav":
            return _extract_wav_samples(path, sample_rate)
        raise
    if process.returncode != 0:
        if path.suffix.lower() == ".wav":
            return _extract_wav_samples(path, sample_rate)
        raise FFmpegError(process.stderr.decode("utf-8", errors="ignore").strip() or "audio extraction failed")

    samples = np.frombuffer(process.stdout, dtype=np.float32)
    if samples.size == 0:
        return np.zeros(sample_rate, dtype=np.float32)
    return samples


def _extract_wav_samples(path: Path, sample_rate: int) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        channel_count = max(1, handle.getnchannels())
        sample_width = handle.getsampwidth()
        source_rate = handle.getframerate()
        frame_count = handle.getnframes()
        raw = handle.readframes(frame_count)

    if not raw:
        return np.zeros(sample_rate, dtype=np.float32)
    if sample_width == 1:
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sample_width == 2:
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_width == 4:
        samples = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise FFmpegError(f"Unsupported WAV sample width: {sample_width}")

    if channel_count > 1:
        complete_frames = samples.size - (samples.size % channel_count)
        samples = samples[:complete_frames].reshape((-1, channel_count)).mean(axis=1)
    if source_rate > 0 and source_rate != sample_rate and samples.size:
        output_size = max(1, int(round(samples.size * sample_rate / float(source_rate))))
        source_positions = np.linspace(0.0, 1.0, num=samples.size, dtype=np.float32)
        target_positions = np.linspace(0.0, 1.0, num=output_size, dtype=np.float32)
        samples = np.interp(target_positions, source_positions, samples).astype(np.float32)
    return samples.astype(np.float32, copy=False)


def iter_analysis_frames(path: Path, fps: int, width: int, source: VideoMetadata) -> Iterator[Tuple[float, np.ndarray]]:
    height = max(2, int(round((width * source.height / max(source.width, 1)) / 2.0) * 2))
    frame_size = width * height * 3
    process = subprocess.Popen(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            f"fps={fps},scale={width}:{height}",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdout is None:
        raise FFmpegError("unable to stream analysis frames")

    index = 0
    while True:
        buffer = process.stdout.read(frame_size)
        if not buffer:
            break
        if len(buffer) < frame_size:
            break
        frame = np.frombuffer(buffer, dtype=np.uint8).reshape((height, width, 3))
        yield index / float(fps), frame
        index += 1

    stderr = process.stderr.read().decode("utf-8", errors="ignore") if process.stderr else ""
    return_code = process.wait()
    if return_code != 0:
        raise FFmpegError(stderr.strip() or "frame extraction failed")


def _parse_fraction(value: str) -> float:
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        denominator_value = float(denominator or 1)
        return float(numerator) / denominator_value if denominator_value else 0.0
    return float(value)


def _parse_steelseries_metadata(tags: Dict[str, str]) -> Dict[str, Any]:
    chunks = []
    for key in sorted(tags):
        if key.startswith("STEELSERIES_META"):
            chunks.append(tags[key])
    if not chunks:
        return {}
    raw = "".join(chunks)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_kill_events(payload: Dict[str, Any]) -> list[Dict[str, Any]]:
    events = payload.get("gamesense_events", [])
    if not isinstance(events, list):
        return []

    normalized = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if str(event.get("type") or "").upper() != "KILL":
            continue
        try:
            timestamp = float(event.get("clip_timestamp"))
        except (TypeError, ValueError):
            continue
        normalized.append(
            {
                "type": "KILL",
                "timestamp": timestamp,
                "name": str(event.get("unlocalized_display_name") or event.get("display_name_suffix") or "ELIMINATION"),
                "previewable": bool(event.get("previewable", True)),
                "game": str(event.get("game") or ""),
            }
        )
    normalized.sort(key=lambda item: float(item["timestamp"]))
    return normalized
