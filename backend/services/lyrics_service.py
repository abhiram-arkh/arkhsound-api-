"""Lyrics fetcher and parser using LRCLIB API and syncedlyrics fallback.
Extracts synchronized (.lrc timestamps) and unsynchronized plain lyrics.
"""
import re
import logging
from typing import Dict, Any, Optional, List
import requests

logger = logging.getLogger("lyrics")

LRCLIB_URL = "https://lrclib.net/api"
USER_AGENT = "ArkhSoundHarvester/2.0 (https://github.com/arkhsound)"


def fetch_lyrics(
    track_title: str,
    artist_name: str,
    album_name: str = "",
    duration_seconds: Optional[int] = None
) -> Dict[str, Any]:
    """
    Fetches synchronized and plain lyrics.
    Returns dictionary with synced lyrics, plain lyrics, and parsed timestamp lines.
    """
    # Clean track title for query
    clean_title = re.sub(r"\(feat\.[^\)]+\)|\(with[^\)]+\)|\[.*?\]|\(.*?Version\)", "", track_title, flags=re.IGNORECASE).strip()
    clean_artist = artist_name.split(",")[0].split("&")[0].split("feat.")[0].strip()

    params: Dict[str, Any] = {
        "track_name": clean_title,
        "artist_name": clean_artist,
    }
    if album_name:
        params["album_name"] = album_name
    if duration_seconds and duration_seconds > 0:
        params["duration"] = duration_seconds

    headers = {"User-Agent": USER_AGENT}

    # 1. Exact LRCLIB query
    try:
        resp = requests.get(f"{LRCLIB_URL}/get", params=params, headers=headers, timeout=6)
        if resp.status_code == 200:
            data = resp.json()
            synced = data.get("syncedLyrics")
            plain = data.get("plainLyrics")
            if synced or plain:
                return _format_lyrics_response(synced, plain)
    except Exception as e:
        logger.debug(f"LRCLIB exact get error: {e}")

    # 2. Search LRCLIB
    try:
        search_query = f"{clean_title} {clean_artist}"
        resp = requests.get(f"{LRCLIB_URL}/search", params={"q": search_query}, headers=headers, timeout=6)
        if resp.status_code == 200:
            results = resp.json()
            if results and isinstance(results, list):
                for item in results:
                    synced = item.get("syncedLyrics")
                    plain = item.get("plainLyrics")
                    if synced or plain:
                        return _format_lyrics_response(synced, plain)
    except Exception as e:
        logger.debug(f"LRCLIB search error: {e}")

    # 3. Fallback: syncedlyrics library
    try:
        import syncedlyrics
        lrc = syncedlyrics.search(f"{clean_title} {clean_artist}")
        if lrc:
            return _format_lyrics_response(lrc, None)
    except Exception as e:
        logger.debug(f"syncedlyrics fallback error: {e}")

    return {
        "has_lyrics": False,
        "is_synced": False,
        "synced_lyrics": "",
        "plain_lyrics": "No lyrics available for this song.",
        "parsed_lines": [],
    }


def _format_lyrics_response(synced: Optional[str], plain: Optional[str]) -> Dict[str, Any]:
    synced_str = synced.strip() if synced else ""
    plain_str = plain.strip() if plain else ""
    parsed_lines: List[Dict[str, Any]] = []

    if synced_str:
        pattern = r"\[(\d{2}):(\d{2}(?:\.\d+)?)\](.*)"
        lines = synced_str.splitlines()
        for line in lines:
            match = re.match(pattern, line.strip())
            if match:
                minutes = int(match.group(1))
                seconds = float(match.group(2))
                time_sec = round(minutes * 60 + seconds, 2)
                text = match.group(3).strip()
                parsed_lines.append({"time": time_sec, "text": text})

        if not plain_str and parsed_lines:
            plain_str = "\n".join(item["text"] for item in parsed_lines if item["text"])

    return {
        "has_lyrics": bool(synced_str or plain_str),
        "is_synced": bool(synced_str),
        "synced_lyrics": synced_str,
        "plain_lyrics": plain_str,
        "parsed_lines": parsed_lines,
    }
