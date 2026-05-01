from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict

from .types import MontagePlan


def latest_render_marker_path() -> Path:
    return (Path.cwd() / "review_sessions" / "latest_render.json").resolve()


def record_latest_render(plan: MontagePlan) -> Dict[str, object]:
    marker_path = latest_render_marker_path()
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    output_path = plan.output_path.resolve()
    payload = {
        "rendered_at": datetime.now().isoformat(),
        "output_path": str(output_path),
        "plan_path": str(output_path.with_suffix(".plan.json")),
        "credits_path": str(output_path.with_suffix(".credits.txt")),
        "clip_count": len(plan.segments),
        "mood": plan.mood,
        "target_bpm": plan.target_bpm,
        "music": plan.music.to_dict() if plan.music else None,
    }
    marker_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def load_latest_render() -> Dict[str, object]:
    marker_path = latest_render_marker_path()
    if not marker_path.exists():
        return {}
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
