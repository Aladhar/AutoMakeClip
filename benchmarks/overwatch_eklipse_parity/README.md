# Overwatch Eklipse Parity Benchmark

Measures how closely AutoMakeClip's highlight detection matches Eklipse's highlight picks for the same Overwatch VODs.

## Structure

```
benchmarks/overwatch_eklipse_parity/
├── README.md                 # This file
├── inputs.example.json       # Example input template (safe to commit)
├── local_inputs.json         # Your private URL list (git-ignored)
├── references/
│   ├── vod_001_eklipse.json  # Timestamps chosen by Eklipse for VOD 001
│   └── vod_002_eklipse.json  # Timestamps chosen by Eklipse for VOD 002
└── reports/                  # Generated benchmark reports land here
```

## How to Run

1. Copy `inputs.example.json` → `local_inputs.json`.
2. Fill in your VOD URLs and metadata in `local_inputs.json`.
3. Ensure `references/` contains the Eklipse-selected timestamps for each VOD.
4. Run the benchmark (TBD once the benchmark harness is implemented).

## Reference File Format

Each reference JSON should contain an array of highlight objects:

```json
[
  {
    "start_sec": 120.5,
    "end_sec": 135.2,
    "label": "teamfight_win",
    "notes": "Eklipse marked this as a top play"
  }
]
```

## Metrics

- **Overlap rate** – percentage of Eklipse highlights that AutoMakeClip also captures.
- **False positive rate** – percentage of AutoMakeClip highlights not in Eklipse's list.
- **Timing accuracy** – average offset between matched highlight boundaries.