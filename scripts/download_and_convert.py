#!/usr/bin/env python3
from pathlib import Path
from automakeclip.config import MusicConfig
from automakeclip.music import select_music_track, auto_detect_drop_times
import subprocess, traceback, sys

out=Path('output_music_debug')
out.mkdir(parents=True, exist_ok=True)
config=MusicConfig(source='youtube', library_manifest='music_library/tracks.json', allow_generated_fallback=False)
try:
    track=select_music_track(mood='balanced', target_bpm=128.0, output_dir=out, config=config)
    print('DOWNLOADED:', track.local_path)
    input_path=Path(track.local_path)
    wav_path = out / (input_path.stem + '.wav')
    if input_path.suffix.lower() != '.wav':
        cmd = ['ffmpeg','-y','-i', str(input_path), str(wav_path)]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        print('FFMPEG RC:', proc.returncode)
        if proc.returncode!=0:
            print('FFMPEG ERR:', proc.stderr[:1000])
            sys.exit(1)
    else:
        wav_path = input_path
    print('WAV FILE:', wav_path.resolve())
    detected = auto_detect_drop_times(wav_path)
    print('DETECTED DROPS:', detected)
except Exception as e:
    print('ERROR', e)
    traceback.print_exc()
    sys.exit(1)
