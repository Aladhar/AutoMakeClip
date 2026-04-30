from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class VideoMetadata:
    duration: float
    width: int
    height: int
    fps: float
    sample_rate: int = 0
    kill_events: List[Dict[str, Any]] = field(default_factory=list)
    source_game: str = ""
    trigger_name: str = ""


@dataclass
class AnalysisTimeline:
    times: List[float]
    visual_motion: List[float]
    killfeed_motion: List[float]
    hud_motion: List[float]
    center_motion: List[float]
    audio_rms: List[float]
    audio_flux: List[float]
    scene_change: List[float]
    gameplay_confidence: List[float]
    scores: List[float]
    duration: float


@dataclass
class Segment:
    start: float
    end: float
    score: float
    label: str
    note: str = ""
    source_path: Optional[str] = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["duration"] = round(self.duration, 3)
        return payload


@dataclass
class MusicTrack:
    title: str
    artist: str
    license_name: str
    page_url: str
    download_url: str
    bpm: Optional[float] = None
    tags: List[str] = field(default_factory=list)
    local_path: Optional[Path] = None
    source_kind: str = ""
    usage_note: str = ""
    youtube_safe: bool = False
    trend_score: float = 0.0

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        if self.local_path is not None:
            payload["local_path"] = str(self.local_path)
        return payload


@dataclass
class MontagePlan:
    input_paths: List[Path]
    output_path: Path
    title: str
    subtitle: str
    target_seconds: float
    source_duration: float
    source_durations: Dict[str, float]
    segments: List[Segment]
    mood: str
    target_bpm: float
    music: Optional[MusicTrack] = None

    @property
    def montage_seconds(self) -> float:
        return round(sum(segment.duration for segment in self.segments), 3)

    def to_dict(self) -> Dict[str, object]:
        return {
            "input_paths": [str(path) for path in self.input_paths],
            "output_path": str(self.output_path),
            "title": self.title,
            "subtitle": self.subtitle,
            "target_seconds": self.target_seconds,
            "source_duration": self.source_duration,
            "source_durations": self.source_durations,
            "montage_seconds": self.montage_seconds,
            "mood": self.mood,
            "target_bpm": self.target_bpm,
            "segments": [segment.to_dict() for segment in self.segments],
            "music": self.music.to_dict() if self.music else None,
        }
