"""Audio stream proxy service for instant web preview."""
import logging
from typing import Generator
import requests
import yt_dlp

logger = logging.getLogger("streamer")

def proxy_audio_stream(source_url: str) -> Generator[bytes, None, None]:
    """Resolves direct progressive audio stream URL from YouTube and proxies bytes."""
    ydl_opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
    }

    direct_url = None
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(source_url, download=False)
        direct_url = info.get("url")

    if not direct_url:
        raise RuntimeError("Could not resolve direct audio stream URL.")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    with requests.get(direct_url, headers=headers, stream=True, timeout=10) as r:
        r.raise_for_status()
        for chunk in r.iter_content(chunk_size=65536):
            if chunk:
                yield chunk
