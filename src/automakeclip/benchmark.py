"""Eklipse parity benchmark: compare Eklipse-exported clip windows against local plan output.

Supports two comparison modes:
  1. **export-window comparison** — did AutoMakeClip produce comparable individual selected windows?
  2. **unique-event-group comparison** — did AutoMakeClip detect the underlying important moment at least once?

Matching rules:
  - A local clip matches an Eklipse exported clip when:
      IoU ≥ 0.50  OR  highlight-anchor timestamps are within 2.0 seconds.
  - One local clip must not satisfy multiple unrelated unique event groups.
  - Multiple overlapping Eklipse exports inside the same ``event_group`` are credited
    as one detected underlying moment in event-group metrics.
  - ``rank`` is treated as unknown when null/missing; rank metrics are omitted in that case.

Clip types:
  - ``parity_benchmark`` entries use full VODs and participate in parity comparison.
  - ``style_reference`` entries (already-shortened clips) are skipped by the parity report.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Matching thresholds (documented and deterministic)
# ---------------------------------------------------------------------------
IOU_THRESHOLD = 0.50
ANCHOR_DISTANCE_THRESHOLD_SEC = 2.0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class EklipseClip:
    """A single exported clip window from Eklipse."""

    start_sec: float
    end_sec: float
    event_type: str = ""
    event_group: str = ""
    label: str = ""
    rank: Optional[int] = None
    notes: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)

    @property
    def anchor(self) -> float:
        """Midpoint timestamp used for anchor-distance matching."""
        return (self.start_sec + self.end_sec) / 2.0


@dataclass
class LocalClip:
    """A single clip from AutoMakeClip's ``.plan.json`` segments array."""

    start: float
    end: float
    score: float = 0.0
    label: str = ""
    note: str = ""
    source_path: str = ""
    highlight_time: Optional[float] = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def anchor(self) -> float:
        if self.highlight_time is not None:
            return self.highlight_time
        return (self.start + self.end) / 2.0


@dataclass
class ExportWindowMatch:
    """Result of matching one Eklipse clip to one local clip."""

    eklipse_clip: EklipseClip
    local_clip: Optional[LocalClip]
    iou: float = 0.0
    anchor_distance: float = 0.0
    matched: bool = False


@dataclass
class EventGroupResult:
    """Result of unique-event-group comparison."""

    event_group: str
    event_type: str = ""
    label: str = ""
    matched: bool = False
    matched_by_local_label: str = ""


@dataclass
class ParityReport:
    """Full parity report for one VOD comparison."""

    vod_id: str = ""
    eklipse_clip_count: int = 0
    local_clip_count: int = 0
    matched_clips: int = 0
    missed_clips: int = 0
    extra_clips: int = 0
    export_recall: float = 0.0
    export_precision: float = 0.0
    start_errors: List[float] = field(default_factory=list)
    end_errors: List[float] = field(default_factory=list)
    mean_start_error: float = 0.0
    mean_end_error: float = 0.0
    export_matches: List[ExportWindowMatch] = field(default_factory=list)
    missed_exports: List[EklipseClip] = field(default_factory=list)
    extra_locals: List[LocalClip] = field(default_factory=list)

    # Event-group metrics
    unique_event_group_count: int = 0
    matched_event_groups: int = 0
    missed_event_groups: int = 0
    unique_event_recall: float = 0.0
    event_group_results: List[EventGroupResult] = field(default_factory=list)

    # Rank metrics (omitted when rank is not available)
    rank_available: bool = False
    rank_agreement_count: int = 0
    rank_agreement_total: int = 0

    # Metadata
    eklipse_file: str = ""
    local_file: str = ""


# ---------------------------------------------------------------------------
# IOU calculation
# ---------------------------------------------------------------------------
def _compute_iou(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    """Compute intersection-over-union for two time intervals."""
    intersection_start = max(start_a, start_b)
    intersection_end = min(end_a, end_b)
    intersection = max(0.0, intersection_end - intersection_start)

    union_start = min(start_a, start_b)
    union_end = max(end_a, end_b)
    union = max(0.0, union_end - union_start)

    if union <= 0.0:
        return 0.0
    return intersection / union


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
def load_eklipse_clips(path: Path, clip_window_sec: float = 10.0) -> List[EklipseClip]:
    """Load Eklipse exported clips from a JSON file.

    Supports three formats:

    1. List at root: ``[{...}, ...]``
    2. Dict with ``"clips"`` key: ``{"clips": [{...}, ...]}``
    3. Session summary dict with ``"reported_highlight_events"`` key:
       Each event has ``timestamp_sec`` but no end time.  We create
       approximate clip windows of ``clip_window_sec`` seconds on each
       side of the anchor.
    """
    data = json.loads(path.read_text(encoding="utf-8"))

    clips_raw: list = []
    if isinstance(data, list):
        clips_raw = data
    elif isinstance(data, dict):
        clips_raw = data.get("clips", [])
        # Session summary format — convert timestamps to clip windows
        if not clips_raw and "reported_highlight_events" in data:
            clips_raw = _session_events_to_clip_refs(
                data["reported_highlight_events"], clip_window_sec
            )
    else:
        raise ValueError(f"Unexpected Eklipse reference structure in {path}")

    clips: List[EklipseClip] = []
    for entry in clips_raw:
        clips.append(
            EklipseClip(
                start_sec=float(entry.get("start_sec", entry.get("start", 0.0))),
                end_sec=float(entry.get("end_sec", entry.get("end", 0.0))),
                event_type=str(entry.get("event_type", "")),
                event_group=str(entry.get("event_group", "")),
                label=str(entry.get("label", entry.get("event_label", ""))),
                rank=entry.get("rank"),
                notes=str(entry.get("notes", "")),
            )
        )
    return clips


def _session_events_to_clip_refs(
    events: list, clip_window_sec: float
) -> list:
    """Convert session-summary timestamp events into approximate clip refs.

    Each event ``timestamp_sec`` becomes the anchor of a
    ``[anchor - clip_window_sec, anchor + clip_window_sec]`` window.
    Events sharing the same ``event_type`` within close proximity are
    grouped into the same ``event_group``.
    """
    refs: list = []
    # Group by event_type, assigning event_group ids
    group_counters: Dict[str, int] = {}
    for event in events:
        ts = float(event.get("timestamp_sec", 0.0))
        event_type = str(event.get("event_type", ""))
        event_label = str(event.get("event_label", event.get("label", "")))

        # Build event_group key: type + sequential id
        group_key = event_type if event_type else "unknown"
        if group_key not in group_counters:
            group_counters[group_key] = 0
        group_counters[group_key] += 1
        event_group = f"{group_key}_{group_counters[group_key]:03d}"

        refs.append({
            "start_sec": max(0.0, ts - clip_window_sec),
            "end_sec": ts + clip_window_sec,
            "event_type": event_type,
            "event_group": event_group,
            "label": event_label,
            "rank": None,
            "notes": str(event.get("notes", "")),
        })
    return refs


def load_local_plan(path: Path) -> List[LocalClip]:
    """Load local clips from an AutoMakeClip ``.plan.json`` file."""
    data = json.loads(path.read_text(encoding="utf-8"))

    segments_raw: List[Dict[str, Any]] = []
    if isinstance(data, dict):
        segments_raw = data.get("segments", [])
    elif isinstance(data, list):
        segments_raw = data

    clips: List[LocalClip] = []
    for seg in segments_raw:
        clips.append(
            LocalClip(
                start=float(seg.get("start", 0.0)),
                end=float(seg.get("end", 0.0)),
                score=float(seg.get("score", 0.0)),
                label=str(seg.get("label", "")),
                note=str(seg.get("note", "")),
                source_path=str(seg.get("source_path", "")),
                highlight_time=seg.get("highlight_time"),
            )
        )
    return clips


# ---------------------------------------------------------------------------
# Matching engine
# ---------------------------------------------------------------------------
def _match_clips(
    eklipse_clips: List[EklipseClip],
    local_clips: List[LocalClip],
) -> List[ExportWindowMatch]:
    """Match Eklipse clips to local clips using IoU or anchor-distance rules.

    Uses a greedy one-to-one matching: for each Eklipse clip, find the best
    matching local clip that hasn't already been claimed.
    """
    if not eklipse_clips or not local_clips:
        return [
            ExportWindowMatch(eklipse_clip=ec, local_clip=None, matched=False)
            for ec in eklipse_clips
        ]

    # Pre-compute all candidate matches
    candidates: List[Tuple[float, int, int]] = []  # (score, eklipse_idx, local_idx)
    for ei, ec in enumerate(eklipse_clips):
        for li, lc in enumerate(local_clips):
            iou = _compute_iou(ec.start_sec, ec.end_sec, lc.start, lc.end)
            anchor_dist = abs(ec.anchor - lc.anchor)
            is_match = iou >= IOU_THRESHOLD or anchor_dist <= ANCHOR_DISTANCE_THRESHOLD_SEC
            if is_match:
                # Higher IoU is better; lower anchor distance is better
                priority = iou * 1000.0 + (1.0 / max(anchor_dist, 0.001))
                candidates.append((priority, ei, li))

    # Sort by priority (best matches first)
    candidates.sort(key=lambda c: c[0], reverse=True)

    # Greedy one-to-one assignment
    matched_eklipse: set[int] = set()
    matched_local: set[int] = set()
    match_map: Dict[int, Tuple[int, float, float]] = {}  # eklipse_idx -> (local_idx, iou, anchor_dist)

    for _priority, ei, li in candidates:
        if ei in matched_eklipse or li in matched_local:
            continue
        iou = _compute_iou(
            eklipse_clips[ei].start_sec, eklipse_clips[ei].end_sec,
            local_clips[li].start, local_clips[li].end,
        )
        anchor_dist = abs(eklipse_clips[ei].anchor - local_clips[li].anchor)
        match_map[ei] = (li, iou, anchor_dist)
        matched_eklipse.add(ei)
        matched_local.add(li)

    # Build results
    results: List[ExportWindowMatch] = []
    for ei, ec in enumerate(eklipse_clips):
        if ei in match_map:
            li, iou, anchor_dist = match_map[ei]
            results.append(
                ExportWindowMatch(
                    eklipse_clip=ec,
                    local_clip=local_clips[li],
                    iou=iou,
                    anchor_distance=anchor_dist,
                    matched=True,
                )
            )
        else:
            results.append(ExportWindowMatch(eklipse_clip=ec, local_clip=None, matched=False))

    return results


def _compute_event_group_results(
    eklipse_clips: List[EklipseClip],
    export_matches: List[ExportWindowMatch],
) -> List[EventGroupResult]:
    """Compute unique-event-group comparison.

    Clips sharing the same ``event_group`` string are considered part of the
    same underlying event. If any clip in a group is matched, the event is
    considered detected.
    """
    groups: Dict[str, List[int]] = {}
    for idx, clip in enumerate(eklipse_clips):
        group_key = clip.event_group or f"_ungrouped_{idx}"
        groups.setdefault(group_key, []).append(idx)

    # Build a set of matched eklipse indices
    matched_indices = {idx for idx, m in enumerate(export_matches) if m.matched}

    results: List[EventGroupResult] = []
    for group_key, indices in groups.items():
        first_clip = eklipse_clips[indices[0]]
        any_matched = any(idx in matched_indices for idx in indices)

        # Find the label of the local clip that matched (if any)
        matched_local_label = ""
        if any_matched:
            for idx in indices:
                if idx in matched_indices and export_matches[idx].local_clip:
                    matched_local_label = export_matches[idx].local_clip.label
                    break

        results.append(
            EventGroupResult(
                event_group=group_key,
                event_type=first_clip.event_type,
                label=first_clip.label,
                matched=any_matched,
                matched_by_local_label=matched_local_label,
            )
        )

    return results


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------
def generate_parity_report(
    eklipse_clips: List[EklipseClip],
    local_clips: List[LocalClip],
    vod_id: str = "",
    eklipse_file: str = "",
    local_file: str = "",
) -> ParityReport:
    """Generate a full parity report comparing Eklipse clips to local clips."""
    export_matches = _match_clips(eklipse_clips, local_clips)

    matched_count = sum(1 for m in export_matches if m.matched)
    missed_count = sum(1 for m in export_matches if not m.matched)

    # Extra = local clips that were not matched to any Eklipse clip
    matched_local_indices: set[int] = set()
    for m in export_matches:
        if m.matched and m.local_clip is not None:
            for li, lc in enumerate(local_clips):
                if lc is m.local_clip:
                    matched_local_indices.add(li)
                    break
    extra_locals = [lc for li, lc in enumerate(local_clips) if li not in matched_local_indices]

    # Timing errors
    start_errors = [m.eklipse_clip.start_sec - m.local_clip.start for m in export_matches if m.matched and m.local_clip]
    end_errors = [m.eklipse_clip.end_sec - m.local_clip.end for m in export_matches if m.matched and m.local_clip]

    # Recall / precision
    eklipse_count = len(eklipse_clips)
    local_count = len(local_clips)
    recall = matched_count / eklipse_count if eklipse_count > 0 else 0.0
    precision = matched_count / local_count if local_count > 0 else 0.0

    # Event-group comparison
    event_group_results = _compute_event_group_results(eklipse_clips, export_matches)
    unique_groups = len(event_group_results)
    matched_groups = sum(1 for r in event_group_results if r.matched)
    missed_groups = unique_groups - matched_groups
    unique_recall = matched_groups / unique_groups if unique_groups > 0 else 0.0

    # Rank comparison (only if rank values are present)
    rank_available = any(ec.rank is not None for ec in eklipse_clips)
    rank_agreement_count = 0
    rank_agreement_total = 0
    if rank_available:
        # Compare rank order for matched clips where both have ranks
        matched_with_ranks = [
            (m.eklipse_clip, m.local_clip)
            for m in export_matches
            if m.matched and m.local_clip is not None and m.eklipse_clip.rank is not None
        ]
        if len(matched_with_ranks) >= 2:
            # Sort by Eklipse rank
            sorted_by_eklipse = sorted(matched_with_ranks, key=lambda pair: pair[0].rank)
            # Sort by local score (descending, since higher score = better)
            sorted_by_local = sorted(matched_with_ranks, key=lambda pair: pair[1].score, reverse=True)

            # Agreement = both agree on which clip is ranked higher
            n_ranks = len(matched_with_ranks)
            rank_agreement_total = n_ranks * (n_ranks - 1) // 2
            for i in range(n_ranks):
                for j in range(i + 1, n_ranks):
                    eklipse_rank_i = sorted_by_eklipse[i][0].rank
                    eklipse_rank_j = sorted_by_eklipse[j][0].rank
                    score_i = sorted_by_eklipse[i][1].score
                    score_j = sorted_by_eklipse[j][1].score
                    eklipse_order = eklipse_rank_i < eklipse_rank_j
                    local_order = score_i > score_j
                    if eklipse_order == local_order:
                        rank_agreement_count += 1

    mean_start_err = sum(abs(e) for e in start_errors) / len(start_errors) if start_errors else 0.0
    mean_end_err = sum(abs(e) for e in end_errors) / len(end_errors) if end_errors else 0.0

    return ParityReport(
        vod_id=vod_id,
        eklipse_clip_count=eklipse_count,
        local_clip_count=local_count,
        matched_clips=matched_count,
        missed_clips=missed_count,
        extra_clips=len(extra_locals),
        export_recall=recall,
        export_precision=precision,
        start_errors=start_errors,
        end_errors=end_errors,
        mean_start_error=mean_start_err,
        mean_end_error=mean_end_err,
        export_matches=export_matches,
        missed_exports=[m.eklipse_clip for m in export_matches if not m.matched],
        extra_locals=extra_locals,
        unique_event_group_count=unique_groups,
        matched_event_groups=matched_groups,
        missed_event_groups=missed_groups,
        unique_event_recall=unique_recall,
        event_group_results=event_group_results,
        rank_available=rank_available,
        rank_agreement_count=rank_agreement_count,
        rank_agreement_total=rank_agreement_total,
        eklipse_file=eklipse_file,
        local_file=local_file,
    )


# ---------------------------------------------------------------------------
# Markdown report rendering
# ---------------------------------------------------------------------------
def _fmt_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def render_report_markdown(report: ParityReport) -> str:
    """Render a parity report as markdown text."""
    lines: List[str] = []
    lines.append(f"# Parity Report: {report.vod_id}")
    lines.append("")
    lines.append(f"Eklipse reference: `{report.eklipse_file}`")
    lines.append(f"Local plan: `{report.local_file}`")
    lines.append("")

    # --- Export-window comparison ---
    lines.append("## Export-Window Comparison")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Eklipse exported clip count | {report.eklipse_clip_count} |")
    lines.append(f"| AutoMakeClip selected clip count | {report.local_clip_count} |")
    lines.append(f"| Matched exported clips | {report.matched_clips} |")
    lines.append(f"| Missed Eklipse clips | {report.missed_clips} |")
    lines.append(f"| Extra AutoMakeClip clips | {report.extra_clips} |")
    lines.append(f"| Export-level recall | {report.export_recall:.1%} |")
    lines.append(f"| Export-level precision | {report.export_precision:.1%} |")

    if report.start_errors or report.end_errors:
        lines.append(f"| Mean start-time error | {report.mean_start_error:.1f}s |")
        lines.append(f"| Mean end-time error | {report.mean_end_error:.1f}s |")
    lines.append("")

    # --- Event-group comparison ---
    lines.append("## Unique-Event-Group Comparison")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Unique event-group count | {report.unique_event_group_count} |")
    lines.append(f"| Matched event groups | {report.matched_event_groups} |")
    lines.append(f"| Missed event groups | {report.missed_event_groups} |")
    lines.append(f"| Unique-event recall | {report.unique_event_recall:.1%} |")
    lines.append("")

    # --- Rank comparison (conditional) ---
    if report.rank_available:
        lines.append("## Rank Agreement")
        lines.append("")
        if report.rank_agreement_total > 0:
            agreement_pct = report.rank_agreement_count / report.rank_agreement_total
            lines.append(f"Pairwise rank agreement: {report.rank_agreement_count}/{report.rank_agreement_total} ({agreement_pct:.1%})")
        else:
            lines.append("Insufficient matched clips with rank values for rank comparison.")
        lines.append("")
    else:
        lines.append("## Rank Agreement")
        lines.append("")
        lines.append("Rank values not present in Eklipse reference — rank metrics omitted.")
        lines.append("")

    # --- Matched clip details ---
    lines.append("## Matched Clip Details")
    lines.append("")
    if report.export_matches:
        lines.append("| # | Eklipse Start | Eklipse End | Eklipse Label | Local Start | Local End | IoU | Anchor Dist |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for idx, m in enumerate(report.export_matches, start=1):
            if m.matched and m.local_clip:
                lines.append(
                    f"| {idx} | {_fmt_time(m.eklipse_clip.start_sec)} | {_fmt_time(m.eklipse_clip.end_sec)} "
                    f"| {m.eklipse_clip.label or m.eklipse_clip.event_type} "
                    f"| {_fmt_time(m.local_clip.start)} | {_fmt_time(m.local_clip.end)} "
                    f"| {m.iou:.2f} | {m.anchor_distance:.1f}s |"
                )
            else:
                lines.append(
                    f"| {idx} | {_fmt_time(m.eklipse_clip.start_sec)} | {_fmt_time(m.eklipse_clip.end_sec)} "
                    f"| {m.eklipse_clip.label or m.eklipse_clip.event_type} "
                    f"| — | — | — | — |"
                )
    lines.append("")

    # --- Missed exports ---
    if report.missed_exports:
        lines.append("## Missed Eklipse Exports")
        lines.append("")
        for clip in report.missed_exports:
            label = clip.label or clip.event_type or "unknown"
            lines.append(f"- **{label}** at {_fmt_time(clip.start_sec)}–{_fmt_time(clip.end_sec)}", )
            if clip.notes:
                lines.append(f"  - Note: {clip.notes}")
        lines.append("")

    # --- Extra local clips ---
    if report.extra_locals:
        lines.append("## Extra AutoMakeClip Clips (not in Eklipse)")
        lines.append("")
        for clip in report.extra_locals:
            lines.append(
                f"- **{clip.label}** at {_fmt_time(clip.start)}–{_fmt_time(clip.end)} (score={clip.score:.2f})"
            )
        lines.append("")

    # --- Event-group details ---
    lines.append("## Event-Group Details")
    lines.append("")
    lines.append("| Event Group | Event Type | Label | Matched | Matched By |")
    lines.append("|---|---|---|---|---|")
    for r in report.event_group_results:
        matched_str = "Yes" if r.matched else "No"
        lines.append(
            f"| {r.event_group} | {r.event_type} | {r.label} | {matched_str} | {r.matched_by_local_label or '—'} |"
        )
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare Eklipse exported clips against AutoMakeClip local plan output."
    )
    parser.add_argument(
        "--eklipse",
        required=True,
        help="Path to Eklipse reference JSON (exported clip windows).",
    )
    parser.add_argument(
        "--local",
        required=True,
        help="Path to AutoMakeClip .plan.json output.",
    )
    parser.add_argument(
        "--report",
        required=True,
        help="Path to write the markdown parity report.",
    )
    parser.add_argument(
        "--vod-id",
        default="",
        help="Logical VOD identifier for the report (auto-detected from filenames if omitted).",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    eklipse_path = Path(args.eklipse).expanduser().resolve()
    local_path = Path(args.local).expanduser().resolve()
    report_path = Path(args.report).expanduser().resolve()

    if not eklipse_path.exists():
        print(f"Eklipse reference not found: {eklipse_path}", file=sys.stderr)
        return 1
    if not local_path.exists():
        print(f"Local plan not found: {local_path}", file=sys.stderr)
        return 1

    eklipse_clips = load_eklipse_clips(eklipse_path)
    local_clips = load_local_plan(local_path)

    vod_id = args.vod_id
    if not vod_id:
        vod_id = local_path.stem.replace("_local", "").replace(".plan", "")

    report = generate_parity_report(
        eklipse_clips=eklipse_clips,
        local_clips=local_clips,
        vod_id=vod_id,
        eklipse_file=str(eklipse_path),
        local_file=str(local_path),
    )

    markdown = render_report_markdown(report)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(markdown, encoding="utf-8")
    print(f"Parity report written to: {report_path}", file=sys.stderr)

    # Also write JSON version alongside
    json_path = report_path.with_suffix(".json")
    json_data = {
        "vod_id": report.vod_id,
        "eklipse_clip_count": report.eklipse_clip_count,
        "local_clip_count": report.local_clip_count,
        "matched_clips": report.matched_clips,
        "missed_clips": report.missed_clips,
        "extra_clips": report.extra_clips,
        "export_recall": round(report.export_recall, 4),
        "export_precision": round(report.export_precision, 4),
        "mean_start_error_sec": round(report.mean_start_error, 3),
        "mean_end_error_sec": round(report.mean_end_error, 3),
        "unique_event_group_count": report.unique_event_group_count,
        "matched_event_groups": report.matched_event_groups,
        "missed_event_groups": report.missed_event_groups,
        "unique_event_recall": round(report.unique_event_recall, 4),
        "rank_available": report.rank_available,
        "eklipse_file": report.eklipse_file,
        "local_file": report.local_file,
    }
    json_path.write_text(json.dumps(json_data, indent=2), encoding="utf-8")
    print(f"JSON report written to: {json_path}", file=sys.stderr)

    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())