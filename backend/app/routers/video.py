from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from pathlib import Path
import logging
from ..services.video_processor import VideoManager
import io

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/sample-video/stream")
async def stream_video():
    """Stream the sample video with real-time KPI extraction"""
    video_path = Path(__file__).parent.parent / "data" / "sample_traffic.mp4"
    
    try:
        video_manager = VideoManager.get_instance()
        processor = video_manager.get_processor(str(video_path))
        
        def generate_frames():
            for data in processor.get_frame_generator():
                # Create a multipart response with both the frame and KPIs
                frame = data["frame"]
                kpis = data["kpis"]
                
                # Format as multipart response
                boundary = b"frame"
                frame_data = b"".join([
                    b"--", boundary, b"\r\n",
                    b"Content-Type: image/jpeg\r\n",
                    b"Content-Length: ", str(len(frame)).encode(), b"\r\n",
                    b"\r\n",
                    frame, b"\r\n",
                    b"--", boundary, b"\r\n",
                    b"Content-Type: application/json\r\n",
                    b"\r\n",
                    str(kpis).encode(), b"\r\n"
                ])
                yield frame_data
        
        return StreamingResponse(
            generate_frames(),
            media_type="multipart/x-mixed-replace;boundary=frame"
        )
        
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Sample video file not found")
    except Exception as e:
        logger.error(f"Error streaming video: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sample-video/kpis")
async def get_video_kpis():
    """Get the latest KPIs from the video feed without the video stream"""
    video_path = Path(__file__).parent.parent / "data" / "sample_traffic.mp4"
    
    try:
        video_manager = VideoManager.get_instance()
        processor = video_manager.get_processor(str(video_path))
        
        # Get one frame of KPIs
        data = next(processor.get_frame_generator())
        return JSONResponse(content=data["kpis"])
        
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Sample video file not found")
    except Exception as e:
        logger.error(f"Error getting video KPIs: {e}")
        raise HTTPException(status_code=500, detail=str(e)) 