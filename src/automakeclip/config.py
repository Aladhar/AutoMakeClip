from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class AnalysisConfig:
    analysis_fps: int = 4
    analysis_width: int = 320
    audio_sample_rate: int = 22050
    score_smoothing_frames: int = 5
    highlight_threshold_percentile: float = 78.0
    min_segment_seconds: float = 1.8
    max_segment_seconds: float = 7.5
    pre_roll_seconds: float = 2.1
    post_roll_seconds: float = 3.2
    comedy_segment_seconds: Tuple[float, float] = (1.3, 2.4)
    intro_seconds: float = 2.4
    event_pre_roll_seconds: float = 2.0
    event_post_roll_seconds: float = 2.8
    event_merge_gap_seconds: float = 4.25
    generic_peak_threshold_percentile: float = 84.0
    generic_peak_min_spacing_seconds: float = 3.2
    generic_peak_quota: int = 3
    fallback_fight_quota: int = 1
    killfeed_roi: Tuple[float, float, float, float] = (0.72, 0.03, 0.27, 0.25)
    hud_roi: Tuple[float, float, float, float] = (0.28, 0.70, 0.44, 0.26)
    center_roi: Tuple[float, float, float, float] = (0.22, 0.16, 0.56, 0.58)
    weights: Dict[str, float] = field(
        default_factory=lambda: {
            "visual_motion": 0.24,
            "killfeed_motion": 0.28,
            "hud_motion": 0.10,
            "audio_rms": 0.18,
            "audio_flux": 0.14,
            "scene_change": 0.06,
        }
    )


@dataclass
class MusicConfig:
    source: str = "auto"
    preferred_licenses: List[str] = field(default_factory=lambda: ["by"])
    query_limit: int = 24
    download_timeout_seconds: int = 30
    library_manifest: str = "music_library/tracks.json"
    allow_generated_fallback: bool = True


@dataclass
class RenderConfig:
    width: int = 2560
    height: int = 1440
    fps: int = 30
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    crf: int = 18
    preset: str = "medium"
    audio_bitrate: str = "192k"
    music_gain: float = 0.20
    game_audio_gain: float = 1.10
    intro_title: str = "OVERWATCH HIGHLIGHTS"
    intro_subtitle: str = "slay, chaos, and one goofy moment"
    beat_snap_tolerance_seconds: float = 0.40


@dataclass
class CacheConfig:
    enabled: bool = True
    directory_name: str = ".automakeclip_cache"


@dataclass
class AppConfig:
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    music: MusicConfig = field(default_factory=MusicConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
