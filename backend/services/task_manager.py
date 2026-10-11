"""Download Task Manager with WebSocket streaming and batch ZIP packager."""
import os
import asyncio
import logging
import zipfile
from typing import Dict, List, Any, Optional, Set
from fastapi import WebSocket

from backend.services.downloader import download_playlist_track
from backend.services.lyrics_service import fetch_lyrics
from backend.services.tagger import tag_mp3_file

logger = logging.getLogger("task_manager")


class TaskManager:
    def __init__(self, download_base_dir: str):
        self.download_base_dir = download_base_dir
        os.makedirs(download_base_dir, exist_ok=True)
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.active_websockets: Set[WebSocket] = set()
        self.semaphore = asyncio.Semaphore(2)  # Concurrency limit

    async def register_websocket(self, websocket: WebSocket):
        await websocket.accept()
        self.active_websockets.add(websocket)

    def unregister_websocket(self, websocket: WebSocket):
        self.active_websockets.discard(websocket)

    async def broadcast(self, message: Dict[str, Any]):
        dead_sockets = set()
        for ws in self.active_websockets:
            try:
                await ws.send_json(message)
            except Exception:
                dead_sockets.add(ws)
        for ws in dead_sockets:
            self.active_websockets.discard(ws)

    def create_session(self, playlist_info: Dict[str, Any]) -> str:
        session_id = playlist_info.get("id", "playlist_session")
        session_dir = os.path.join(self.download_base_dir, session_id)
        os.makedirs(session_dir, exist_ok=True)

        tracks = []
        for t in playlist_info.get("tracks", []):
            tracks.append({
                **t,
                "status": "queued",
                "stage": "queued",
                "percent": 0.0,
                "speed": "",
                "eta": 0,
                "error": None,
                "file_path": None,
                "filename": None,
                "has_lyrics": False,
                "is_synced": False,
                "lrc_path": None,
            })

        self.sessions[session_id] = {
            "id": session_id,
            "title": playlist_info.get("title", "Playlist"),
            "source": playlist_info.get("source", "unknown"),
            "cover_art": playlist_info.get("cover_art", ""),
            "total_tracks": len(tracks),
            "completed_count": 0,
            "failed_count": 0,
            "directory": session_dir,
            "tracks": tracks,
            "is_running": False,
            "zip_path": None,
        }
        return session_id

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self.sessions.get(session_id)

    async def start_downloads(self, session_id: str, track_ids: Optional[List[str]] = None, bitrate: str = "320"):
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        session["is_running"] = True
        tasks = []
        for track in session["tracks"]:
            if track_ids and track["id"] not in track_ids:
                continue
            if track["status"] == "completed":
                continue
            tasks.append(self._process_single_track(session_id, track, bitrate))

        asyncio.create_task(self._run_all(session_id, tasks))

    async def _run_all(self, session_id: str, tasks: List[Any]):
        await asyncio.gather(*tasks, return_exceptions=True)
        session = self.sessions.get(session_id)
        if session:
            session["is_running"] = False
            self.create_zip(session_id)
            await self.broadcast({
                "type": "batch_completed",
                "session_id": session_id,
                "completed": session["completed_count"],
                "total": session["total_tracks"],
            })

    async def _process_single_track(self, session_id: str, track: Dict[str, Any], bitrate: str):
        async with self.semaphore:
            session = self.sessions.get(session_id)
            if not session:
                return

            track["status"] = "processing"
            track["stage"] = "searching"
            track["percent"] = 5.0
            await self._notify_track_update(session_id, track)

            loop = asyncio.get_running_loop()

            def sync_progress_callback(data: Dict[str, Any]):
                track["stage"] = data.get("stage", track["stage"])
                track["percent"] = data.get("percent", track["percent"])
                track["speed"] = data.get("speed", "")
                track["eta"] = data.get("eta", 0)
                asyncio.run_coroutine_threadsafe(
                    self._notify_track_update(session_id, track), loop
                )

            try:
                dur_ms = track.get("duration_ms", 0)
                download_res = await loop.run_in_executor(
                    None,
                    download_playlist_track,
                    session["directory"],
                    track["title"],
                    track["artist"],
                    dur_ms,
                    bitrate,
                    sync_progress_callback,
                )

                mp3_path = download_res["file_path"]
                track["file_path"] = mp3_path
                track["filename"] = download_res["filename"]

                # Fetch lyrics
                track["stage"] = "lyrics"
                track["percent"] = 92.0
                await self._notify_track_update(session_id, track)

                lyrics_res = await loop.run_in_executor(
                    None,
                    fetch_lyrics,
                    track["title"],
                    track["artist"],
                    track.get("album", ""),
                    dur_ms // 1000 if dur_ms else None,
                )

                track["has_lyrics"] = lyrics_res.get("has_lyrics", False)
                track["is_synced"] = lyrics_res.get("is_synced", False)

                # Tag MP3
                track["stage"] = "tagging"
                track["percent"] = 97.0
                await self._notify_track_update(session_id, track)

                await loop.run_in_executor(
                    None,
                    tag_mp3_file,
                    mp3_path,
                    track["title"],
                    track["artist"],
                    track.get("album", session.get("title", "")),
                    track.get("track_number", 1),
                    session.get("total_tracks", 1),
                    None,
                    track.get("cover_art") or session.get("cover_art"),
                    lyrics_res.get("plain_lyrics"),
                    lyrics_res.get("synced_lyrics"),
                    True,
                )

                if track["is_synced"]:
                    track["lrc_path"] = os.path.splitext(mp3_path)[0] + ".lrc"

                track["status"] = "completed"
                track["stage"] = "ready"
                track["percent"] = 100.0
                session["completed_count"] += 1

            except Exception as e:
                logger.error(f"Error processing track {track['title']}: {e}", exc_info=True)
                track["status"] = "failed"
                track["stage"] = "error"
                track["error"] = str(e)
                session["failed_count"] += 1

            await self._notify_track_update(session_id, track)

    async def _notify_track_update(self, session_id: str, track: Dict[str, Any]):
        await self.broadcast({
            "type": "track_update",
            "session_id": session_id,
            "track_id": track["id"],
            "track": track,
        })

    def create_zip(self, session_id: str) -> Optional[str]:
        session = self.sessions.get(session_id)
        if not session:
            return None

        zip_filename = f"{session['id']}.zip"
        zip_path = os.path.join(session["directory"], zip_filename)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for track in session["tracks"]:
                if track.get("file_path") and os.path.exists(track["file_path"]):
                    arcname = os.path.basename(track["file_path"])
                    zipf.write(track["file_path"], arcname=arcname)
                if track.get("lrc_path") and os.path.exists(track["lrc_path"]):
                    arcname = os.path.basename(track["lrc_path"])
                    zipf.write(track["lrc_path"], arcname=arcname)

        session["zip_path"] = zip_path
        return zip_path
