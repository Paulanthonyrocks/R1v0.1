import datetime
import collections.abc
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
import logging
from app.config import get_current_config
from app.dependency_injection import get_current_active_user, get_feed_manager
from app.services.video_processor import VideoManager

router = APIRouter()
logger = logging.getLogger(__name__)


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
            # The stream will end here, which is a graceful way to handle it.
            # The client will see the connection close.


    return StreamingResponse(
        generate_frames(), media_type="multipart/x-mixed-replace; boundary=frame"
    )
