from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence
from urllib.parse import urlparse


class InputResolutionError(RuntimeError):
    """Raised when an input path or URL cannot be resolved."""


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm"}


@dataclass
class YoutubeEntry:
    title: str
    webpage_url: str


def resolve_inputs(raw_inputs: List[List[str]], download_root: Path, youtube_playlist_limit: int | None = None) -> List[Path]:
    resolved: List[Path] = []
    seen = set()
    for group in raw_inputs:
        for value in group:
            if _is_probable_url(value):
                for path in _resolve_url_input(value, download_root, youtube_playlist_limit):
                    if path not in seen:
                        resolved.append(path)
                        seen.add(path)
                continue

            path = Path(value).expanduser().resolve()
            if path.is_dir():
                for child in _iter_local_video_files(path):
                    if child not in seen:
                        resolved.append(child)
                        seen.add(child)
                continue
            if path not in seen:
                resolved.append(path)
                seen.add(path)
    return resolved


def _iter_local_video_files(path: Path) -> List[Path]:
    return sorted(
        (
            child.resolve()
            for child in path.rglob("*")
            if child.is_file() and child.suffix.lower() in VIDEO_EXTENSIONS
        ),
        key=lambda child: str(child).lower(),
    )


def _resolve_url_input(url: str, download_root: Path, youtube_playlist_limit: int | None) -> List[Path]:
    yt_dlp_command = _yt_dlp_command()

    urls = [url]
    if _looks_like_youtube_streams_page(url):
        entries = _list_youtube_entries(url, playlist_limit=youtube_playlist_limit, yt_dlp_command=yt_dlp_command)
        filtered = _filter_overwatch_entries(entries)
        urls = [entry.webpage_url for entry in (filtered or entries)]
        if not urls:
            raise InputResolutionError(f"No downloadable videos were found at {url}")

    download_root.mkdir(parents=True, exist_ok=True)
    downloaded: List[Path] = []
    for item_url in urls:
        downloaded.extend(_download_youtube_url(item_url, download_root, yt_dlp_command))
    return downloaded


def _download_youtube_url(url: str, download_root: Path, yt_dlp_command: Sequence[str]) -> List[Path]:
    command = [
        *yt_dlp_command,
        "--no-playlist",
        "--merge-output-format",
        "mp4",
        "-f",
        "bv*+ba/best",
        "-o",
        str(download_root / "%(title).120B [%(id)s].%(ext)s"),
        "--print",
        "after_move:filepath",
        url,
    ]
    process = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if process.returncode != 0:
        raise InputResolutionError(process.stderr.strip() or f"Unable to download {url}")

    downloaded = [Path(line.strip()).expanduser().resolve() for line in process.stdout.splitlines() if line.strip()]
    if not downloaded:
        raise InputResolutionError(f"`yt-dlp` did not report any downloaded files for {url}")
    existing_mp4s = [path for path in downloaded if path.suffix.lower() == ".mp4" and path.exists()]
    if not existing_mp4s:
        raise InputResolutionError(f"No MP4 file was produced for {url}")
    return existing_mp4s


def _list_youtube_entries(url: str, playlist_limit: int | None, yt_dlp_command: Sequence[str]) -> List[YoutubeEntry]:
    command = [
        *yt_dlp_command,
        "--flat-playlist",
        *_playlist_end_args(playlist_limit),
        "--print",
        "%(title)s\t%(webpage_url)s",
        url,
    ]
    process = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if process.returncode != 0:
        raise InputResolutionError(process.stderr.strip() or f"Unable to inspect {url}")

    entries: List[YoutubeEntry] = []
    for line in process.stdout.splitlines():
        if not line.strip():
            continue
        title, _, webpage_url = line.partition("\t")
        if not webpage_url:
            continue
        entries.append(YoutubeEntry(title=title.strip(), webpage_url=webpage_url.strip()))
    return entries


def _filter_overwatch_entries(entries: Sequence[YoutubeEntry]) -> List[YoutubeEntry]:
    filtered = [
        entry
        for entry in entries
        if "overwatch" in entry.title.lower() or "ow2" in entry.title.lower()
    ]
    return filtered


def _playlist_end_args(playlist_limit: int | None) -> List[str]:
    if playlist_limit is None or playlist_limit <= 0:
        return []
    return ["--playlist-end", str(playlist_limit)]


def looks_like_non_gameplay_source(path: Path) -> bool:
    name = path.name.lower()
    return name.startswith("zoom_") or " zoom " in name or "zoomcall" in name or "meeting" in name


def _looks_like_youtube_streams_page(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower().rstrip("/")
    if "youtube.com" not in host and "youtu.be" not in host:
        return False
    return path.endswith("/streams")


def _is_probable_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _yt_dlp_command() -> List[str]:
    binary = shutil.which("yt-dlp")
    if binary is not None:
        return [binary]
    user_binary = Path.home() / "Library" / "Python" / "3.14" / "bin" / "yt-dlp"
    if user_binary.exists():
        return [str(user_binary)]
    homebrew_python = Path("/opt/homebrew/bin/python3")
    if homebrew_python.exists() and _command_supports_yt_dlp([str(homebrew_python), "-m", "yt_dlp"]):
        return [str(homebrew_python), "-m", "yt_dlp"]
    try:
        __import__("yt_dlp")
    except ModuleNotFoundError as error:
        raise InputResolutionError(
            "URL inputs require `yt-dlp`. Install it or pass downloaded MP4 files instead."
        ) from error
    return [sys.executable, "-m", "yt_dlp"]


def _command_supports_yt_dlp(command: Sequence[str]) -> bool:
    process = subprocess.run(
        [*command, "--version"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return process.returncode == 0
