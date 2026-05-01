from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional, Tuple

from .config import AnalysisConfig
from .types import AnalysisTimeline, VideoMetadata


CACHE_VERSION = 2


def load_analysis_cache(cache_dir: Path, source_path: Path, config: AnalysisConfig) -> Optional[Tuple[VideoMetadata, AnalysisTimeline]]:
    cache_path = _cache_path(cache_dir, source_path)
    if not cache_path.exists():
        return None

    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    stat = source_path.stat()
    if payload.get("cache_version") != CACHE_VERSION:
        return None
    if payload.get("source_path") != str(source_path):
        return None
    if payload.get("file_size") != stat.st_size:
        return None
    if payload.get("file_mtime_ns") != stat.st_mtime_ns:
        return None
    if payload.get("analysis_config") != _normalized_config(config):
        return None

    metadata_payload = payload.get("metadata")
    timeline_payload = payload.get("timeline")
    if not isinstance(metadata_payload, dict) or not isinstance(timeline_payload, dict):
        return None

    try:
        metadata = VideoMetadata(**metadata_payload)
        timeline = AnalysisTimeline(**timeline_payload)
    except TypeError:
        return None
    return metadata, timeline


def store_analysis_cache(
    cache_dir: Path,
    source_path: Path,
    config: AnalysisConfig,
    metadata: VideoMetadata,
    timeline: AnalysisTimeline,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    stat = source_path.stat()
    payload = {
        "cache_version": CACHE_VERSION,
        "source_path": str(source_path),
        "file_size": stat.st_size,
        "file_mtime_ns": stat.st_mtime_ns,
        "analysis_config": _normalized_config(config),
        "metadata": asdict(metadata),
        "timeline": asdict(timeline),
    }
    cache_path = _cache_path(cache_dir, source_path)
    cache_path.write_text(json.dumps(payload), encoding="utf-8")
    return cache_path


def load_cached_analysis_entries(cache_dir: Path, config: AnalysisConfig) -> List[Tuple[Path, VideoMetadata, AnalysisTimeline]]:
    if not cache_dir.exists():
        return []

    entries: List[Tuple[Path, VideoMetadata, AnalysisTimeline]] = []
    for cache_path in sorted(cache_dir.glob("*.json")):
        entry = _load_cache_payload(cache_path, config)
        if entry is not None:
            entries.append(entry)
    entries.sort(key=lambda item: str(item[0]).lower())
    return entries


def _cache_path(cache_dir: Path, source_path: Path) -> Path:
    digest = hashlib.sha1(str(source_path).encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.json"


def _load_cache_payload(cache_path: Path, config: AnalysisConfig) -> Optional[Tuple[Path, VideoMetadata, AnalysisTimeline]]:
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    source_value = payload.get("source_path")
    if not isinstance(source_value, str):
        return None
    source_path = Path(source_value)
    if not source_path.exists():
        return None

    try:
        stat = source_path.stat()
    except OSError:
        return None

    if payload.get("cache_version") != CACHE_VERSION:
        return None
    if payload.get("file_size") != stat.st_size:
        return None
    if payload.get("file_mtime_ns") != stat.st_mtime_ns:
        return None
    if payload.get("analysis_config") != _normalized_config(config):
        return None

    metadata_payload = payload.get("metadata")
    timeline_payload = payload.get("timeline")
    if not isinstance(metadata_payload, dict) or not isinstance(timeline_payload, dict):
        return None

    try:
        metadata = VideoMetadata(**metadata_payload)
        timeline = AnalysisTimeline(**timeline_payload)
    except TypeError:
        return None
    return source_path, metadata, timeline


def _normalized_config(config: AnalysisConfig):
    return json.loads(json.dumps(asdict(config)))
