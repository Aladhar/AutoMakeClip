from __future__ import annotations

import json
import math
import subprocess
import ssl
import wave
from pathlib import Path
from typing import List, Optional
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen
from urllib.error import URLError

import numpy as np

from .config import MusicConfig
from .inputs import _yt_dlp_command
from .types import MusicTrack
from .ffmpeg import extract_audio_samples

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class MusicSelectionError(RuntimeError):
    """Raised when a music track cannot be selected."""


def select_music_track(mood: str, target_bpm: float, output_dir: Path, config: MusicConfig) -> MusicTrack:
    output_dir.mkdir(parents=True, exist_ok=True)
    sources = _resolved_sources(config.source)
    library_error: Optional[MusicSelectionError] = None
    youtube_error: Optional[MusicSelectionError] = None

    if "library" in sources or "spotify" in sources:
        try:
            library_tracks = _load_local_library_tracks(resolve_music_manifest_path(config.library_manifest))
        except MusicSelectionError as error:
            library_tracks = []
            library_error = error
        if "spotify" in sources and "library" not in sources:
            library_tracks = [track for track in library_tracks if _is_spotify_track(track)]
        if library_tracks:
            selected = max(library_tracks, key=lambda track: _local_track_score(track, mood, target_bpm))
            if selected.local_path:
                _ensure_track_drop_times(selected, selected.local_path)
            return selected

    if "youtube" in sources:
        try:
            youtube_tracks = _load_youtube_manifest_tracks(resolve_music_manifest_path(config.library_manifest))
        except MusicSelectionError as error:
            youtube_tracks = []
            youtube_error = error
        for candidate in sorted(youtube_tracks, key=lambda track: _local_track_score(track, mood, target_bpm), reverse=True):
            try:
                if candidate.local_path is None or not candidate.local_path.exists():
                    candidate.local_path = _download_youtube_audio(candidate, output_dir, config)
                # Convert downloaded audio to WAV for more reliable analysis, then run detection.
                try:
                    analysis_path = _convert_to_wav(candidate.local_path, output_dir)
                except MusicSelectionError:
                    analysis_path = candidate.local_path
                _ensure_track_drop_times(candidate, analysis_path)
                return candidate
            except MusicSelectionError as error:
                youtube_error = error
                continue

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
                _ensure_track_drop_times(candidate, candidate.local_path)
                return candidate
            except MusicSelectionError:
                continue

    if config.allow_generated_fallback or "generated" in sources:
        return _synthesize_fallback_track(mood, target_bpm, output_dir)
    if youtube_error is not None:
        raise youtube_error
    if library_error is not None:
        raise library_error
    if "spotify" in sources:
        raise MusicSelectionError(
            "No usable Spotify music was found. Add Spotify-tagged tracks to music_library/tracks.json with "
            "a local audio file path; Spotify URLs alone cannot be mixed into an MP4."
        )
    raise MusicSelectionError(
        "No usable real music track was found. Add licensed songs to music_library/tracks.json "
        "or pass --allow-generated-fallback if you want the synthetic backup."
    )


def resolve_music_manifest_path(manifest_value: str | Path) -> Path:
    manifest_path = Path(manifest_value).expanduser()
    if manifest_path.is_absolute():
        return manifest_path

    cwd_candidate = (Path.cwd() / manifest_path).resolve()
    if cwd_candidate.exists():
        return cwd_candidate

    project_candidate = (PROJECT_ROOT / manifest_path).resolve()
    if project_candidate.exists():
        return project_candidate

    return cwd_candidate


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
    skipped_spotify_records = 0
    for record in payload:
        if not isinstance(record, dict):
            continue
        record_is_spotify = _record_is_spotify(record)
        local_path_value = record.get("local_path")
        if not local_path_value:
            if record_is_spotify:
                skipped_spotify_records += 1
            continue
        local_path = Path(str(local_path_value)).expanduser()
        if not local_path.is_absolute():
            local_path = (manifest_path.parent / local_path).resolve()
        if not local_path.exists():
            if record_is_spotify:
                skipped_spotify_records += 1
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
    if not tracks and skipped_spotify_records:
        raise MusicSelectionError(
            "Spotify music entries need a local audio file path that exists. Spotify links are metadata only; "
            "the renderer cannot pull encrypted Spotify streams directly."
        )
    return tracks


def _load_youtube_manifest_tracks(manifest_path: Path) -> List[MusicTrack]:
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
        if not isinstance(record, dict) or not _record_is_youtube(record):
            continue
        local_path = _record_local_path(record, manifest_path)
        source_kind = str(record.get("source_kind") or "youtube")
        if _record_is_youtube_playlist(record) and source_kind == "youtube":
            source_kind = "youtube_playlist"
        url = str(record.get("download_url") or record.get("page_url") or "")
        if not url:
            continue
        tracks.append(
            MusicTrack(
                title=str(record.get("title") or "YouTube Music"),
                artist=str(record.get("artist") or "YouTube"),
                license_name=str(record.get("license_name") or "User-supplied YouTube audio - verify rights before publishing"),
                page_url=str(record.get("page_url") or url),
                download_url=url,
                bpm=_coerce_float(record.get("bpm")),
                tags=_coerce_tags(record.get("tags")),
                local_path=local_path,
                source_kind=source_kind,
                usage_note=str(record.get("usage_note") or "Downloaded with yt-dlp from a user-supplied YouTube URL."),
                youtube_safe=bool(record.get("youtube_safe", False)),
                trend_score=float(record.get("trend_score") or 0.0),
                drop_times=_coerce_float_list(record.get("drop_times")) or _default_drop_times(_coerce_float(record.get("bpm"))),
            )
        )
    return tracks


def _record_local_path(record: dict, manifest_path: Path) -> Optional[Path]:
    local_path_value = record.get("local_path")
    if not local_path_value:
        return None
    local_path = Path(str(local_path_value)).expanduser()
    if not local_path.is_absolute():
        local_path = (manifest_path.parent / local_path).resolve()
    return local_path if local_path.exists() else None


def _download_youtube_audio(track: MusicTrack, output_dir: Path, config: MusicConfig) -> Path:
    url = track.download_url or track.page_url
    if not _looks_like_youtube_url(url):
        raise MusicSelectionError(f"Music track is not a YouTube URL: {url}")
    output_dir.mkdir(parents=True, exist_ok=True)
    playlist_args = (
        [
            "--yes-playlist",
            "--playlist-end",
            str(max(1, config.query_limit)),
            "--max-downloads",
            "1",
            "--ignore-errors",
        ]
        if _is_youtube_playlist_track(track)
        else ["--no-playlist"]
    )
    command = [
        *_yt_dlp_command(),
        *playlist_args,
        "-f",
        "ba/best",
        "--extract-audio",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
        "-o",
        str(output_dir / "%(title).120B [%(id)s].%(ext)s"),
        "--print",
        "after_move:filepath",
        url,
    ]
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(config.download_timeout_seconds, 30) * 4,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MusicSelectionError(f"Unable to download YouTube music with yt-dlp: {error}") from error
    audio_paths = [
        Path(line.strip()).expanduser().resolve()
        for line in process.stdout.splitlines()
        if line.strip()
    ]
    for audio_path in audio_paths:
        if audio_path.exists() and audio_path.suffix.lower() in {".mp3", ".m4a", ".opus", ".webm", ".wav", ".ogg"}:
            return audio_path
    if process.returncode != 0:
        raise MusicSelectionError(process.stderr.strip() or f"Unable to download YouTube music from {url}")
    raise MusicSelectionError(f"`yt-dlp` did not report a usable audio file for {url}")


def _download_file(url: str, destination: Path, timeout_seconds: int) -> None:
    try:
        with _open_url(url, timeout_seconds=timeout_seconds) as response, destination.open("wb") as handle:
            handle.write(response.read())
    except (URLError, OSError) as error:
        raise MusicSelectionError(f"Unable to download music track: {error}") from error


def _ensure_track_drop_times(track: MusicTrack, analysis_path: Path) -> None:
    if track.drop_times:
        return
    detected = auto_detect_drop_times(analysis_path)
    if detected:
        track.drop_times = detected
        return
    duration_seconds = _audio_duration_seconds(analysis_path)
    track.drop_times = _default_drop_times(track.bpm, duration_seconds=duration_seconds if duration_seconds > 0 else 96.0)


def _convert_to_wav(input_path: Path, output_dir: Path) -> Path:
    """Convert an audio file to WAV using ffmpeg; return the WAV path."""
    wav_path = output_dir / (input_path.stem + ".wav")
    if wav_path.exists():
        return wav_path
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                str(wav_path),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise MusicSelectionError(f"Unable to convert audio to WAV: {error}") from error
    return wav_path


def _tags_for_mood(mood: str) -> List[str]:
    if mood == "aggro":
        return ["electronic", "hip_hop", "dance"]
    if mood == "chaotic":
        return ["funk", "electronic", "breakbeat"]
    return ["electronic", "house", "hip_hop"]


def _resolved_sources(source: str) -> List[str]:
    normalized = source.strip().lower()
    if normalized == "auto":
        return ["youtube", "library", "ccmixter"]
    if normalized == "youtube":
        return ["youtube"]
    if normalized == "spotify":
        return ["spotify"]
    if normalized == "library":
        return ["library"]
    if normalized == "ccmixter":
        return ["ccmixter"]
    if normalized == "generated":
        return ["generated"]
    raise MusicSelectionError(f"Unsupported music source: {source}")


def _local_track_score(track: MusicTrack, mood: str, target_bpm: float) -> float:
    tag_bonus = sum(1.0 for tag in track.tags if tag.lower() in _tags_for_mood(mood))
    bpm_penalty = 0.0
    # De-emphasize BPM for YouTube tracks, as user-provided drop_times are often more important.
    if not _is_youtube_track(track):
        bpm_penalty = _track_distance(track, target_bpm)
    youtube_bonus = 8.0 if track.youtube_safe else 0.0
    source_bonus = 0.0
    if _is_spotify_track(track):
        source_bonus = 7.0
    elif _is_youtube_track(track):
        source_bonus = 6.5
    elif track.source_kind in {"youtube_audio_library", "creator_music"}:
        source_bonus = 6.0
    return track.trend_score * 2.5 + tag_bonus * 3.0 + youtube_bonus + source_bonus - bpm_penalty * 0.2


def _track_distance(track: MusicTrack, target_bpm: float) -> float:
    if track.bpm is None:
        return 25.0
    return abs(track.bpm - target_bpm)


def _is_spotify_track(track: MusicTrack) -> bool:
    return (
        track.source_kind.lower() == "spotify"
        or "open.spotify.com" in track.page_url.lower()
        or track.download_url.lower().startswith("spotify:")
    )


def _record_is_spotify(record: dict) -> bool:
    return (
        str(record.get("source_kind") or "").lower() == "spotify"
        or "open.spotify.com" in str(record.get("page_url") or "").lower()
        or str(record.get("download_url") or "").lower().startswith("spotify:")
    )


def _is_youtube_track(track: MusicTrack) -> bool:
    return track.source_kind.lower() in {"youtube", "youtube_playlist"} or _looks_like_youtube_url(track.page_url) or _looks_like_youtube_url(track.download_url)


def _is_youtube_playlist_track(track: MusicTrack) -> bool:
    return track.source_kind.lower() == "youtube_playlist" or _youtube_url_has_playlist(track.download_url) or _youtube_url_has_playlist(track.page_url)


def _record_is_youtube(record: dict) -> bool:
    return (
        str(record.get("source_kind") or "").lower() in {"youtube", "youtube_playlist"}
        or _looks_like_youtube_url(str(record.get("page_url") or ""))
        or _looks_like_youtube_url(str(record.get("download_url") or ""))
    )


def _record_is_youtube_playlist(record: dict) -> bool:
    return (
        str(record.get("source_kind") or "").lower() == "youtube_playlist"
        or _youtube_url_has_playlist(str(record.get("page_url") or ""))
        or _youtube_url_has_playlist(str(record.get("download_url") or ""))
    )


def _looks_like_youtube_url(value: str) -> bool:
    parsed = urlparse(value)
    host = parsed.netloc.lower()
    return parsed.scheme in {"http", "https"} and ("youtube.com" in host or "youtu.be" in host)


def _youtube_url_has_playlist(value: str) -> bool:
    if not _looks_like_youtube_url(value):
        return False
    parsed = urlparse(value)
    return bool(parse_qs(parsed.query).get("list"))


def _coerce_tags(value) -> List[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.replace(",", " ").split() if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


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


def auto_detect_drop_times(audio_path: Path) -> List[float]:
    """
    Automated DSP Filter: Detects music drops via spectral flux and energy transients.
    Replaces manual JSON entry by analyzing the actual audio waveform.
    """
    sample_rate = 22050
    try:
        samples = extract_audio_samples(audio_path, sample_rate)
    except Exception:
        return []
    if samples.size < sample_rate * 4:
        return []

    samples = np.asarray(samples, dtype=np.float32)
    peak = float(np.max(np.abs(samples))) or 0.0
    if peak <= 1e-5:
        return []
    samples = samples / peak

    window_size = 2048
    hop_length = 512
    if len(samples) < window_size + hop_length:
        return []

    frame_count = 1 + (len(samples) - window_size) // hop_length
    window = np.hanning(window_size).astype(np.float32)
    spectra = np.zeros((frame_count, window_size // 2 + 1), dtype=np.float32)
    rms = np.zeros(frame_count, dtype=np.float32)

    for index in range(frame_count):
        start = index * hop_length
        frame = samples[start : start + window_size]
        if frame.shape[0] < window_size:
            frame = np.pad(frame, (0, window_size - frame.shape[0]))
        rms[index] = float(np.sqrt(np.mean(np.square(frame)) + 1e-10))
        spectra[index] = np.abs(np.fft.rfft(frame * window))

    flux = np.zeros(frame_count, dtype=np.float32)
    if frame_count > 1:
        diffs = spectra[1:] - spectra[:-1]
        flux[1:] = np.maximum(diffs, 0.0).mean(axis=1)

    frequencies = np.fft.rfftfreq(window_size, d=1.0 / sample_rate)
    bass_mask = (frequencies >= 40.0) & (frequencies <= 180.0)
    body_mask = (frequencies >= 180.0) & (frequencies <= 2000.0)
    treble_mask = frequencies >= 3500.0
    bass_energy = spectra[:, bass_mask].mean(axis=1) if np.any(bass_mask) else np.zeros(frame_count, dtype=np.float32)
    body_energy = spectra[:, body_mask].mean(axis=1) if np.any(body_mask) else np.zeros(frame_count, dtype=np.float32)
    treble_energy = spectra[:, treble_mask].mean(axis=1) if np.any(treble_mask) else np.zeros(frame_count, dtype=np.float32)

    rms_smooth = _smooth_curve(rms, window_size=11)
    bass_smooth = _smooth_curve(bass_energy, window_size=11)
    body_smooth = _smooth_curve(body_energy, window_size=11)
    treble_smooth = _smooth_curve(treble_energy, window_size=11)
    flux_smooth = _smooth_curve(flux, window_size=5)

    rms_jump = np.maximum(rms_smooth - _rolling_mean(rms_smooth, window_size=43), 0.0)
    bass_jump = np.maximum(bass_smooth - _rolling_mean(bass_smooth, window_size=43), 0.0)
    body_jump = np.maximum(body_smooth - _rolling_mean(body_smooth, window_size=43), 0.0)

    novelty = (
        0.38 * _zscore_like(flux_smooth)
        + 0.24 * _zscore_like(rms_jump)
        + 0.22 * _zscore_like(bass_jump)
        + 0.10 * _zscore_like(body_jump)
        + 0.06 * _zscore_like(treble_smooth)
    )
    absolute_energy = 0.65 * _zscore_like(rms_smooth) + 0.35 * _zscore_like(bass_smooth)

    minimum_time = 4.0
    spacing_seconds = 6.0
    novelty_threshold = max(1.1, float(np.percentile(novelty, 87)))
    energy_threshold = max(0.15, float(np.percentile(absolute_energy, 55)))

    scored_candidates: List[tuple[float, float]] = []
    for index in range(2, frame_count - 2):
        if novelty[index] < novelty_threshold:
            continue
        if absolute_energy[index] < energy_threshold:
            continue
        if novelty[index] < novelty[index - 1] or novelty[index] < novelty[index + 1]:
            continue
        timestamp = (index * hop_length) / sample_rate
        if timestamp < minimum_time:
            continue
        score = (
            0.55 * float(novelty[index])
            + 0.25 * float(absolute_energy[index])
            + 0.20 * float(_zscore_like(bass_smooth)[index])
        )
        scored_candidates.append((timestamp, score))

    if not scored_candidates:
        return []

    selected: List[float] = []
    for timestamp, _ in sorted(scored_candidates, key=lambda item: item[1], reverse=True):
        if any(abs(timestamp - existing) < spacing_seconds for existing in selected):
            continue
        selected.append(timestamp)
        if len(selected) >= 8:
            break

    return [round(timestamp, 3) for timestamp in sorted(selected)]


def _audio_duration_seconds(audio_path: Path, sample_rate: int = 22050) -> float:
    try:
        samples = extract_audio_samples(audio_path, sample_rate)
    except Exception:
        return 0.0
    return float(samples.size) / float(sample_rate) if samples.size else 0.0


def _smooth_curve(values: np.ndarray, window_size: int) -> np.ndarray:
    if values.size == 0 or window_size <= 1:
        return values.astype(np.float32, copy=False)
    window_size = min(window_size, values.size)
    kernel = np.ones(window_size, dtype=np.float32) / float(window_size)
    return np.convolve(values, kernel, mode="same").astype(np.float32)


def _rolling_mean(values: np.ndarray, window_size: int) -> np.ndarray:
    if values.size == 0:
        return values.astype(np.float32, copy=False)
    window_size = max(3, min(window_size, values.size))
    return _smooth_curve(values, window_size)


def _zscore_like(values: np.ndarray) -> np.ndarray:
    if values.size == 0:
        return values.astype(np.float32, copy=False)
    median = float(np.median(values))
    spread = max(float(np.percentile(values, 75) - np.percentile(values, 25)), float(np.std(values)) * 0.5, 1e-6)
    normalized = (values - median) / spread
    return np.clip(normalized.astype(np.float32), -3.0, 5.0)


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
