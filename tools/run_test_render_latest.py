#!/usr/bin/env python3
"""Load the latest review session plan and run a test render with transitions.
"""
import json
import sys
from pathlib import Path

from automakeclip.types import MontagePlan, Segment, MusicTrack
from automakeclip.render import render_montage
from automakeclip.config import AppConfig

PLAN_PATH = Path("review_sessions/20260430_203917/final_review_montage.plan.json")

if not PLAN_PATH.exists():
    print("Plan file not found:", PLAN_PATH)
    sys.exit(2)

payload = json.loads(PLAN_PATH.read_text(encoding="utf-8"))

segments = []
for s in payload.get("segments", []):
    seg = Segment(
        start=float(s.get("start", 0.0)),
        end=float(s.get("end", 0.0)),
        score=float(s.get("score", 0.0)),
        label=str(s.get("label", "")),
        note=str(s.get("note", "")),
        source_path=str(s.get("source_path", "")) if s.get("source_path") else None,
        highlight_time=float(s.get("highlight_time")) if s.get("highlight_time") is not None else None,
    )
    segments.append(seg)

music_payload = payload.get("music")
music = None
if music_payload:
    music = MusicTrack(
        title=music_payload.get("title", ""),
        artist=music_payload.get("artist", ""),
        license_name=music_payload.get("license_name", ""),
        page_url=music_payload.get("page_url", ""),
        download_url=music_payload.get("download_url", ""),
        bpm=music_payload.get("bpm"),
        tags=music_payload.get("tags", []),
        local_path=Path(music_payload["local_path"]) if music_payload.get("local_path") else None,
        source_kind=music_payload.get("source_kind", ""),
        usage_note=music_payload.get("usage_note", ""),
        youtube_safe=bool(music_payload.get("youtube_safe", False)),
        trend_score=float(music_payload.get("trend_score", 0.0)),
        drop_times=music_payload.get("drop_times", []),
    )

plan = MontagePlan(
    input_paths=[Path(p) for p in payload.get("input_paths", [])],
    output_path=Path(payload.get("output_path")),
    title=payload.get("title", "OVERWATCH HIGHLIGHTS"),
    subtitle=payload.get("subtitle", ""),
    target_seconds=float(payload.get("target_seconds", 0.0)),
    source_duration=float(payload.get("source_duration", 0.0)),
    source_durations=payload.get("source_durations", {}),
    segments=segments,
    mood=payload.get("mood", ""),
    target_bpm=float(payload.get("target_bpm", 0.0)),
    music=music,
)

app_config = AppConfig()
# Enable transitions for this test
app_config.render.transition_style = "fire"
app_config.render.transition_duration = 0.6

print("Starting test render ->", plan.output_path)
try:
    render_montage(plan=plan, analysis_config=app_config.analysis, render_config=app_config.render, keep_temp=False)
except Exception as e:
    print("Render failed:", e)
    raise
print("Render finished ->", plan.output_path)
