"""Spotify Playlist and Album metadata parser via public embed pages."""
import re
import json
import base64
import logging
import urllib.request
import urllib.error
from typing import Dict, List, Any, Optional
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("spotify_parser")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",
    "Connection": "close",
}


def parse_spotify_url(url: str) -> Optional[Dict[str, str]]:
    """Extracts entity type and id from any Spotify URL or URI."""
    clean = url.strip().split("?")[0].split("#")[0]
    pattern = r"(?:spotify\.com\/(?:intl-[a-z]+\/)?(?:user\/[^\/]+\/)?|spotify:)(playlist|album|track)[/:]([a-zA-Z0-9]+)"
    match = re.search(pattern, clean)
    if match:
        return {"type": match.group(1), "id": match.group(2)}
    return None


def fetch_spotify_html(embed_url: str) -> str:
    """Fetches embed HTML safely with anti-chunk-reset headers and urllib fallback."""
    # Method 1: requests with Session and identity encoding
    try:
        session = requests.Session()
        resp = session.get(embed_url, headers=HEADERS, timeout=12)
        if resp.status_code == 200 and resp.text:
            return resp.text
        if resp.status_code == 404:
            raise ValueError("Spotify content not found (404). Please ensure the playlist is public and valid.")
    except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError, requests.exceptions.ProtocolError) as e:
        logger.warning(f"requests chunked/connection error on {embed_url}: {e}. Retrying with urllib...")
    except ValueError:
        raise
    except Exception as e:
        logger.warning(f"requests error on {embed_url}: {e}. Retrying with urllib...")

    # Method 2: urllib.request fallback (bypasses urllib3 connection reset quirks)
    try:
        req = urllib.request.Request(
            embed_url,
            headers={
                "User-Agent": HEADERS["User-Agent"],
                "Accept": HEADERS["Accept"],
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "close",
            }
        )
        with urllib.request.urlopen(req, timeout=12) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ValueError("Spotify content not found (404). Please ensure the link is public and valid.")
        raise RuntimeError(f"Spotify embed returned HTTP {e.code}")
    except Exception as e:
        raise RuntimeError(f"Failed to fetch Spotify embed page: {e}")


def fetch_spotify_oembed(url: str) -> Optional[Dict[str, Any]]:
    """Retrieves basic public metadata (title, cover, author) via Spotify oEmbed."""
    try:
        clean_url = url.split("?")[0]
        oembed_url = f"https://open.spotify.com/oembed?url={clean_url}"
        r = requests.get(oembed_url, headers={"User-Agent": HEADERS["User-Agent"], "Connection": "close"}, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.debug(f"oEmbed fetch error for {url}: {e}")
    return None


def find_entity_in_json(obj: Any) -> Optional[Dict[str, Any]]:
    """Recursively locates entity payload in various Spotify JSON formats."""
    if not isinstance(obj, dict):
        return None

    # Check state.data.entity path
    state_data = obj.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {})
    if isinstance(state_data, dict) and "entity" in state_data and isinstance(state_data["entity"], dict):
        return state_data["entity"]

    # Check pageProps.entity
    page_props = obj.get("props", {}).get("pageProps", {})
    if isinstance(page_props, dict):
        if "entity" in page_props and isinstance(page_props["entity"], dict):
            return page_props["entity"]
        if "data" in page_props and isinstance(page_props["data"], dict) and "entity" in page_props["data"]:
            return page_props["data"]["entity"]

    # If top-level is already an entity object
    if "trackList" in obj or (obj.get("type") in ("playlist", "album", "track") and ("title" in obj or "name" in obj)):
        return obj

    # Recursive search
    for k, v in obj.items():
        if isinstance(v, dict):
            if "trackList" in v or (v.get("type") in ("playlist", "album", "track") and ("title" in v or "name" in v)):
                return v
            res = find_entity_in_json(v)
            if res:
                return res
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    res = find_entity_in_json(item)
                    if res:
                        return res
    return None


def fetch_spotify_metadata(url: str) -> Dict[str, Any]:
    parsed = parse_spotify_url(url)
    if not parsed:
        raise ValueError("Invalid Spotify URL format. Expected playlist, album, or track URL.")

    entity_type = parsed["type"]
    entity_id = parsed["id"]
    embed_url = f"https://open.spotify.com/embed/{entity_type}/{entity_id}"

    # Fetch embed HTML
    html = fetch_spotify_html(embed_url)
    soup = BeautifulSoup(html, "html.parser")

    # Strategy 1: Look for __NEXT_DATA__
    entity = None
    next_script = soup.find("script", id="__NEXT_DATA__")
    if next_script and next_script.string:
        try:
            data = json.loads(next_script.string)
            page_props = data.get("props", {}).get("pageProps", {})
            if isinstance(page_props, dict):
                if page_props.get("status") == 404 or page_props.get("title") == "Page not found":
                    raise ValueError("Spotify playlist or album not found or is private. Please ensure the link is public and accessible.")
            entity = find_entity_in_json(data)
        except ValueError:
            raise
        except Exception as e:
            logger.debug(f"Failed parsing __NEXT_DATA__: {e}")

    # Strategy 2: Look for initial-state (base64)
    if not entity:
        init_script = soup.find("script", id="initial-state")
        if init_script and init_script.string:
            try:
                raw_json = base64.b64decode(init_script.string.strip()).decode("utf-8")
                data = json.loads(raw_json)
                entity = find_entity_in_json(data)
            except Exception as e:
                logger.debug(f"Failed parsing initial-state: {e}")

    # Strategy 3: Look for resource (base64)
    if not entity:
        res_script = soup.find("script", id="resource")
        if res_script and res_script.string:
            try:
                raw_json = base64.b64decode(res_script.string.strip()).decode("utf-8")
                data = json.loads(raw_json)
                entity = find_entity_in_json(data)
            except Exception as e:
                logger.debug(f"Failed parsing resource: {e}")

    # Optional oEmbed fallback for metadata enhancement
    oembed_data = fetch_spotify_oembed(url)

    if not entity:
        if oembed_data and oembed_data.get("title"):
            raise RuntimeError(
                f"Found '{oembed_data.get('title')}' on Spotify, but track details could not be loaded. "
                "The playlist might have restricted embed permissions or be empty."
            )
        raise RuntimeError(
            "Could not retrieve playlist tracks from Spotify. Please ensure the playlist is public and accessible."
        )

    title = entity.get("title") or entity.get("name") or (oembed_data.get("title") if oembed_data else "Spotify Collection")
    subtitle = entity.get("subtitle") or entity.get("description") or ""
    if not subtitle and isinstance(entity.get("artists"), list) and entity["artists"]:
        subtitle = ", ".join([a.get("name", "") for a in entity["artists"] if "name" in a])

    cover_art = ""
    visual_identity = entity.get("visualIdentity", {})
    if visual_identity.get("image"):
        images = visual_identity["image"]
        if isinstance(images, list) and images:
            cover_art = images[0].get("url", "")
        elif isinstance(images, dict):
            cover_art = images.get("url", "")

    if not cover_art and entity.get("images"):
        imgs = entity["images"]
        if isinstance(imgs, list) and imgs:
            cover_art = imgs[0].get("url", "")

    if not cover_art and oembed_data and oembed_data.get("thumbnail_url"):
        cover_art = oembed_data.get("thumbnail_url", "")

    tracks: List[Dict[str, Any]] = []

    if entity_type == "track":
        duration_ms = entity.get("duration") or entity.get("duration_ms") or 0
        tracks.append({
            "id": entity_id,
            "title": title,
            "artist": subtitle,
            "album": entity.get("album", {}).get("name", title) if isinstance(entity.get("album"), dict) else title,
            "duration_ms": duration_ms,
            "duration_formatted": format_duration(duration_ms),
            "cover_art": cover_art,
            "track_number": 1,
            "source": "spotify",
        })
    else:
        raw_tracks = entity.get("trackList")
        if not raw_tracks and isinstance(entity.get("tracks"), dict):
            raw_tracks = entity["tracks"].get("items")
        elif not raw_tracks and isinstance(entity.get("tracks"), list):
            raw_tracks = entity["tracks"]
        elif not raw_tracks and isinstance(entity.get("items"), list):
            raw_tracks = entity["items"]

        if not raw_tracks:
            raw_tracks = []

        for idx, t in enumerate(raw_tracks, start=1):
            if not isinstance(t, dict):
                continue

            # Nested track object handling (e.g. {track: {...}})
            if "track" in t and isinstance(t["track"], dict):
                t_inner = t["track"]
            else:
                t_inner = t

            t_title = t_inner.get("title") or t_inner.get("name") or "Track"
            t_title = str(t_title).strip()

            t_subtitle = t_inner.get("subtitle") or ""
            if not t_subtitle and isinstance(t_inner.get("artists"), list):
                t_subtitle = ", ".join([a.get("name", "") for a in t_inner["artists"] if isinstance(a, dict) and "name" in a])
            if not t_subtitle:
                t_subtitle = subtitle

            duration_ms = t_inner.get("duration") or t_inner.get("duration_ms") or 0
            track_id = t_inner.get("uri", "").split(":")[-1] if t_inner.get("uri") else (t_inner.get("id") or f"sp_{idx}")

            t_album = title if entity_type == "album" else ""
            if not t_album and isinstance(t_inner.get("album"), dict):
                t_album = t_inner["album"].get("name", "")
            if not t_album:
                t_album = str(t_inner.get("album") or title)

            t_cover = cover_art
            if isinstance(t_inner.get("album"), dict) and t_inner["album"].get("images"):
                alb_imgs = t_inner["album"]["images"]
                if isinstance(alb_imgs, list) and alb_imgs:
                    t_cover = alb_imgs[0].get("url", cover_art)

            tracks.append({
                "id": track_id,
                "title": t_title,
                "artist": t_subtitle,
                "album": t_album,
                "duration_ms": duration_ms,
                "duration_formatted": format_duration(duration_ms),
                "cover_art": t_cover,
                "track_number": idx,
                "source": "spotify",
            })

    if not tracks:
        raise RuntimeError(
            f"No tracks found in '{title}'. The playlist might be empty or restricted."
        )

    return {
        "id": entity_id,
        "type": entity_type,
        "title": title,
        "description": subtitle,
        "cover_art": cover_art,
        "source": "spotify",
        "total_tracks": len(tracks),
        "tracks": tracks,
    }


def format_duration(duration_ms: int) -> str:
    total_seconds = int(duration_ms) // 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:02d}"
