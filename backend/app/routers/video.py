import datetime
import collections.abc
import os
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import logging

from app.config import get_current_config
from app.dependency_injection import get_current_active_user, get_feed_manager, get_video_manager
from app.services.video_processor import VideoManager
from app.database import get_database_manager

router = APIRouter()
logger = logging.getLogger(__name__)


class StartRecordingRequest(BaseModel):
    stream_id: str
    output_filename: str
    frame_rate: float = 10.0


class ProcessedVideoResponse(BaseModel):
    id: int
    stream_id: str
    file_path: str
    start_time: str
    end_time: str
    duration: float

    class Config:
        from_attributes = True


def convert_datetime_to_iso(obj):
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    elif isinstance(obj, str) or isinstance(obj, bytes):
        return obj
    elif isinstance(obj, collections.abc.Mapping):
        return {k: convert_datetime_to_iso(v) for k, v in obj.items()}
    elif isinstance(obj, collections.abc.Iterable):
        return [convert_datetime_to_iso(i) for i in obj]
    else:
        return obj


@router.get("/stream/{stream_id:path}")
async def stream_video(stream_id: str, current_user: dict = Depends(get_current_active_user)):
    """
    DEPRECATED. Live frames are delivered via the WebSocket VIDEO_FRAME
    subscription. The legacy MJPEG generator now returns 501 so any stale
    clients fail fast instead of silently hanging on an unreachable queue.
    """
    logger.info(
        f"GET /video/stream/{stream_id} endpoint called by user: {current_user.get('email')} "
        f"(deprecated path)"
    )
    raise HTTPException(
        status_code=501,
        detail=(
            "MJPEG streaming is no longer supported. "
            "Subscribe to VIDEO_FRAME messages over the WebSocket API instead."
        ),
    )


@router.post("/record/start")
async def start_video_recording(
    request: StartRecordingRequest,
    video_manager: VideoManager = Depends(get_video_manager),
    feed_manager=Depends(get_feed_manager),
    current_user: dict = Depends(get_current_active_user),
):
    """
    Start a recording for the given stream.

    Recording writes decoded frames to disk via VideoProcessor.
    The pipeline is enabled per-feed in config (video_output.enabled=true)
    and per-stream via the live_feed_manager when the stream actually has
    a running pipeline.
    """
    config = get_current_config()
    if not config.get("video_output", {}).get("enabled", False):
        raise HTTPException(
            status_code=503,
            detail=(
                "Recording is disabled in config (video_output.enabled=false). "
                "Set it to true to enable. The recording pipeline is wired but opt-in."
            ),
        )

    processor = video_manager.get_processor(request.stream_id, feed_manager)
    success = await processor.start_recording(
        request.output_filename,
        request.frame_rate,
    )
    if success:
        return {
            "message": f"Recording started for stream {request.stream_id}",
            "output_path": os.path.join(
                "backend", "data", "processed_videos", request.output_filename
            ),
        }
    raise HTTPException(
        status_code=400,
        detail=f"Failed to start recording for stream {request.stream_id}",
    )


@router.post("/record/stop")
async def stop_video_recording(
    stream_id: str, video_manager: VideoManager = Depends(get_video_manager),
    feed_manager=Depends(get_feed_manager),
    current_user: dict = Depends(get_current_active_user),
):
    config = get_current_config()
    if not config.get("video_output", {}).get("enabled", False):
        raise HTTPException(
            status_code=503,
            detail="Recording is disabled in config (video_output.enabled=false).",
        )
    processor = video_manager.get_processor(stream_id, feed_manager)
    success = await processor.stop_recording()
    if success:
        return {"message": f"Recording stopped for stream {stream_id}"}
    raise HTTPException(
        status_code=400, detail=f"Failed to stop recording for stream {stream_id}"
    )


@router.get("/processed-videos", response_model=List[ProcessedVideoResponse])
async def get_processed_videos_metadata(
    stream_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db_manager=Depends(get_database_manager),
    current_user: dict = Depends(get_current_active_user),
):
    videos = await db_manager.get_processed_videos(stream_id, limit, offset)
    return videos


@router.get("/processed-videos/{video_id}/download")
async def download_processed_video(
    video_id: int,
    db_manager=Depends(get_database_manager),
    current_user: dict = Depends(get_current_active_user),
):
    video_entry = await db_manager.get_processed_video_by_id(video_id)
    if not video_entry:
        raise HTTPException(status_code=404, detail="Video not found")

    file_path = video_entry.file_path
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Video file not found on server")

    # Path traversal guard: file must resolve (following symlinks) into one of
    # the configured output directories. ``realpath`` catches symlinks pointing
    # outside the allowed dir, which ``abspath`` alone would miss.
    resolved_file = os.path.realpath(video_entry.file_path)
    allowed_dirs = [
        os.path.realpath(d)
        for d in (
            get_current_config().get("video_output", {}).get("output_directory"),
            get_current_config().get("video_processing", {}).get("output_directory"),
        )
        if d
    ]
    # Fallback if no dir from config
    if not allowed_dirs:
        allowed_dirs = [os.path.realpath("backend/data/processed_videos")]
    if not any(
        resolved_file == d or resolved_file.startswith(d + os.sep)
        for d in allowed_dirs
    ):
        logger.warning(
            f"download_processed_video blocked: '{resolved_file}' not in {allowed_dirs}"
        )
        raise HTTPException(status_code=403, detail="Access denied")

    return FileResponse(
        path=file_path, filename=os.path.basename(file_path), media_type="video/mp4"
    )
