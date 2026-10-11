"""Apple Music Playlist and Album metadata parser via serialized page payload."""
import re
import json
import logging
from typing import Dict, List, Any, Optional
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("apple_parser")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def parse_apple_url(url: str) -> Optional[Dict[str, str]]:
    pattern = r"music\.apple\.com\/([a-z]{2})\/(playlist|album)\/([^/]+)\/([a-zA-Z0-9\.\-]+)"
    match = re.search(pattern, url)
    if match:
        return {
            "country": match.group(1),
            "type": match.group(2),
            "slug": match.group(3),
            "id": match.group(4),
        }
    alt_pattern = r"music\.apple\.com\/([a-z]{2})\/(playlist|album)\/([a-zA-Z0-9\.\-]+)"
    match2 = re.search(alt_pattern, url)
    if match2:
        return {
            "country": match2.group(1),
            "type": match2.group(2),
            "slug": "",
            "id": match2.group(3),
        }
    return None

def fetch_apple_metadata(url: str) -> Dict[str, Any]:
    parsed = parse_apple_url(url)
    if not parsed:
        raise ValueError("Invalid Apple Music URL format.")

    entity_type = parsed["type"]
    entity_id = parsed["id"]
    country = parsed["country"]

    resp = requests.get(url, headers=HEADERS, timeout=15)
    if resp.status_code != 200:
        if entity_type == "album" and entity_id.isdigit():
            return fetch_itunes_album(entity_id, country)
        raise RuntimeError(f"Apple Music returned status {resp.status_code}")

    soup = BeautifulSoup(resp.text, "html.parser")
    script = soup.find("script", id="serialized-server-data")
    if not script or not script.string:
        if entity_type == "album" and entity_id.isdigit():
            return fetch_itunes_album(entity_id, country)
        raise RuntimeError("Could not find serialized data on Apple Music page.")

    data = json.loads(script.string)
    inner = {}
    if isinstance(data, dict):
        d_val = data.get("data")
        if isinstance(d_val, list) and d_val:
            inner = d_val[0].get("data", {})
        elif isinstance(d_val, dict):
            inner = d_val
    elif isinstance(data, list) and data:
        d_val = data[0].get("data")
        if isinstance(d_val, dict):
            inner = d_val
        elif isinstance(d_val, list) and d_val:
            inner = d_val[0].get("data", {})

    if not inner or not isinstance(inner, dict):
        if entity_type == "album" and entity_id.isdigit():
            return fetch_itunes_album(entity_id, country)
        raise RuntimeError("Unexpected Apple Music data structure.")

    sections = inner.get("sections", [])
    playlist_title = "Apple Music Collection"
    description = ""
    cover_art = ""
    tracks: List[Dict[str, Any]] = []

    for sec in sections:
        kind = sec.get("itemKind", "")
        items = sec.get("items", [])

        if kind == "containerDetailHeaderLockup" and items:
            header_item = items[0]
            playlist_title = header_item.get("title", playlist_title)
            description = header_item.get("subtitle", "") or header_item.get("editorialNotes", "")
            art_dict = header_item.get("artwork", {}).get("dictionary", {})
            if "url" in art_dict:
                cover_art = art_dict["url"].replace("{w}x{h}", "600x600").replace("{f}", "jpg")

        if kind == "trackLockup" and items:
            for idx, item in enumerate(items, start=1):
                track_title = item.get("title", "").strip()
                artist = item.get("artistName", "").strip()
                duration_ms = item.get("duration", 0)

                album = ""
                tertiary = item.get("tertiaryLinks", [])
                if tertiary and isinstance(tertiary, list):
                    album = tertiary[0].get("title", "")
                if not album and entity_type == "album":
                    album = playlist_title

                art_dict = item.get("artwork", {}).get("dictionary", {})
                track_art = cover_art
                if "url" in art_dict:
                    track_art = art_dict["url"].replace("{w}x{h}", "600x600").replace("{f}", "jpg")

                track_id = item.get("id", f"am_{idx}").split(" - ")[-1]

                tracks.append({
                    "id": track_id,
                    "title": track_title,
                    "artist": artist,
                    "album": album,
                    "duration_ms": duration_ms,
                    "duration_formatted": format_duration(duration_ms),
                    "cover_art": track_art or cover_art,
                    "track_number": idx,
                    "source": "apple_music",
                })

    if not tracks and entity_type == "album" and entity_id.isdigit():
        return fetch_itunes_album(entity_id, country)

    return {
        "id": entity_id,
        "type": entity_type,
        "title": playlist_title,
        "description": description,
        "cover_art": cover_art,
        "source": "apple_music",
        "total_tracks": len(tracks),
        "tracks": tracks,
    }

def fetch_itunes_album(album_id: str, country: str = "us") -> Dict[str, Any]:
    itunes_url = f"https://itunes.apple.com/lookup?id={album_id}&entity=song&country={country}"
    resp = requests.get(itunes_url, timeout=10)
    data = resp.json()
    results = data.get("results", [])

    if not results:
        raise RuntimeError("No tracks found in iTunes lookup.")

    album_info = results[0]
    album_title = album_info.get("collectionName", "Album")
    cover_art = album_info.get("artworkUrl100", "").replace("100x100bb.jpg", "600x600bb.jpg")
    artist = album_info.get("artistName", "")

    tracks = []
    track_num = 1
    for r in results[1:]:
        if r.get("wrapperType") == "track":
            dur_ms = r.get("trackTimeMillis", 0)
            art = r.get("artworkUrl100", "").replace("100x100bb.jpg", "600x600bb.jpg")
            tracks.append({
                "id": str(r.get("trackId", track_num)),
                "title": r.get("trackName", ""),
                "artist": r.get("artistName", artist),
                "album": r.get("collectionName", album_title),
                "duration_ms": dur_ms,
                "duration_formatted": format_duration(dur_ms),
                "cover_art": art or cover_art,
                "track_number": r.get("trackNumber", track_num),
                "source": "apple_music",
            })
            track_num += 1

    return {
        "id": album_id,
        "type": "album",
        "title": album_title,
        "description": f"By {artist}",
        "cover_art": cover_art,
        "source": "apple_music",
        "total_tracks": len(tracks),
        "tracks": tracks,
    }

def format_duration(duration_ms: int) -> str:
    total_seconds = int(duration_ms) // 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:02d}"
