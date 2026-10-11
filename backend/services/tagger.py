"""ID3 metadata and lyrics tagging engine using Mutagen."""
import os
import re
import logging
from typing import Optional, Dict, Any
import requests
import mutagen
from mutagen.id3 import (
    ID3,
    TIT2,
    TPE1,
    TPE2,
    TALB,
    TRCK,
    TDRC,
    TYER,
    APIC,
    USLT,
    COMM,
    ID3NoHeaderError,
)

logger = logging.getLogger("tagger")


def tag_mp3_file(
    file_path: str,
    title: str,
    artist: str,
    album: str,
    track_number: int = 1,
    total_tracks: int = 1,
    year: Optional[str] = None,
    cover_art_url: Optional[str] = None,
    plain_lyrics: Optional[str] = None,
    synced_lyrics: Optional[str] = None,
    save_lrc_file: bool = True,
) -> bool:
    """Embeds full ID3v2.3 tags, cover art, and lyrics into MP3 file."""
    if not os.path.exists(file_path):
        return False

    try:
        try:
            audio = ID3(file_path)
        except ID3NoHeaderError:
            audio = ID3()

        # Text tags
        audio.add(TIT2(encoding=3, text=title))
        audio.add(TPE1(encoding=3, text=artist))
        audio.add(TPE2(encoding=3, text=artist))
        audio.add(TALB(encoding=3, text=album))
        audio.add(TRCK(encoding=3, text=f"{track_number}/{total_tracks}"))
        audio.add(COMM(encoding=3, lang="eng", desc="Comment", text="Downloaded via ArkhSound Harvester"))

        if year:
            audio.add(TDRC(encoding=3, text=str(year)))
            audio.add(TYER(encoding=3, text=str(year)))

        # Lyrics embedding (USLT)
        lyrics_text = plain_lyrics or ""
        if not lyrics_text and synced_lyrics:
            lyrics_text = re.sub(r"\[\d{2}:\d{2}(?:\.\d+)?\]", "", synced_lyrics).strip()

        if lyrics_text:
            audio.add(
                USLT(
                    encoding=3,
                    lang="eng",
                    desc="Lyrics",
                    text=lyrics_text,
                )
            )

        # Cover Art embedding (APIC)
        if cover_art_url:
            try:
                img_resp = requests.get(cover_art_url, timeout=8)
                if img_resp.status_code == 200:
                    mime = "image/jpeg"
                    if cover_art_url.lower().endswith(".png"):
                        mime = "image/png"
                    audio.add(
                        APIC(
                            encoding=3,
                            mime=mime,
                            type=3,  # Front cover
                            desc="Cover",
                            data=img_resp.content,
                        )
                    )
            except Exception as e:
                logger.warning(f"Could not download artwork for {title}: {e}")

        # Save ID3v2.3 (best compatibility across all OS, phones, car stereos)
        audio.save(file_path, v2_version=3)

        # Write companion .lrc file
        if save_lrc_file and synced_lyrics:
            lrc_path = os.path.splitext(file_path)[0] + ".lrc"
            try:
                with open(lrc_path, "w", encoding="utf-8") as f:
                    f.write(synced_lyrics)
            except Exception as e:
                logger.warning(f"Failed to write .lrc file: {e}")

        return True
    except Exception as e:
        logger.error(f"Error tagging MP3 file {file_path}: {e}")
        return False
