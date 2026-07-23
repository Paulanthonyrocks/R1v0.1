import json
import logging
import asyncio
from fastapi import APIRouter, HTTPException, Depends
from app.dependency_injection import get_current_active_user
from pydantic import BaseModel
from aiortc import RTCPeerConnection, RTCSessionDescription
from ..websocket.webrtc_track import FeedStreamTrack

logger = logging.getLogger("webrtc")
router = APIRouter()

# In-memory store for active WebRTC connections
# In production, ensure graceful shutdown hooks clean this up
_pcs = set()

class WebRTCOffer(BaseModel):
    sdp: str
    type: str

@router.post("/offer/{feed_id}")
async def webrtc_offer(feed_id: str, offer: WebRTCOffer, current_user: dict = Depends(get_current_active_user)):
    """
    Receives an SDP offer from a WebRTC client, registers a 
    FeedStreamTrack for the specified feed_id, and returns an SDP answer.
    """
    from ..main import feed_manager_instance # Avoid circular import
    
    if not feed_manager_instance:
        raise HTTPException(status_code=503, detail="Feed Manager not initialized")
        
    if feed_id not in feed_manager_instance.active_feeds:
        raise HTTPException(status_code=404, detail=f"Feed {feed_id} is not active")

    pc = RTCPeerConnection()
    _pcs.add(pc)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info(f"WebRTC Connection State for {feed_id} changed: {pc.connectionState}")
        if pc.connectionState == "failed" or pc.connectionState == "closed":
            # Clean up tracking and peer connections
            await pc.close()
            _pcs.discard(pc)
            
            # De-register from FeedManager
            if hasattr(feed_manager_instance, 'unregister_webrtc_queue'):
                feed_manager_instance.unregister_webrtc_queue(feed_id, id(pc))

    # Create a dedicated queue for this connection
    frame_queue = asyncio.Queue(maxsize=10)
    
    # Register this queue with the FeedManager so it pushes decoded numpy frames to it
    if hasattr(feed_manager_instance, 'register_webrtc_queue'):
        feed_manager_instance.register_webrtc_queue(feed_id, id(pc), frame_queue)
    else:
        logger.error("FeedManager missing register_webrtc_queue capability")
        raise HTTPException(status_code=500, detail="WebRTC hooks not installed in FeedManager")

    # Bind the queue to an aiortc track and append it to the connection
    track = FeedStreamTrack(feed_id=feed_id, frame_queue=frame_queue)
    pc.addTrack(track)

    # Process the remote SDP offer
    rtc_offer = RTCSessionDescription(sdp=offer.sdp, type=offer.type)
    await pc.setRemoteDescription(rtc_offer)

    # Create and send the local SDP answer
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return {
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type
    }
