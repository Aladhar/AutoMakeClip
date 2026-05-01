# AutoMakeClip

AutoMakeClip turns a long Overwatch gameplay recording into a tighter highlight reel:

- finds kill-heavy and fight-heavy moments from one or more source MP4s
- boosts kill-feed and action-heavy sections
- prefers embedded SteelSeries / GameSense kill events when they exist
- inserts a short stylized intro
- prefers real licensed music you supply locally, with generated fallback only when you explicitly allow it
- aligns clip lengths toward the chosen track's beat grid
- exports a higher-fidelity final montage MP4 plus a credits file for the music

The goal is the vibe you described: compact gameplay cuts, a quick intro, some silly chaos, and the strongest highlight moments.

## How It Works

The current version is Overwatch-aware and uses a hybrid selection flow:

- embedded SteelSeries / GameSense kill timestamps when present
- visual motion from low-FPS analysis frames
- top-right HUD change detection to approximate kill-feed activity
- bottom-center HUD change detection to catch active ability/fight states
- center-screen gameplay confidence gating to suppress loading / dead / spectate junk
- audio RMS and spectral flux to rank hype around real fight windows

It does not need manual timestamps.

## Requirements

- Python 3.9+
- `ffmpeg`
- `ffprobe`

On macOS:

```bash
brew install ffmpeg
python3 -m pip install -e .
```

## Usage

```bash
automakeclip \
  --input /path/to/overwatch_take_01.mp4 /path/to/overwatch_take_02.mp4 \
  --output /path/to/overwatch_highlight.mp4 \
  --title "Overwatch Highlight Reel" \
  --subtitle "big plays, chaos, and one stupidly funny fight"
```

Useful options:

```bash
automakeclip --help
automakeclip --input take.mp4 --output reel.mp4 --target-seconds 38
automakeclip --input take1.mp4 take2.mp4 take3.mp4 --output reel.mp4
automakeclip --input "/path/to/Videos" --output reel.mp4
automakeclip --input take.mp4 --output reel.mp4 --no-silly
automakeclip --input take.mp4 --output reel.mp4 --no-music
automakeclip --input "/path/to/Videos" --output reel.mp4 --no-cache
automakeclip --input take.mp4 --output reel.mp4 --keep-temp
automakeclip --input take.mp4 --output reel.mp4 --dry-run
```

You can also pass YouTube URLs when `yt-dlp` is installed. If you pass a YouTube channel `/streams` page, the resolver looks for recent titles that mention `Overwatch` or `OW2`, downloads those VODs locally, and then analyzes the resulting MP4s.

## Review UI

To calibrate what counts as a good clip, generate sample clips and review them in a tiny yes/no UI:

```bash
automakeclip-review \
  --input "/path/to/Videos" \
  --samples 18
```

If the new console script is not installed yet in your environment, run the repo-local launcher instead:

```bash
python3 review_ui.py --input "/path/to/Videos" --samples 18
```

This creates a review session in `review_sessions/` and starts a local UI at `http://127.0.0.1:8765`.

The review app:

- renders short sample clips from the current detector
- lets you mark each one `yes`, `no`, or `skip`
- saves your decisions to `labels.json` in the session folder
- works with local MP4s, folders, or YouTube URLs when `yt-dlp` is available

## Output

For a command like:

```bash
automakeclip --input take1.mp4 take2.mp4 --output output/reel.mp4
```

you will get:

- `output/reel.mp4`
- `output/reel.plan.json`
- `output/reel.credits.txt`

The plan JSON is useful for tuning the cut logic if you want to iterate on the montage style.

When you provide multiple inputs, the tool scores each recording separately, pools the best moments, and builds one final montage across all of them.

The tool also keeps a per-video analysis cache in `.automakeclip_cache/` so repeated runs over the same folder do not need to re-scan every MP4 unless the file or analysis settings changed.

## Music Source

The music picker now works in this order:

- local library manifest in `music_library/tracks.json` for real songs you already downloaded or licensed
- optional public ccMixter catalog only if you explicitly switch `--music-source auto` or `--music-source ccmixter`
- generated fallback only if you explicitly pass `--allow-generated-fallback`

The local-library route is the safest way to use actual songs in a repeatable workflow. A starter schema lives at `music_library/tracks.example.json`.

If you want actual trending songs instead of generated music:

- license them through YouTube Creator Music if your channel is eligible
- or use tracks from YouTube Audio Library
- download them yourself
- list them in `music_library/tracks.json`

That keeps the workflow legal and repeatable without pretending random Spotify or YouTube uploads are safe to use.

The picker aims for:

- aggressive electronic / hip-hop for high-intensity highlight-heavy reels
- bouncier groove-oriented tracks when the reel has more chaos/comedy energy
- mid-tempo energetic tracks for balanced highlight reels

The exact track is chosen automatically from the reel's detected pace.

For YouTube-safe publishing, prefer tracks you downloaded from YouTube Audio Library or music you separately licensed through YouTube Creator Music and list them in the manifest with their local file paths.

Example:

```bash
automakeclip \
  --input "/path/to/Videos" \
  --output latest-output/final_clip.mp4 \
  --music-source library \
  --music-manifest music_library/tracks.json
```

If you deliberately want the old synthetic fallback:

```bash
automakeclip \
  --input "/path/to/Videos" \
  --output latest-output/final_clip.mp4 \
  --allow-generated-fallback
```

If you care about drop-sync specifically, add `drop_times` to your `music_library/tracks.json` entries. Those should be the song timestamps where the main drop or major impact moments happen, and the renderer will shift the song so those moments line up with the strongest gameplay beats.

## Notes

- Default export quality is now geared toward 1440p source footage.
- This repo is still intentionally easy to tweak in code if you want to push the style further.
- `ffmpeg` is required at runtime even after Python dependencies are installed.
- If your footage uses a different HUD layout or resolution, adjust the Overwatch ROI values in `src/automakeclip/config.py`.
