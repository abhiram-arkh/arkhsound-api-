"""Unified Single-Track Metadata Resolver.
Resolves queries from Spotify URLs, YouTube links, or search terms into structured metadata.
"""
import re
import json
import logging
from typing import Optional, Dict, Any
import requests
import yt_dlp

from backend.parsers.deezer_parser import resolve_deezer_track
from backend.parsers.soundcloud_parser import resolve_soundcloud_track, is_soundcloud_url
from backend.parsers.bandcamp_parser import fetch_bandcamp_metadata, is_bandcamp_url

logger = logging.getLogger("metadata")

SPOTIFY_TRACK_REGEX = re.compile(
    r"https?://open\.spotify\.com(?:/intl-[a-z]{2})?/track/([a-zA-Z0-9]+)"
)

def is_spotify_url(text: str) -> bool:
    return bool(SPOTIFY_TRACK_REGEX.search(text.strip()))

def clean_title(raw_title: str) -> str:
    cleaned = re.sub(r"[\u2010-\u2015\u2212\uFF0D]", " - ", raw_title)
    cleaned = re.sub(
        r"(?i)\s*[\(\[]\s*(official\s*(music\s*)?video(\s*remastered)?|official\s*audio|lyrics|hd|4k|remastered|remaster|visualizer|clip\s*officiel|official\s*video|audio|video)\s*[\)\]]",
        "",
        cleaned,
    )
    cleaned = re.sub(r"(?i)\s*[\(\[]\s*official\s*[\)\]]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned

def format_duration(seconds: Optional[int]) -> str:
    if not seconds:
        return "0:00"
    minutes = seconds // 60
    rem_seconds = seconds % 60
    return f"{minutes}:{rem_seconds:02d}"

def resolve_spotify_track(url: str) -> Dict[str, Any]:
    match = SPOTIFY_TRACK_REGEX.search(url.strip())
    track_id = match.group(1) if match else None

    title = "Unknown Title"
    artists = ["Unknown Artist"]
    album = "Unknown Album"
    year = ""
    cover_url = ""

    if track_id:
        try:
            oembed_url = f"https://open.spotify.com/oembed?url=https://open.spotify.com/track/{track_id}"
            resp = requests.get(oembed_url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if "title" in data:
                    title = data["title"]
                if "thumbnail_url" in data:
                    cover_url = data["thumbnail_url"]
        except Exception as e:
            logger.warning(f"Error fetching Spotify oEmbed: {e}")

        try:
            embed_url = f"https://open.spotify.com/embed/track/{track_id}"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            r = requests.get(embed_url, headers=headers, timeout=5)
            if r.status_code == 200:
                script_match = re.search(r'id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', r.text)
                if script_match:
                    page_data = json.loads(script_match.group(1))
                    entity = (
                        page_data.get("props", {})
                        .get("pageProps", {})
                        .get("state", {})
                        .get("data", {})
                        .get("entity", {})
                    )
                    if entity.get("name"):
                        title = entity.get("name")
                    artist_list = [a["name"] for a in entity.get("artists", []) if "name" in a]
                    if artist_list:
                        artists = artist_list
                    album_obj = entity.get("album", {})
                    if album_obj and album_obj.get("name"):
                        album = album_obj.get("name")
                    if album_obj and album_obj.get("release_date"):
                        year = album_obj.get("release_date").split("-")[0]
                    images = album_obj.get("images", [])
                    if images:
                        cover_url = images[0].get("url", cover_url)
        except Exception as e:
            logger.warning(f"Error fetching Spotify embed data: {e}")

    primary_artist = artists[0] if artists else "Unknown Artist"
    all_artists_str = ", ".join(artists)

    return {
        "title": title,
        "artist": primary_artist,
        "artists_display": all_artists_str,
        "album": album,
        "year": year,
        "cover_url": cover_url,
        "duration_str": "0:00",
        "duration_sec": 0,
        "search_query": f"{primary_artist} - {title} audio",
        "source": "spotify",
        "source_url": url,
    }

def resolve_youtube_query(query: str) -> Dict[str, Any]:
    is_url = bool(re.match(r"^https?://", query.strip()))
    target = query.strip() if is_url else f"ytsearch1:{query.strip()}"

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(target, download=False)
        if "entries" in info and info["entries"]:
            info = info["entries"][0]

    raw_title = info.get("title", "Unknown Title")
    cleaned = clean_title(raw_title)

    uploader = info.get("uploader") or info.get("channel") or "Unknown Artist"
    if uploader.endswith(" - Topic"):
        uploader = uploader.replace(" - Topic", "").strip()

    title = cleaned
    artist = uploader

    if " - " in cleaned:
        parts = cleaned.split(" - ", 1)
        artist = parts[0].strip()
        title = parts[1].strip()

    thumbnails = info.get("thumbnails", [])
    cover_url = thumbnails[-1].get("url", "") if thumbnails else info.get("thumbnail", "")
    duration_sec = info.get("duration", 0)
    upload_date = info.get("upload_date", "")
    year = upload_date[:4] if upload_date and len(upload_date) >= 4 else ""

    video_url = info.get("webpage_url") or (f"https://www.youtube.com/watch?v={info.get('id')}" if info.get("id") else "")

    return {
        "title": title,
        "artist": artist,
        "artists_display": artist,
        "album": f"{title} - Single",
        "year": year,
        "cover_url": cover_url,
        "duration_str": format_duration(duration_sec),
        "duration_sec": duration_sec,
        "search_query": video_url or query,
        "source": "youtube",
        "source_url": video_url,
    }

def resolve_metadata(query: str) -> Dict[str, Any]:
    query_str = query.strip()
    if is_spotify_url(query_str):
        spotify_meta = resolve_spotify_track(query_str)
        # Augment with duration from YouTube search
        try:
            yt_aug = resolve_youtube_query(spotify_meta["search_query"])
            spotify_meta["duration_str"] = yt_aug.get("duration_str", "0:00")
            spotify_meta["duration_sec"] = yt_aug.get("duration_sec", 0)
            spotify_meta["source_url"] = yt_aug.get("source_url", query_str)
        except Exception:
            pass
        return spotify_meta
    elif "deezer.com" in query_str:
        deezer_meta = resolve_deezer_track(query_str)
        try:
            yt_aug = resolve_youtube_query(deezer_meta["search_query"])
            deezer_meta["source_url"] = yt_aug.get("source_url", query_str)
        except Exception:
            pass
        return deezer_meta
    elif is_soundcloud_url(query_str):
        return resolve_soundcloud_track(query_str)
    elif is_bandcamp_url(query_str):
        bc_data = fetch_bandcamp_metadata(query_str)
        first_track = bc_data["tracks"][0]
        return {
            "title": first_track["title"],
            "artist": first_track["artist"],
            "artists_display": first_track["artist"],
            "album": bc_data["title"],
            "year": "",
            "cover_url": bc_data["cover_art"],
            "duration_str": first_track["duration_formatted"],
            "duration_sec": first_track["duration_ms"] // 1000,
            "search_query": first_track["source_url"],
            "source": "bandcamp",
            "source_url": first_track["source_url"],
        }
    else:
        return resolve_youtube_query(query_str)
