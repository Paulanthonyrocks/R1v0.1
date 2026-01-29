import datetime
import collections.abc
import os
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
import logging

from app.config import get_current_config
from app.dependency_injection import get_current_active_user, get_feed_manager
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
    """Stream sample traffic video with real-time processing"""

    logger.info(
        f"GET /video/stream/{stream_id} endpoint called by user: {current_user.get('email')}"
    )
    
    config = get_current_config()
    output_directory = config.get("video_output", {}).get("output_directory")
    video_manager = VideoManager.get_instance(output_directory=output_directory)

    feed_manager = get_feed_manager()
    processor = video_manager.get_processor(stream_id, feed_manager)

    async def generate_frames():
        try:
            async for data in processor.get_frame_generator():
                frame_bytes = data["frame"]
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
                )
        except Exception as e:
            logger.error(f"Error during video stream for {stream_id}: {e}", exc_info=True)


    return StreamingResponse(
        generate_frames(), media_type="multipart/x-mixed-replace; boundary=frame"
    )


@router.post("/record/start")
async def start_video_recording(
    request: StartRecordingRequest,
    video_manager: VideoManager = Depends(VideoManager.get_instance),
    feed_manager=Depends(get_feed_manager),
):
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
    stream_id: str, video_manager: VideoManager = Depends(VideoManager.get_instance),
    feed_manager=Depends(get_feed_manager)
):
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
):
    videos = await db_manager.get_processed_videos(stream_id, limit, offset)
    return videos


@router.get("/processed-videos/{video_id}/download")
async def download_processed_video(
    video_id: int,
    db_manager=Depends(get_database_manager),
):
    video_entry = await db_manager.get_processed_video_by_id(video_id)
    if not video_entry:
        raise HTTPException(status_code=404, detail="Video not found")

    file_path = video_entry.file_path
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Video file not found on server")

    return FileResponse(
        path=file_path, filename=os.path.basename(file_path), media_type="video/mp4"
    )
