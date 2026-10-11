"""Audio stream proxy service for instant web preview with in-memory TTL caching and Range support."""
import time
import logging
from typing import Generator, Dict, Any, Tuple, Optional
import requests
import yt_dlp

logger = logging.getLogger("streamer")

# In-memory stream cache: {source_url: {"direct_url": str, "expires_at": float}}
_STREAM_URL_CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_TTL = 7200  # 2 hours


def resolve_streamable_source(source_url: str) -> str:
    """Translates DRM/metadata-only URLs (Deezer, Spotify, Apple) or queries to search strings."""
    url_lower = source_url.lower().strip()
    if "deezer.com" in url_lower:
        try:
            from backend.parsers.deezer_parser import resolve_deezer_track
            meta = resolve_deezer_track(source_url)
            return f"ytsearch1:{meta.get('search_query', source_url)}"
        except Exception:
            return f"ytsearch1:{source_url}"
    elif "spotify.com" in url_lower:
        try:
            from backend.services.metadata import resolve_spotify_track
            meta = resolve_spotify_track(source_url)
            return f"ytsearch1:{meta.get('search_query', source_url)}"
        except Exception:
            return f"ytsearch1:{source_url}"
    elif not source_url.startswith("http"):
        return f"ytsearch1:{source_url}"
    return source_url


def get_direct_stream_url(source_url: str) -> str:
    """Resolves and caches progressive audio stream URL from YouTube/SoundCloud waterfall."""
    now = time.time()
    cached = _STREAM_URL_CACHE.get(source_url)
    if cached and cached.get("expires_at", 0) > now:
        return cached["direct_url"]

    target = resolve_streamable_source(source_url)

    ydl_opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "tv_embedded"]
            }
        },
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }

    direct_url = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target, download=False)
            if info and "entries" in info and info["entries"]:
                info = info["entries"][0]
            if info:
                direct_url = info.get("url")
    except Exception as yt_err:
        logger.warning(f"YouTube stream resolution failed: {yt_err}")

    # Fallback to SoundCloud if YouTube failed or got blocked
    if not direct_url:
        try:
            logger.info("Falling back to SoundCloud audio stream proxy...")
            sc_target = target.replace("ytsearch1:", "scsearch1:") if target.startswith("ytsearch1:") else f"scsearch1:{source_url}"
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as sc_ydl:
                sc_info = sc_ydl.extract_info(sc_target, download=False)
                if sc_info and "entries" in sc_info and sc_info["entries"]:
                    sc_info = sc_info["entries"][0]
                if sc_info:
                    direct_url = sc_info.get("url")
        except Exception as sc_err:
            logger.error(f"SoundCloud fallback stream failed: {sc_err}")

    if not direct_url:
        raise RuntimeError("Could not resolve direct audio stream URL across YouTube and SoundCloud.")

    _STREAM_URL_CACHE[source_url] = {
        "direct_url": direct_url,
        "expires_at": now + CACHE_TTL,
    }
    return direct_url


def stream_audio_chunks(source_url: str, range_header: Optional[str] = None) -> Tuple[int, Dict[str, str], Generator[bytes, None, None]]:
    """Proxies progressive audio stream with HTTP Range header support and partial content."""
    direct_url = get_direct_stream_url(source_url)

    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    if range_header:
        req_headers["Range"] = range_header

    resp = requests.get(direct_url, headers=req_headers, stream=True, timeout=12)
    resp.raise_for_status()

    status_code = resp.status_code
    response_headers = {
        "Content-Type": resp.headers.get("Content-Type", "audio/mp4"),
        "Accept-Ranges": "bytes",
    }
    if "Content-Range" in resp.headers:
        response_headers["Content-Range"] = resp.headers["Content-Range"]
    if "Content-Length" in resp.headers:
        response_headers["Content-Length"] = resp.headers["Content-Length"]

    def chunk_generator():
        try:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk
        finally:
            resp.close()

    return status_code, response_headers, chunk_generator()


def proxy_audio_stream(source_url: str) -> Generator[bytes, None, None]:
    """Backward compatibility wrapper for progressive stream generator."""
    _, _, generator = stream_audio_chunks(source_url)
    return generator
