from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from typing import List, Optional
from pydantic import BaseModel
import os

from app.services.video_processor import VideoManager
from app.services.feed_manager import get_feed_manager
from app.database import get_database_manager

router = APIRouter()


class StartRecordingRequest(BaseModel):
    stream_id: str
    output_filename: str
    frame_rate: float = 10.0
    frame_width: int = 1280
    frame_height: int = 720


class ProcessedVideoResponse(BaseModel):
    id: int
    stream_id: str
    file_path: str
    start_time: str
    end_time: str
    duration: float

    class Config:
        orm_mode = True


@router.post("/video/record/start")
async def start_video_recording(
    request: StartRecordingRequest,
    video_manager: VideoManager = Depends(VideoManager.get_instance),
    feed_manager=Depends(get_feed_manager),
):
    processor = video_manager.get_processor(request.stream_id, feed_manager)
    success = await processor.start_recording(
        request.output_filename,
        request.frame_rate,
        (request.frame_width, request.frame_height),
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


@router.post("/video/record/stop")
async def stop_video_recording(
    stream_id: str, video_manager: VideoManager = Depends(VideoManager.get_instance)
):
    processor = video_manager.get_processor(
        stream_id, Depends(get_feed_manager)
    )  # Feed manager is needed for processor instance
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
    background_tasks: BackgroundTasks,
    db_manager=Depends(get_database_manager),
):
    video_entry = await db_manager.get_processed_video_by_id(video_id)
    if not video_entry:
        raise HTTPException(status_code=404, detail="Video not found")

    file_path = video_entry.file_path
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Video file not found on server")

    from fastapi.responses import FileResponse

    return FileResponse(
        path=file_path, filename=os.path.basename(file_path), media_type="video/mp4"
    )
