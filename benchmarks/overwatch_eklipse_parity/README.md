# Overwatch Eklipse Parity Benchmark

Measures how closely AutoMakeClip's highlight detection matches Eklipse's highlight picks for the same Overwatch VODs.

## Structure

```
benchmarks/overwatch_eklipse_parity/
├── README.md
├── inputs.example.json                          # Example input schema (committed)
├── local_inputs.json                            # Your private URL list (git-ignored)
├── downloads/
│   └── vod.mp4                                  # Local source video (git-ignored)
├── references/
│   ├── vod_001_eklipse.json                     # Eklipse exported clip windows
│   ├── vod_001_eklipse_session_summary.json      # Eklipse AI session summary
│   └── vod_002_eklipse.json                     # Placeholder for next VOD
└── reports/
    └── .gitkeep
```

## Two Parity Goals

### 1. Clip-Selection Parity (implemented now)

Compare Eklipse exported clip windows against AutoMakeClip `.plan.json` output.

```bash
automakeclip-parity \
  --eklipse references/vod_001_eklipse.json \
  --local output/vod_001_local.plan.json \
  --report reports/vod_001_report.md
```

Reports:
- Export-window: recall, precision, matched/missed/extra clips, timing errors
- Unique-event-group: did AutoMakeClip detect each underlying event at least once?
- Rank agreement: only reported when rank values are present (currently `null`)

### 2. Session-Summary/Coaching Parity (future)

AutoMakeClip should build a detected event timeline and generate post-session insights similar to Eklipse's AI session summary. Documented in the roadmap but not yet implemented.

## Input Schema

See `inputs.example.json` for the full schema. Key fields:

| Field | Values | Purpose |
|---|---|---|
| `vod_id` | string | Logical identifier |
| `platform` | `youtube`, `twitch`, `local` | Where the video comes from |
| `url` | string | Video URL or local path |
| `purpose` | `parity_benchmark`, `style_reference` | How this input is used |
| `clip_type` | `full_vod`, `clip` | Full VOD or already-shortened clip |

**Full VOD URLs** (`purpose: parity_benchmark`, `clip_type: full_vod`):
- Used for Eklipse parity comparison
- Both Eklipse and AutoMakeClip must find highlights from the same source

**Shortened clip URLs** (`purpose: style_reference`, `clip_type: clip`):
- Only for testing visual style, crop/layout, captions, audio mix
- Skipped by the parity comparison report

## Eklipse Reference Format

### Exported Clips (`vod_001_eklipse.json`)

```json
[
  {
    "start_sec": 312.4,
    "end_sec": 327.8,
    "event_type": "multi_kill",
    "event_group": "multi_kill_001",
    "label": "Multi kill",
    "rank": null,
    "notes": "Team fight ending in three eliminations"
  }
]
```

- `event_group`: clips sharing the same group are from the same underlying event
- `rank`: null = unknown display order; rank metrics omitted when null

### Session Summary (`vod_001_eklipse_session_summary.json`)

Contains Eklipse's AI-reported events and coaching insights (future parity target).

## Matching Rules

A local clip matches an Eklipse exported clip when:
- **IoU ≥ 0.50**, OR
- **Anchor timestamps within 2.0 seconds**

One local clip cannot satisfy multiple unrelated event groups. Multiple overlapping exports within the same `event_group` count as one detected event for event-group metrics.

## Metrics

### Export-Window Comparison

| Metric | Description |
|---|---|
| Export-level recall | Eklipse clips matched by AutoMakeClip |
| Export-level precision | AutoMakeClip clips that match Eklipse |
| Mean start-time error | Average absolute start offset |
| Mean end-time error | Average absolute end offset |

### Unique-Event-Group Comparison

| Metric | Description |
|---|---|
| Unique event-group count | Distinct underlying events |
| Unique-event recall | Events detected at least once |