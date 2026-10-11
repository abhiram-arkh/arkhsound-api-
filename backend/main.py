"""ArkhSound Harvester - Unified Backend Microservice & Web App.
Combines ArkhSound single-track search/streaming with full playlist & lyrics downloading.
"""
import os
import sys
import shutil
import logging
from typing import Optional, Dict, Any, List
from urllib.parse import quote
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel

# Ensure project root is in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from backend.services.metadata import resolve_metadata
from backend.services.downloader import download_single_track
from backend.services.streamer import proxy_audio_stream
from backend.services.cleanup import cleanup_temp_dir
from backend.services.lyrics_service import fetch_lyrics
from backend.parsers.spotify_parser import fetch_spotify_metadata
from backend.parsers.apple_parser import fetch_apple_metadata
from backend.services.task_manager import TaskManager

DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")
STATIC_DIR = os.path.join(BASE_DIR, "static")

os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("arkhsound_api")

app = FastAPI(
    title="ArkhSound Harvester API",
    version="2.0.0",
    description="Full-Featured MP3 Downloader, Playlist Harvester & Karaoke Lyrics Microservice",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "Content-Length"],
)

task_manager = TaskManager(DOWNLOADS_DIR)

# Request Models
class SingleMetadataRequest(BaseModel):
    query: str

class SingleDownloadRequest(BaseModel):
    query: str
    bitrate: Optional[str] = "320k"
    metadata: Optional[Dict[str, Any]] = None

class PlaylistParseRequest(BaseModel):
    url: str

class PlaylistBatchRequest(BaseModel):
    session_id: str
    track_ids: Optional[List[str]] = None
    bitrate: Optional[str] = "320"


# ---------------------------------------------------------
# ArkhSound Core Microservice Endpoints
# ---------------------------------------------------------

@app.get("/api/health")
def health_check():
    ffmpeg_path = shutil.which("ffmpeg")
    return {
        "status": "online",
        "app": "ArkhSound Harvester B&W",
        "version": "2.0.0",
        "ffmpeg_available": bool(ffmpeg_path),
        "ffmpeg_path": ffmpeg_path,
        "features": ["single_track_download", "playlist_harvesting", "synced_lyrics", "karaoke", "zip_packaging"],
    }


@app.post("/api/metadata")
def get_metadata(req: SingleMetadataRequest):
    """Resolves single track metadata from search query, YouTube, or Spotify URL."""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty.")
    try:
        data = resolve_metadata(req.query)
        return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"Error resolving metadata: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/download")
def download_single(req: SingleDownloadRequest, background_tasks: BackgroundTasks):
    """ArkhSound single track downloader with auto ID3 tags and lyrics."""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty.")
    try:
        result = download_single_track(
            query_or_url=req.query,
            bitrate=req.bitrate or "320k",
            existing_meta=req.metadata,
        )
        file_path = result["file_path"]
        temp_dir = result["temp_dir"]
        filename = result["filename"]

        # Clean up temporary download directory after serving
        background_tasks.add_task(cleanup_temp_dir, temp_dir)

        encoded_filename = quote(filename)
        return FileResponse(
            path=file_path,
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{encoded_filename}"
            },
        )
    except Exception as e:
        logger.error(f"Single download error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stream")
def stream_audio(url: str = Query(..., description="YouTube or direct track URL")):
    """Proxies progressive audio stream for instant in-browser preview."""
    try:
        return StreamingResponse(
            proxy_audio_stream(url),
            media_type="audio/webm",
            headers={"Accept-Ranges": "bytes"},
        )
    except Exception as e:
        logger.error(f"Stream proxy error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------
# Playlist Harvester Endpoints (New Feature!)
# ---------------------------------------------------------

@app.post("/api/parse-playlist")
async def parse_playlist(req: PlaylistParseRequest):
    """Parses full Spotify or Apple Music playlist/album URL."""
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL cannot be empty.")

    try:
        if "spotify.com" in url:
            metadata = fetch_spotify_metadata(url)
        elif "apple.com" in url:
            metadata = fetch_apple_metadata(url)
        else:
            raise HTTPException(
                status_code=400,
                detail="Unsupported playlist link. Please enter a valid Spotify or Apple Music URL."
            )

        session_id = task_manager.create_session(metadata)
        session = task_manager.get_session(session_id)
        return {
            "success": True,
            "session_id": session_id,
            "session": session,
        }
    except Exception as e:
        logger.error(f"Failed to parse playlist: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/download/start")
async def start_batch_download(req: PlaylistBatchRequest):
    """Starts background downloading of selected playlist tracks."""
    session = task_manager.get_session(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    await task_manager.start_downloads(
        session_id=req.session_id,
        track_ids=req.track_ids,
        bitrate=req.bitrate or "320"
    )
    return {"success": True, "message": "Batch download started."}


@app.get("/api/session/{session_id}")
async def get_session(session_id: str):
    session = task_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    return {"success": True, "session": session}


@app.get("/api/file/{session_id}/{track_id}")
async def get_track_file(session_id: str, track_id: str):
    session = task_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    for track in session["tracks"]:
        if track["id"] == track_id:
            fp = track.get("file_path")
            if fp and os.path.exists(fp):
                return FileResponse(fp, media_type="audio/mpeg", filename=os.path.basename(fp))
            raise HTTPException(status_code=404, detail="Track audio not ready yet.")

    raise HTTPException(status_code=404, detail="Track not found.")


@app.get("/api/lrc/{session_id}/{track_id}")
async def get_track_lrc(session_id: str, track_id: str):
    session = task_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    for track in session["tracks"]:
        if track["id"] == track_id:
            lp = track.get("lrc_path")
            if lp and os.path.exists(lp):
                return FileResponse(lp, media_type="text/plain", filename=os.path.basename(lp))
            raise HTTPException(status_code=404, detail="LRC lyrics file not found.")

    raise HTTPException(status_code=404, detail="Track not found.")


@app.get("/api/lyrics/{session_id}/{track_id}")
async def get_track_lyrics(session_id: str, track_id: str):
    session = task_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    for track in session["tracks"]:
        if track["id"] == track_id:
            dur_ms = track.get("duration_ms", 0)
            lyrics = fetch_lyrics(
                track["title"],
                track["artist"],
                track.get("album", ""),
                dur_ms // 1000 if dur_ms else None
            )
            return {"success": True, "lyrics": lyrics}

    raise HTTPException(status_code=404, detail="Track not found.")


@app.get("/api/download-zip/{session_id}")
async def download_zip(session_id: str):
    session = task_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    zip_path = task_manager.create_zip(session_id)
    if not zip_path or not os.path.exists(zip_path):
        raise HTTPException(status_code=400, detail="No files available to package.")

    zip_filename = f"{session['title'].replace(' ', '_')}.zip"
    return FileResponse(zip_path, media_type="application/zip", filename=zip_filename)


@app.websocket("/ws/progress")
async def websocket_progress(websocket: WebSocket):
    await task_manager.register_websocket(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        task_manager.unregister_websocket(websocket)
    except Exception:
        task_manager.unregister_websocket(websocket)


# Mount static assets
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=False)
