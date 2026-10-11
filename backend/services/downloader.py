"""High-Fidelity Audio Download Engine using yt-dlp & FFmpeg.
Handles both ArkhSound single-track downloads and playlist batch track downloads.
"""
import os
import re
import uuid
import logging
from typing import Dict, Any, Optional, Callable
import yt_dlp

from backend.services.metadata import resolve_metadata
from backend.services.lyrics_service import fetch_lyrics
from backend.services.tagger import tag_mp3_file

logger = logging.getLogger("downloader")

TEMP_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "downloads")
os.makedirs(TEMP_ROOT, exist_ok=True)


def sanitize_filename(name: str) -> str:
    sanitized = re.sub(r'[\\/*?:"<>|]', "", name)
    sanitized = sanitized.strip().strip(".")
    return sanitized if sanitized else "audio_track"


def download_single_track(
    query_or_url: str,
    bitrate: str = "320k",
    existing_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """ArkhSound single track downloader with auto metadata, tags, and lyrics."""
    meta = existing_meta if existing_meta else resolve_metadata(query_or_url)

    job_id = str(uuid.uuid4())
    job_dir = os.path.join(TEMP_ROOT, f"single_{job_id}")
    os.makedirs(job_dir, exist_ok=True)

    clean_bitrate = bitrate.replace("k", "")
    target_url = meta.get("source_url") or meta.get("search_query")
    if not target_url.startswith("http"):
        target_url = f"ytsearch1:{target_url}"

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(job_dir, "raw_audio.%(ext)s"),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": clean_bitrate,
            }
        ],
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([target_url])

    expected_mp3 = os.path.join(job_dir, "raw_audio.mp3")
    if not os.path.exists(expected_mp3):
        for f in os.listdir(job_dir):
            if f.endswith(".mp3"):
                expected_mp3 = os.path.join(job_dir, f)
                break

    if not os.path.exists(expected_mp3):
        raise RuntimeError("Failed to generate MP3 audio file.")

    # Fetch lyrics
    dur_sec = meta.get("duration_sec", 0)
    lyrics = fetch_lyrics(
        meta.get("title", ""),
        meta.get("artist", ""),
        meta.get("album", ""),
        dur_sec if dur_sec else None
    )

    # Tag MP3
    tag_mp3_file(
        file_path=expected_mp3,
        title=meta.get("title", "Unknown Title"),
        artist=meta.get("artists_display") or meta.get("artist", "Unknown Artist"),
        album=meta.get("album", "Single"),
        track_number=1,
        total_tracks=1,
        year=meta.get("year"),
        cover_art_url=meta.get("cover_url"),
        plain_lyrics=lyrics.get("plain_lyrics"),
        synced_lyrics=lyrics.get("synced_lyrics"),
        save_lrc_file=True,
    )

    artist_clean = sanitize_filename(meta.get("artist", "Artist"))
    title_clean = sanitize_filename(meta.get("title", "Title"))
    final_name = f"{artist_clean} - {title_clean}.mp3"
    final_path = os.path.join(job_dir, final_name)

    if os.path.exists(final_path) and final_path != expected_mp3:
        os.remove(final_path)
    os.rename(expected_mp3, final_path)

    lrc_path = os.path.splitext(final_path)[0] + ".lrc"

    return {
        "file_path": final_path,
        "temp_dir": job_dir,
        "filename": final_name,
        "metadata": meta,
        "lyrics": lyrics,
        "has_lyrics": lyrics.get("has_lyrics", False),
        "lrc_path": lrc_path if os.path.exists(lrc_path) else None,
    }


def download_playlist_track(
    output_dir: str,
    title: str,
    artist: str,
    duration_ms: int = 0,
    bitrate: str = "320",
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Playlist batch track downloader with real-time progress callbacks."""
    os.makedirs(output_dir, exist_ok=True)
    clean_bitrate = bitrate.replace("k", "")
    clean_title = re.sub(r"\(feat\.[^\)]+\)|\(with[^\)]+\)|\[.*?\]", "", title, flags=re.IGNORECASE).strip()
    search_query = f"ytsearch5:{artist} - {clean_title} official audio"
    expected_duration_sec = duration_ms // 1000 if duration_ms else 0

    def ydl_progress_hook(d: Dict[str, Any]):
        if not progress_callback:
            return
        status = d.get("status")
        if status == "downloading":
            total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            percent = round((downloaded / total_bytes) * 100, 1) if total_bytes > 0 else 0.0
            speed = d.get("speed") or 0
            speed_str = f"{speed / 1024 / 1024:.1f} MB/s" if speed > 1024 * 1024 else f"{speed / 1024:.0f} KB/s"
            progress_callback({
                "stage": "downloading",
                "percent": percent,
                "speed": speed_str,
                "eta": d.get("eta") or 0,
            })
        elif status == "finished":
            progress_callback({
                "stage": "converting",
                "percent": 95.0,
                "speed": "",
                "eta": 0,
            })

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(output_dir, "%(id)s.%(ext)s"),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": clean_bitrate,
            }
        ],
        "progress_hooks": [ydl_progress_hook],
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }

    if progress_callback:
        progress_callback({"stage": "searching", "percent": 10.0})

    best_video_id = None
    best_diff = 999999

    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True}) as ydl:
        try:
            results = ydl.extract_info(search_query, download=False)
            entries = results.get("entries", []) if results else []
            if not entries:
                results = ydl.extract_info(f"ytsearch5:{artist} - {clean_title}", download=False)
                entries = results.get("entries", []) if results else []

            for entry in entries:
                if not entry:
                    continue
                v_dur = entry.get("duration") or 0
                v_title = entry.get("title", "").lower()
                if any(bad in v_title for bad in ["cover", "karaoke", "reaction", "1 hour"]):
                    continue
                if expected_duration_sec > 0 and v_dur > 0:
                    diff = abs(v_dur - expected_duration_sec)
                    if diff < best_diff:
                        best_diff = diff
                        best_video_id = entry.get("id")
                        if diff <= 3:
                            break
                else:
                    best_video_id = entry.get("id")
                    break

            if not best_video_id and entries:
                best_video_id = entries[0].get("id")
        except Exception as e:
            logger.error(f"Search error: {e}")

    if not best_video_id:
        raise RuntimeError(f"Could not find audio for '{artist} - {title}' on YouTube.")

    target_url = f"https://www.youtube.com/watch?v={best_video_id}"
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([target_url])

    expected_mp3 = os.path.join(output_dir, f"{best_video_id}.mp3")
    if not os.path.exists(expected_mp3):
        for f in os.listdir(output_dir):
            if f.startswith(best_video_id) and f.endswith(".mp3"):
                expected_mp3 = os.path.join(output_dir, f)
                break

    if not os.path.exists(expected_mp3):
        raise RuntimeError(f"MP3 file not generated for video ID {best_video_id}")

    safe_artist = sanitize_filename(artist)
    safe_title = sanitize_filename(title)
    final_name = f"{safe_artist} - {safe_title}.mp3"
    final_path = os.path.join(output_dir, final_name)

    counter = 1
    while os.path.exists(final_path) and final_path != expected_mp3:
        final_path = os.path.join(output_dir, f"{safe_artist} - {safe_title} ({counter}).mp3")
        counter += 1

    if os.path.exists(final_path) and final_path != expected_mp3:
        os.remove(final_path)
    os.rename(expected_mp3, final_path)

    return {
        "success": True,
        "file_path": final_path,
        "filename": os.path.basename(final_path),
        "video_id": best_video_id,
    }
