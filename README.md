# AutoMakeClip

AutoMakeClip turns a long Overwatch gameplay recording into a tighter highlight reel:
- uses an Eklipse-style highlight filter implemented in this repo
- finds kill-heavy and fight-heavy moments from one or more source MP4s
- boosts kill-feed and action-heavy sections
- prefers embedded SteelSeries / GameSense kill events when they exist
- prefers real licensed music you supply locally, with generated fallback only when you explicitly allow it
- aligns clip lengths toward the chosen track's beat grid (making them synchronous with music)
- exports a higher-fidelity final montage MP4 plus a credits file for the music

The goal is the vibe you described: compact gameplay cuts, a quick intro, some silly chaos, and the strongest highlight moments.

## How It Works

The current version is Overwatch-aware and uses a hybrid selection flow:

- it does not call Eklipse.gg or use Eklipse's proprietary backend
- instead, it implements an Eklipse-style scoring pipeline locally in Python and `ffmpeg`

- embedded SteelSeries / GameSense kill timestamps when present
- visual motion from low-FPS analysis frames
- top-right HUD change detection to approximate kill-feed activity
- bottom-center HUD change detection to catch active ability/fight states
- center-screen gameplay confidence gating to suppress loading / dead / spectate junk
- audio RMS and spectral flux to rank hype around real fight windows

It does not need manual timestamps.

### Eklipse-Style Filter

What `Eklipse-style` means here:

- the system ranks short gameplay windows instead of requiring manual timestamps
- kill-confirmed windows are tiered above generic motion-heavy windows
- fallback fight detection still works when event metadata is missing
- overlap suppression and duplicate-capture suppression try to avoid near-identical clips
- final clip selection pools candidates globally so one strong VOD can contribute multiple highlights
- review memory can later boost, downrank, or retrim similar clips based on prior labels and notes

Where that behavior currently lives:

- [analysis.py](src/automakeclip/analysis.py) extracts and scores candidate highlight windows
- [selection.py](src/automakeclip/selection.py) tiers and selects the final clip pool
- [memory.py](src/automakeclip/memory.py) applies persistent review-memory boosts, downranks, and trim adjustments
- [cli.py](src/automakeclip/cli.py) runs the end-to-end automake flow over local files or YouTube inputs

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
automakeclip --from-cache --output reel.mp4
```

You can also pass YouTube URLs when `yt-dlp` is installed. If you pass a YouTube channel `/streams` page, the resolver looks for titles that mention `Overwatch` or `OW2`, downloads those VODs locally, and then analyzes the resulting MP4s. Stream-page resolution is unlimited by default; pass `--youtube-playlist-limit N` only when you explicitly want to cap how many recent VODs get pulled in.

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

If the videos have already been analyzed and are still present on disk, you can build the review session directly from the cache:

```bash
automakeclip-review --from-cache --samples 18
```

This creates a review session in `review_sessions/` and starts a local UI at `http://127.0.0.1:8765`.

The review app:

- renders short sample clips from the current detector
- lets you mark each one `yes`, `no`, or `skip`
- saves your decisions to `labels.json` in the session folder
- saves review memory to `review_sessions/review_memory.json` so future runs can boost similar accepted clips, downrank rejected patterns, and reuse learned trim offsets.
- reads saved Clip Notes and Session Notes as editing guidance, so notes like `too long`, `start later`, `more multi-kills`, `no generic filler`, or `more aftermath` influence future clip scoring and trim choices
- shows a bottom-right readout for the current clip so you can see the score, bucket, memory effects, and note directives the tool understood
- can finish the review and automatically render a final montage from the full detected candidate pool, using accepted/rejected review clips as guidance
- selects soundtrack music automatically through the normal music picker when it builds that montage
- works with local MP4s, folders, or YouTube URLs when `yt-dlp` is available

The current learning loop is not a neural ML model. It is deterministic detector scoring plus persistent review memory from your labels, trims, Clip Notes, and Session Notes.

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

When you provide multiple inputs, the tool analyzes each recording separately, pools the detected candidate moments, and builds one final montage across all of them.
Selection does not impose a one-clip-per-video limit, and the candidate pool is not trimmed to a fixed per-video clip count before global selection. If one source recording contains many distinct strong moments, the montage may use as many of them as fit the final runtime.

The tool also keeps a per-video analysis cache in `.automakeclip_cache/` so repeated runs over the same folder do not need to re-scan every MP4 unless the file or analysis settings changed.

## Music Source

The music picker now defaults to YouTube playlist audio:

- YouTube entries in `music_library/tracks.json` with `source_kind: "youtube"` or `source_kind: "youtube_playlist"`
- local library manifest entries only if you explicitly switch `--music-source library`
- optional public ccMixter catalog only if you explicitly switch `--music-source auto` or `--music-source ccmixter`
- generated fallback only if you explicitly pass `--allow-generated-fallback`

YouTube audio is fetched with `yt-dlp` into the output `music_cache/` folder when the montage is created. For playlist URLs, the picker downloads the first available playlist item by default. A starter schema lives at `music_library/tracks.example.json`.

If you want actual YouTube playlist music:

- add the YouTube video or playlist URL
- set `source_kind` to `youtube` or `youtube_playlist`
- list them in `music_library/tracks.json`

Use only audio you have rights to publish.

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
  --music-source youtube \
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

## Requested Future Work

These are product notes and requested directions only. They are not implemented yet unless another section above already explicitly says they are.

- After each implementation push, update the README so the current workflow and the next requested steps stay visible.
- Consider integrating Eklipse.gg itself later if the local in-repo filter still trails the strength of Eklipse's own highlight selection.
- Before a new automake render writes fresh output, clear the previous automake-generated output artifacts for that target render set instead of leaving stale reel files around. Scope this to prior automake outputs such as the rendered video, plan JSON, credits file, and related temp output for that render target rather than deleting arbitrary files in the folder.
- Keep strengthening the automake path so automatic clip ranking, trimming, and sequencing factor in both deterministic detector scoring and persistent review memory from labels, trims, Clip Notes, and Session Notes.
- Evolve Review UI toward a lightweight video-editing workflow, including draggable trim controls or a drag bar so clip in/out points can be adjusted directly during review.
- Add a random music mode that can draw from the provided YouTube Music playlist: `https://music.youtube.com/playlist?list=PLaysoNAQ0qMiqX9N9-7TJEydj_MoKTvi0`.
- Support per-song customization notes so each track can record where the beat drops happen, which sections are treble-heavy, which sections have strong bass drops, and any other sync-relevant structure that should affect clip placement.
- Use song-aware clip sizing and repositioning so clips can be moved to different parts of a selected track and aligned more intentionally with drops, impact beats, and texture changes in the music.

## Notes

- Default export quality is now geared toward 1440p source footage.
- This repo is still intentionally easy to tweak in code if you want to push the style further.
- `ffmpeg` is required at runtime even after Python dependencies are installed.
- If your footage uses a different HUD layout or resolution, adjust the Overwatch ROI values in `src/automakeclip/config.py`.



User generated handoff:
Implemented:
- Clip Notes and Session Notes are saved into review memory and interpreted as reusable guidance for future scoring and trim adjustments.
- The selector allows multiple distinct clips from the same source video when they fit the target runtime.
