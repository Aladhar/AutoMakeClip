from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List
from urllib.parse import unquote, urlparse

from .config import AppConfig, RenderConfig
from .inputs import InputResolutionError, resolve_inputs
from .types import Segment


@dataclass
class ReviewClip:
    clip_id: str
    source_path: str
    start: float
    end: float
    score: float
    label: str
    note: str
    filename: str


@dataclass
class ReviewState:
    session_dir: Path
    clips_dir: Path
    clips: List[ReviewClip]
    labels_path: Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review sampled highlight clips with a yes/no UI.")
    parser.add_argument(
        "--input",
        required=True,
        nargs="+",
        action="append",
        help="Source MP4 files, folders, or YouTube URLs.",
    )
    parser.add_argument("--samples", type=int, default=18, help="How many sampled clips to generate for review.")
    parser.add_argument("--port", type=int, default=8765, help="Port for the local review UI.")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface for the local review UI.")
    parser.add_argument("--session-dir", default="", help="Optional review session directory.")
    parser.add_argument("--no-cache", action="store_true", help="Disable per-video analysis cache.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = AppConfig()
    try:
        from .analysis import analyze_gameplay, extract_candidate_segments
        from .cache import load_analysis_cache, store_analysis_cache
        from .ffmpeg import FFmpegError, ensure_ffmpeg, probe_video, run_ffmpeg
    except ModuleNotFoundError as error:
        print(
            f"Missing Python dependency: {error.name}. Run `python3 -m pip install -e .` first.",
            file=sys.stderr,
        )
        return 2

    session_dir = _session_dir(args.session_dir)
    clips_dir = session_dir / "clips"
    labels_path = session_dir / "labels.json"
    session_path = session_dir / "session.json"
    try:
        ensure_ffmpeg()
        input_paths = resolve_inputs(args.input, download_root=Path.cwd() / ".automakeclip_downloads")
        cache_dir = Path.cwd() / config.cache.directory_name
        use_cache = config.cache.enabled and not args.no_cache

        candidates: List[Segment] = []
        total_inputs = len(input_paths)
        for index, input_path in enumerate(input_paths, start=1):
            prefix = f"[{index}/{total_inputs}]"
            cached = load_analysis_cache(cache_dir, input_path, config.analysis) if use_cache else None
            if cached is not None:
                metadata, timeline = cached
                print(f"{prefix} cache hit {input_path.name}", file=sys.stderr)
            else:
                print(f"{prefix} cache miss {input_path.name}", file=sys.stderr)
                metadata = probe_video(input_path)
                timeline = analyze_gameplay(input_path, metadata, config.analysis)
                if use_cache:
                    store_analysis_cache(cache_dir, input_path, config.analysis, metadata, timeline)
            for segment in extract_candidate_segments(timeline, metadata, config.analysis):
                candidates.append(
                    Segment(
                        start=segment.start,
                        end=segment.end,
                        score=segment.score,
                        label=segment.label,
                        note=segment.note,
                        source_path=str(input_path),
                    )
                )

        samples = select_review_samples(candidates, args.samples)
        if not samples:
            raise RuntimeError("No candidate clips were generated for review.")

        clips_dir.mkdir(parents=True, exist_ok=True)
        preview_render = RenderConfig(width=1280, height=720, fps=30, crf=22, preset="veryfast")
        review_clips: List[ReviewClip] = []
        for index, segment in enumerate(samples, start=1):
            clip_id = f"clip-{index:03d}"
            filename = f"{clip_id}.mp4"
            output_path = clips_dir / filename
            _render_review_clip(run_ffmpeg, segment, output_path, preview_render)
            review_clips.append(
                ReviewClip(
                    clip_id=clip_id,
                    source_path=segment.source_path or "",
                    start=segment.start,
                    end=segment.end,
                    score=segment.score,
                    label=segment.label,
                    note=segment.note,
                    filename=filename,
                )
            )

        state = ReviewState(session_dir=session_dir, clips_dir=clips_dir, clips=review_clips, labels_path=labels_path)
        _ensure_labels_file(labels_path)
        session_payload = {
            "created_at": datetime.now().isoformat(),
            "session_dir": str(session_dir),
            "clips": [asdict(clip) for clip in review_clips],
        }
        session_path.write_text(json.dumps(session_payload, indent=2), encoding="utf-8")
        server = ThreadingHTTPServer((args.host, args.port), _build_handler(state))
        print(f"Review UI: http://{args.host}:{args.port}", file=sys.stderr)
        print(f"Session Dir: {session_dir}", file=sys.stderr)
        server.serve_forever()
        return 0
    except KeyboardInterrupt:
        print("Review UI stopped.", file=sys.stderr)
        return 0
    except (FFmpegError, InputResolutionError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1


def select_review_samples(candidates: List[Segment], sample_count: int) -> List[Segment]:
    if sample_count <= 0 or not candidates:
        return []

    ordered = sorted(candidates, key=lambda item: item.score, reverse=True)
    third = max(1, len(ordered) // 3)
    buckets = [
        ordered[:third],
        ordered[third : third * 2],
        ordered[third * 2 :],
    ]
    quotas = [
        max(1, sample_count // 2),
        max(1, int(round(sample_count * 0.33))),
        max(1, sample_count - (max(1, sample_count // 2) + max(1, int(round(sample_count * 0.33))))),
    ]

    selected: List[Segment] = []
    for bucket, quota in zip(buckets, quotas):
        if not bucket:
            continue
        stride = max(1, len(bucket) // max(quota, 1))
        for candidate in bucket[::stride]:
            if len(selected) >= sample_count:
                break
            if any(_same_source_overlap(candidate, existing) > 0.55 for existing in selected):
                continue
            selected.append(candidate)
            if sum(1 for item in selected if item in bucket) >= quota:
                break
    if len(selected) < sample_count:
        for candidate in ordered:
            if len(selected) >= sample_count:
                break
            if any(_same_source_overlap(candidate, existing) > 0.55 for existing in selected):
                continue
            selected.append(candidate)
    return selected[:sample_count]


def _render_review_clip(run_ffmpeg, segment: Segment, output_path: Path, config: RenderConfig) -> None:
    if not segment.source_path:
        raise RuntimeError("Review segment is missing its source_path.")
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


def _session_dir(raw_value: str) -> Path:
    if raw_value:
        return Path(raw_value).expanduser().resolve()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (Path.cwd() / "review_sessions" / timestamp).resolve()


def _ensure_labels_file(labels_path: Path) -> None:
    labels_path.parent.mkdir(parents=True, exist_ok=True)
    if not labels_path.exists():
        labels_path.write_text(json.dumps({"decisions": {}}, indent=2), encoding="utf-8")


def _load_labels(labels_path: Path) -> Dict[str, Dict[str, str]]:
    try:
        payload = json.loads(labels_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {"decisions": {}}
    decisions = payload.get("decisions")
    if not isinstance(decisions, dict):
        decisions = {}
    return {"decisions": decisions}


def _store_labels(labels_path: Path, decisions: Dict[str, str]) -> None:
    labels_path.write_text(json.dumps({"decisions": decisions}, indent=2), encoding="utf-8")


def _build_handler(state: ReviewState):
    class ReviewHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_html(_review_html())
                return
            if parsed.path == "/api/session":
                labels = _load_labels(state.labels_path)["decisions"]
                payload = {
                    "session_dir": str(state.session_dir),
                    "clips": [
                        {
                            **asdict(clip),
                            "video_url": f"/clips/{clip.filename}",
                            "decision": labels.get(clip.clip_id, ""),
                        }
                        for clip in state.clips
                    ],
                    "decisions": labels,
                }
                self._send_json(payload)
                return
            if parsed.path.startswith("/clips/"):
                relative = unquote(parsed.path.removeprefix("/clips/"))
                clip_path = (state.clips_dir / relative).resolve()
                if clip_path.parent != state.clips_dir or not clip_path.exists():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                body = clip_path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/api/label":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                content_length = 0
            raw_body = self.rfile.read(content_length)
            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except json.JSONDecodeError:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return

            clip_id = str(payload.get("clip_id") or "")
            decision = str(payload.get("decision") or "")
            if clip_id not in {clip.clip_id for clip in state.clips}:
                self.send_error(HTTPStatus.BAD_REQUEST, "Unknown clip_id")
                return
            if decision not in {"yes", "no", "skip"}:
                self.send_error(HTTPStatus.BAD_REQUEST, "decision must be yes, no, or skip")
                return

            labels = _load_labels(state.labels_path)["decisions"]
            labels[clip_id] = decision
            _store_labels(state.labels_path, labels)
            self._send_json({"ok": True, "clip_id": clip_id, "decision": decision})

        def log_message(self, format: str, *args) -> None:
            return

        def _send_json(self, payload: Dict[str, object]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return ReviewHandler


def _review_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AutoMakeClip Review</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0f1419;
      --panel: #18212b;
      --accent: #76e2a6;
      --danger: #ff8e72;
      --text: #f3f7fb;
      --muted: #9bb1c5;
    }
    body {
      margin: 0;
      font-family: ui-sans-serif, system-ui, sans-serif;
      background: radial-gradient(circle at top, #1f3342, var(--bg) 55%);
      color: var(--text);
    }
    main {
      max-width: 960px;
      margin: 0 auto;
      padding: 24px;
    }
    .card {
      background: rgba(24, 33, 43, 0.92);
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 18px;
      padding: 20px;
      box-shadow: 0 18px 42px rgba(0,0,0,0.25);
    }
    video {
      width: 100%;
      border-radius: 14px;
      background: black;
    }
    .row {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      margin-top: 14px;
    }
    .buttons {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }
    button {
      border: 0;
      border-radius: 999px;
      padding: 12px 18px;
      font-size: 15px;
      font-weight: 700;
      cursor: pointer;
    }
    .yes { background: var(--accent); color: #0a1c14; }
    .no { background: var(--danger); color: #2c1107; }
    .skip { background: #c9d6e2; color: #1a2530; }
    .meta {
      color: var(--muted);
      line-height: 1.5;
      margin-top: 12px;
    }
    .pill {
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      background: rgba(255,255,255,0.09);
      margin-right: 8px;
      margin-bottom: 8px;
    }
  </style>
</head>
<body>
  <main>
    <div class="card">
      <h1>Clip Review</h1>
      <p id="progress">Loading...</p>
      <video id="player" controls autoplay></video>
      <div class="row">
        <div class="buttons">
          <button class="yes" onclick="label('yes')">Yes</button>
          <button class="no" onclick="label('no')">No</button>
          <button class="skip" onclick="label('skip')">Skip</button>
        </div>
        <div class="buttons">
          <button class="skip" onclick="move(-1)">Prev</button>
          <button class="skip" onclick="move(1)">Next</button>
        </div>
      </div>
      <div class="meta">
        <div id="pills"></div>
        <div id="source"></div>
        <div id="note"></div>
      </div>
    </div>
  </main>
  <script>
    let session = { clips: [] };
    let index = 0;

    async function loadSession() {
      const response = await fetch('/api/session');
      session = await response.json();
      render();
    }

    function render() {
      if (!session.clips.length) {
        document.getElementById('progress').textContent = 'No clips found.';
        return;
      }
      index = Math.max(0, Math.min(index, session.clips.length - 1));
      const clip = session.clips[index];
      const labeled = Object.entries(session.decisions || {});
      const yesCount = labeled.filter(([, value]) => value === 'yes').length;
      const noCount = labeled.filter(([, value]) => value === 'no').length;
      const skipCount = labeled.filter(([, value]) => value === 'skip').length;
      document.getElementById('progress').textContent =
        `Clip ${index + 1} / ${session.clips.length} | yes ${yesCount} | no ${noCount} | skip ${skipCount}`;
      const player = document.getElementById('player');
      if (player.dataset.clipId !== clip.clip_id) {
        player.src = clip.video_url;
        player.dataset.clipId = clip.clip_id;
        player.play().catch(() => {});
      }
      document.getElementById('pills').innerHTML =
        `<span class="pill">${clip.label}</span><span class="pill">score ${clip.score.toFixed(2)}</span><span class="pill">decision ${clip.decision || 'pending'}</span>`;
      document.getElementById('source').textContent =
        `${clip.source_path} | ${clip.start.toFixed(2)}s - ${clip.end.toFixed(2)}s`;
      document.getElementById('note').textContent = clip.note || '';
    }

    async function label(decision) {
      const clip = session.clips[index];
      await fetch('/api/label', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ clip_id: clip.clip_id, decision })
      });
      await loadSession();
      if (index < session.clips.length - 1) {
        index += 1;
        render();
      }
    }

    function move(delta) {
      index += delta;
      render();
    }

    document.addEventListener('keydown', (event) => {
      if (event.key === 'y') label('yes');
      if (event.key === 'n') label('no');
      if (event.key === 's') label('skip');
      if (event.key === 'ArrowLeft') move(-1);
      if (event.key === 'ArrowRight') move(1);
    });

    loadSession();
  </script>
</body>
</html>"""


def _same_source_overlap(first: Segment, second: Segment) -> float:
    if first.source_path != second.source_path:
        return 0.0
    intersection = max(0.0, min(first.end, second.end) - max(first.start, second.start))
    if intersection <= 0.0:
        return 0.0
    return intersection / max(min(first.duration, second.duration), 1e-6)


if __name__ == "__main__":
    raise SystemExit(main())
