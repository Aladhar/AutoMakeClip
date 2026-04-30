from __future__ import annotations

import json
import math
import ssl
import wave
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import URLError

import numpy as np

from .config import MusicConfig
from .types import MusicTrack


class MusicSelectionError(RuntimeError):
    """Raised when a music track cannot be selected."""


def select_music_track(mood: str, target_bpm: float, output_dir: Path, config: MusicConfig) -> MusicTrack:
    output_dir.mkdir(parents=True, exist_ok=True)
    sources = _resolved_sources(config.source)

    if "library" in sources:
        library_tracks = _load_local_library_tracks(Path(config.library_manifest).expanduser().resolve())
        if library_tracks:
            return max(library_tracks, key=lambda track: _local_track_score(track, mood, target_bpm))

    if "ccmixter" in sources:
        tags = _tags_for_mood(mood)
        try:
            records = _fetch_ccmixter_tracks(tags, config)
        except MusicSelectionError:
            records = []

        for candidate in sorted(records, key=lambda track: _track_distance(track, target_bpm)):
            candidate.local_path = output_dir / _safe_music_filename(candidate)
            candidate.source_kind = candidate.source_kind or "ccmixter"
            try:
                if not candidate.local_path.exists():
                    _download_file(candidate.download_url, candidate.local_path, timeout_seconds=config.download_timeout_seconds)
                return candidate
            except MusicSelectionError:
                continue

    if config.allow_generated_fallback or "generated" in sources:
        return _synthesize_fallback_track(mood, target_bpm, output_dir)
    raise MusicSelectionError("No usable music track was found. Add local licensed tracks or enable generated fallback.")


def _fetch_ccmixter_tracks(tags: List[str], config: MusicConfig) -> List[MusicTrack]:
    params = {
        "f": "json",
        "limit": str(config.query_limit),
        "tags": " ".join(tags),
        "sort": "rank",
        "lic": config.preferred_licenses[0],
    }
    url = f"https://ccmixter.org/api/query?{urlencode(params)}"
    try:
        payload = _load_json(url, timeout_seconds=config.download_timeout_seconds)
    except (URLError, OSError, json.JSONDecodeError) as error:
        raise MusicSelectionError(f"Unable to fetch music from ccMixter: {error}") from error

    tracks: List[MusicTrack] = []
    for record in payload:
        artist = record.get("user_real_name") or record.get("artist_name") or record.get("user_name") or "Unknown Artist"
        title = record.get("upload_name") or record.get("title") or "Untitled Track"
        user_name = record.get("user_name") or "artist"
        upload_id = record.get("upload_id")
        if upload_id is None:
            continue
        page_url = record.get("file_page_url") or record.get("page_url") or f"https://ccmixter.org/files/{user_name}/{upload_id}"
        download_url = record.get("download_url") or f"https://ccmixter.org/download/{user_name}/{upload_id}"
        extra = record.get("upload_extra") or {}
        bpm = _coerce_float(extra.get("bpm") or record.get("bpm"))
        license_name = record.get("license_name") or "Creative Commons Attribution"
        tags_value = record.get("upload_tags") or record.get("tags") or []
        if isinstance(tags_value, str):
            tag_list = [item.strip() for item in tags_value.replace(",", " ").split() if item.strip()]
        else:
            tag_list = [str(item) for item in tags_value]
        tracks.append(
            MusicTrack(
                title=title,
                artist=artist,
                license_name=license_name,
                page_url=page_url,
                download_url=download_url,
                bpm=bpm,
                tags=tag_list,
                source_kind="ccmixter",
                usage_note="Open-web track; verify attribution and platform suitability before publishing.",
                drop_times=_default_drop_times(bpm),
                )
        )
    return tracks


def _load_local_library_tracks(manifest_path: Path) -> List[MusicTrack]:
    if not manifest_path.exists():
        return []
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MusicSelectionError(f"Unable to read music library manifest: {error}") from error

    if not isinstance(payload, list):
        raise MusicSelectionError("Music library manifest must be a JSON array.")

    tracks: List[MusicTrack] = []
    for record in payload:
        if not isinstance(record, dict):
            continue
        local_path_value = record.get("local_path")
        if not local_path_value:
            continue
        local_path = Path(str(local_path_value)).expanduser()
        if not local_path.is_absolute():
            local_path = (manifest_path.parent / local_path).resolve()
        if not local_path.exists():
            continue
        tags_value = record.get("tags") or []
        if isinstance(tags_value, str):
            tags = [item.strip() for item in tags_value.replace(",", " ").split() if item.strip()]
        else:
            tags = [str(item).strip() for item in tags_value if str(item).strip()]
        tracks.append(
            MusicTrack(
                title=str(record.get("title") or local_path.stem),
                artist=str(record.get("artist") or "Unknown Artist"),
                license_name=str(record.get("license_name") or "User-supplied license"),
                page_url=str(record.get("page_url") or "local://library"),
                download_url=str(record.get("download_url") or "local://library"),
                bpm=_coerce_float(record.get("bpm")),
                tags=tags,
                local_path=local_path,
                source_kind=str(record.get("source_kind") or "library"),
                usage_note=str(record.get("usage_note") or ""),
                youtube_safe=bool(record.get("youtube_safe", False)),
                trend_score=float(record.get("trend_score") or 0.0),
                drop_times=_coerce_float_list(record.get("drop_times")) or _default_drop_times(_coerce_float(record.get("bpm"))),
            )
        )
    return tracks


def _download_file(url: str, destination: Path, timeout_seconds: int) -> None:
    try:
        with _open_url(url, timeout_seconds=timeout_seconds) as response, destination.open("wb") as handle:
            handle.write(response.read())
    except (URLError, OSError) as error:
        raise MusicSelectionError(f"Unable to download music track: {error}") from error


def _tags_for_mood(mood: str) -> List[str]:
    if mood == "aggro":
        return ["electronic", "hip_hop", "dance"]
    if mood == "chaotic":
        return ["funk", "electronic", "breakbeat"]
    return ["electronic", "house", "hip_hop"]


def _resolved_sources(source: str) -> List[str]:
    normalized = source.strip().lower()
    if normalized == "auto":
        return ["library", "ccmixter"]
    if normalized == "library":
        return ["library"]
    if normalized == "ccmixter":
        return ["ccmixter"]
    if normalized == "generated":
        return ["generated"]
    raise MusicSelectionError(f"Unsupported music source: {source}")


def _local_track_score(track: MusicTrack, mood: str, target_bpm: float) -> float:
    tag_bonus = sum(1.0 for tag in track.tags if tag.lower() in _tags_for_mood(mood))
    bpm_penalty = _track_distance(track, target_bpm)
    youtube_bonus = 8.0 if track.youtube_safe else 0.0
    source_bonus = 6.0 if track.source_kind in {"youtube_audio_library", "creator_music"} else 0.0
    return track.trend_score * 2.5 + tag_bonus * 3.0 + youtube_bonus + source_bonus - bpm_penalty * 0.2


def _track_distance(track: MusicTrack, target_bpm: float) -> float:
    if track.bpm is None:
        return 25.0
    return abs(track.bpm - target_bpm)


def _safe_music_filename(track: MusicTrack) -> str:
    artist = "".join(character if character.isalnum() else "_" for character in track.artist.lower()).strip("_")
    title = "".join(character if character.isalnum() else "_" for character in track.title.lower()).strip("_")
    extension = ".wav" if track.download_url == "local://generated" else ".mp3"
    return f"{artist}_{title}{extension}"


def _coerce_float_list(value) -> List[float]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    values: List[float] = []
    for item in value:
        numeric = _coerce_float(item)
        if numeric is None:
            continue
        values.append(float(numeric))
    return values


def _coerce_float(value) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric):
        return None
    return numeric


def _ssl_context() -> ssl.SSLContext:
    fallback_ca_files = [
        "/opt/homebrew/etc/ca-certificates/cert.pem",
        "/etc/ssl/cert.pem",
        "/private/etc/ssl/cert.pem",
    ]
    for ca_file in fallback_ca_files:
        path = Path(ca_file)
        if path.exists():
            return ssl.create_default_context(cafile=str(path))
    return ssl.create_default_context()


def _open_url(url: str, timeout_seconds: int):
    try:
        return urlopen(url, timeout=timeout_seconds, context=_ssl_context())
    except URLError as error:
        if "CERTIFICATE_VERIFY_FAILED" not in str(error):
            raise
        return urlopen(url, timeout=timeout_seconds, context=ssl._create_unverified_context())


def _load_json(url: str, timeout_seconds: int):
    with _open_url(url, timeout_seconds=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _synthesize_fallback_track(mood: str, target_bpm: float, output_dir: Path) -> MusicTrack:
    output_path = output_dir / f"generated_{mood}_{int(round(target_bpm))}bpm.wav"
    if not output_path.exists():
        _write_generated_track(output_path, mood=mood, bpm=target_bpm)
    return MusicTrack(
        title=f"AutoMakeClip {mood.title()} Beat",
        artist="AutoMakeClip",
        license_name="Original generated track for unrestricted use",
        page_url="local://generated",
        download_url="local://generated",
        bpm=target_bpm,
        tags=[mood, "generated", "instrumental"],
        local_path=output_path,
        source_kind="generated",
        usage_note="Fallback only. Replace with a real licensed track for publishing.",
        drop_times=_default_drop_times(target_bpm, duration_seconds=96.0),
    )


def _write_generated_track(path: Path, mood: str, bpm: float, duration_seconds: float = 96.0) -> None:
    sample_rate = 44100
    beat_seconds = 60.0 / max(bpm, 1.0)
    total_samples = int(duration_seconds * sample_rate)
    audio = np.zeros(total_samples, dtype=np.float32)
    time_axis = np.arange(total_samples, dtype=np.float32) / sample_rate
    drop_times = _default_drop_times(bpm, duration_seconds=duration_seconds)

    bass_root = 55.0 if mood == "aggro" else 48.0 if mood == "chaotic" else 52.0
    pad_frequency = bass_root * (4.0 if mood == "aggro" else 3.0)

    kick_pattern = [0.0, 0.5] if mood == "aggro" else [0.0, 1.0, 2.0, 3.0]
    hat_spacing = 0.25 if mood == "aggro" else 0.5
    snare_offsets = [1.0, 3.0]

    total_beats = int(math.ceil(duration_seconds / beat_seconds))
    for beat_index in range(total_beats):
        beat_time = beat_index * beat_seconds
        energy = _energy_at_time(beat_time, drop_times)
        for offset in kick_pattern:
            _mix_kick(audio, sample_rate, beat_time + offset * beat_seconds, amplitude=0.65 + energy * 0.35)
        for offset in snare_offsets:
            _mix_snare(audio, sample_rate, beat_time + offset * beat_seconds, amplitude=0.45 + energy * 0.55)

        subdivisions = int(round(1.0 / hat_spacing))
        for hat_index in range(subdivisions * 4):
            _mix_hat(audio, sample_rate, beat_time + hat_index * hat_spacing * beat_seconds, amplitude=0.30 + energy * 0.70)

    energy_curve = np.array([_energy_at_time(float(time_value), drop_times) for time_value in time_axis], dtype=np.float32)
    bass = 0.12 * np.sin(2.0 * np.pi * bass_root * time_axis + 0.35 * np.sin(2.0 * np.pi * 0.5 * time_axis))
    bass *= 0.55 + energy_curve * 0.65
    pad = 0.08 * np.sin(2.0 * np.pi * pad_frequency * time_axis)
    pad += 0.05 * np.sin(2.0 * np.pi * pad_frequency * 1.5 * time_axis)
    pad *= 0.85 - energy_curve * 0.22
    wobble = 0.7 + 0.3 * np.sin(2.0 * np.pi * (0.15 if mood == "balanced" else 0.22) * time_axis)
    audio += bass * wobble
    audio += pad

    fade_samples = int(sample_rate * 1.0)
    fade = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
    audio[:fade_samples] *= fade
    audio[-fade_samples:] *= fade[::-1]

    peak = float(np.max(np.abs(audio))) or 1.0
    audio = np.clip(audio / peak * 0.88, -1.0, 1.0)
    pcm = (audio * 32767.0).astype(np.int16)

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


def _mix_kick(audio: np.ndarray, sample_rate: int, start_seconds: float, amplitude: float) -> None:
    _mix_percussive_tone(audio, sample_rate, start_seconds, duration_seconds=0.16, start_hz=95.0, end_hz=42.0, amplitude=0.95 * amplitude)


def _mix_snare(audio: np.ndarray, sample_rate: int, start_seconds: float, amplitude: float) -> None:
    start = int(start_seconds * sample_rate)
    duration = int(0.14 * sample_rate)
    if start >= len(audio):
        return
    end = min(len(audio), start + duration)
    length = end - start
    envelope = np.exp(-np.linspace(0.0, 8.0, length, dtype=np.float32))
    noise = (np.random.rand(length).astype(np.float32) * 2.0 - 1.0) * (0.32 * amplitude)
    tone = np.sin(2.0 * np.pi * 180.0 * np.arange(length, dtype=np.float32) / sample_rate) * (0.08 * amplitude)
    audio[start:end] += (noise + tone) * envelope


def _mix_hat(audio: np.ndarray, sample_rate: int, start_seconds: float, amplitude: float) -> None:
    start = int(start_seconds * sample_rate)
    duration = int(0.05 * sample_rate)
    if start >= len(audio):
        return
    end = min(len(audio), start + duration)
    length = end - start
    envelope = np.exp(-np.linspace(0.0, 18.0, length, dtype=np.float32))
    noise = (np.random.rand(length).astype(np.float32) * 2.0 - 1.0) * (0.10 * amplitude)
    audio[start:end] += noise * envelope


def _mix_percussive_tone(
    audio: np.ndarray,
    sample_rate: int,
    start_seconds: float,
    duration_seconds: float,
    start_hz: float,
    end_hz: float,
    amplitude: float,
) -> None:
    start = int(start_seconds * sample_rate)
    duration = int(duration_seconds * sample_rate)
    if start >= len(audio):
        return
    end = min(len(audio), start + duration)
    length = end - start
    if length <= 0:
        return
    sweep = np.linspace(start_hz, end_hz, length, dtype=np.float32)
    phase = 2.0 * np.pi * np.cumsum(sweep) / sample_rate
    envelope = np.exp(-np.linspace(0.0, 10.0, length, dtype=np.float32))
    audio[start:end] += np.sin(phase) * envelope * amplitude


def _default_drop_times(bpm: Optional[float], duration_seconds: float = 96.0) -> List[float]:
    if bpm is None or bpm <= 0:
        return []
    beat = 60.0 / bpm
    first_drop = beat * 16.0
    spacing = beat * 16.0
    drops: List[float] = []
    current = first_drop
    while current < duration_seconds:
        drops.append(round(current, 3))
        current += spacing
    return drops


def _energy_at_time(time_seconds: float, drop_times: List[float]) -> float:
    if not drop_times:
        return 0.72
    base = 0.28
    for drop_time in drop_times:
        if drop_time - 1.5 <= time_seconds < drop_time:
            ramp = (time_seconds - (drop_time - 1.5)) / 1.5
            return min(0.75, base + max(0.0, ramp) * 0.47)
        if drop_time <= time_seconds < drop_time + 4.0:
            return 1.0
    if time_seconds < drop_times[0]:
        return base
    return 0.68
