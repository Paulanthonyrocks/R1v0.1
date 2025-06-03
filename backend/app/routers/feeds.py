# backend/app/models/feeds.py
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field

class FeedStatus(BaseModel):
    id: str
    source: str
    status: str = Field(..., examples=["stopped", "running", "starting", "error"])
    fps: Optional[float] = None
    error_message: Optional[str] = None

class FeedDetails(FeedStatus):
    name: Optional[str] = None
    last_update: Optional[datetime] = None
    last_capture: Optional[datetime] = None
    error_message: Optional[str] = None

class FeedCreateRequest(BaseModel):
    source: str = Field(..., examples=["/path/to/video.mp4", "webcam:0"])
    name_hint: Optional[str] = None

class FeedCreateResponse(BaseModel):
    id: str
    status: str = "starting"
    message: str
    initial_status: Optional[str] = None

class StandardResponse(BaseModel):
    success: bool = True
    message: str

# backend/app/routers/feeds.py
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import JSONResponse

# Import Dependencies
from app.dependencies import get_feed_manager, get_current_active_user, get_current_active_user_optional, get_current_admin
# Import Services
from app.services.feed_manager import FeedManager, FeedNotFoundError, FeedOperationError, ResourceLimitError
import logging

# Configure logging (optional, can be configured globally in main.py)
# logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


router = APIRouter()

@router.get(
    "/",
    response_model=List[FeedStatus],
    summary="Get Status of All Feeds",
    description="Retrieves the current status, source, FPS, and potential errors for all known feeds.",
)
async def get_feeds_status(
    fm: FeedManager = Depends(get_feed_manager),
    current_user: dict = Depends(get_current_active_user)
) -> List[FeedStatus]:
    """
    Endpoint to get the status of all registered feeds.
    """
    logger.info(f'Received request for get_feeds_status')
    logger.info(f"User {current_user.get('uid', 'unknown_user_uid')} requested status of all feeds.")
    try:
        statuses = await fm.get_all_feed_statuses() # Assume async method
        return statuses
    except Exception as e:
        # Log the exception e
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve feed statuses")

@router.post(
    "/",
    response_model=FeedCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add and Start a New Feed",
    description="Adds a new feed source and initiates the processing task.",
)
async def add_and_start_feed(
    request: FeedCreateRequest,
    fm: FeedManager = Depends(get_feed_manager),
    current_user: dict = Depends(get_current_admin)
) -> FeedCreateResponse:
    """
    Endpoint to add a new feed source and attempt to start it. Requires authentication.
    """
    logger.info(f"Admin user {current_user.get('uid', 'unknown_admin_uid')} attempting to add feed: {request.source}")
    try:
        feed_id = await fm.add_feed(feed_config=request.model_dump()) # Pass the whole request or specific fields
        if not feed_id:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to add feed.")
        
        # Attempt to start the feed immediately
        start_success = await fm.start_feed(feed_id)
        current_status = await fm.get_feed_status(feed_id) # Get FeedStatusData object
        
        return FeedCreateResponse(
            feed_id=feed_id, 
            message=f"Feed '{request.name}' added. Start attempt: {'successful' if start_success else 'failed'}.",
            initial_status=current_status.status if current_status else "error"
        )
    except ResourceLimitError as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))
    except ValueError as e: # E.g., invalid source format
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        # Log the exception e
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to start feed: {e}")

@router.post(
    "/{feed_id}/start",
    response_model=FeedStatus,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an Existing Stopped Feed",
)
async def start_feed(
    feed_id: str,
    fm: FeedManager = Depends(get_feed_manager),
    current_user: dict = Depends(get_current_active_user)
) -> FeedStatus:
    """
    Endpoint to start a feed that is currently stopped. Requires authentication.
    """
    logger.info(f"User {current_user.get('uid', 'unknown_user_uid')} attempting to start feed: {feed_id}")
    try:
        success = await fm.start_feed(feed_id)
        if not success:
            current_status = await fm.get_feed_status(feed_id)
            error_msg = current_status.error_details if current_status else "Unknown error during start."
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to start feed {feed_id}: {error_msg}")
        
        # Return the updated status of the feed
        updated_status = await fm.get_feed_status(feed_id)
        if not updated_status:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Feed {feed_id} not found after start attempt.")
        return updated_status
    except FeedNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Feed ID '{feed_id}' not found.")
    except FeedOperationError as e: # e.g., already running
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ResourceLimitError as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))
    except Exception as e:
        # Log the exception e
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to start feed '{feed_id}': {e}")

@router.post(
    "/{feed_id}/stop",
    response_model=FeedStatus,
    summary="Stop a Running Feed",
)
async def stop_feed(
    feed_id: str,
    fm: FeedManager = Depends(get_feed_manager),
    current_user: dict = Depends(get_current_active_user)
) -> FeedStatus:
    """
    Endpoint to stop a feed that is currently running or starting. Requires authentication.
    """
    logger.info(f"User {current_user.get('uid', 'unknown_user_uid')} attempting to stop feed: {feed_id}")
    try:
        success = await fm.stop_feed(feed_id)
        if not success:
            current_status = await fm.get_feed_status(feed_id)
            error_msg = current_status.error_details if current_status else "Unknown error during stop."
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to stop feed {feed_id}: {error_msg}")

        updated_status = await fm.get_feed_status(feed_id)
        if not updated_status:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Feed {feed_id} not found after stop attempt.")
        return updated_status
    except FeedNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Feed ID '{feed_id}' not found.")
    except FeedOperationError as e: # e.g., already stopped
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except Exception as e:
        # Log the exception e
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to stop feed '{feed_id}': {e}")

@router.post(
    "/{feed_id}/restart",
    response_model=StandardResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Restart a Feed",
)
async def restart_feed(
    feed_id: str,
    fm: FeedManager = Depends(get_feed_manager),
    current_user: dict = Depends(get_current_admin)
) -> StandardResponse:
    """
    Endpoint to stop and then start a feed. Requires authentication.
    """
    logger.info(f"Admin user {current_user.get('uid', 'unknown_admin_uid')} attempting to restart feed: {feed_id}")
    try:
        await fm.restart_feed(feed_id)
        return StandardResponse(message=f"Feed '{feed_id}' restart initiated.")
    except FeedNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Feed ID '{feed_id}' not found.")
    except ResourceLimitError as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))
    except Exception as e:
        # Log the exception e
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to restart feed '{feed_id}': {e}")


@router.post(
    "/stop-all",
    response_model=StandardResponse,
    summary="Stop All Active Feeds",
)
async def stop_all_feeds(
    fm: FeedManager = Depends(get_feed_manager),
    current_user: dict = Depends(get_current_admin)
) -> StandardResponse:
    """
    Endpoint to stop all feeds that are currently running or starting. Requires authentication.
    """
    logger.info(f"Admin user {current_user.get('uid', 'unknown_admin_uid')} attempting to stop all feeds.")
    try:
        await fm.stop_all_feeds()
        return StandardResponse(message="Stopping all feeds initiated.")
    except Exception as e:
        # Log the exception e
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to stop all feeds: {e}")

@router.delete("/{feed_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a Specific Feed")
async def delete_feed(
    feed_id: str,
    fm: FeedManager = Depends(get_feed_manager),
    current_user: dict = Depends(get_current_admin)
):
    """
    Endpoint to delete a specific feed. Requires authentication.
    """
    logger.info(f"Admin user {current_user.get('uid', 'unknown_admin_uid')} attempting to delete feed: {feed_id}")
    success = await fm.remove_feed(feed_id)
    if not success:
        # Check if feed still exists to determine 404 vs 500
        if await fm.get_feed_status(feed_id):
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete feed {feed_id}. It might be in use or encountered an error during removal.")
        else:
            # If status is None, it's already gone or never existed.
            # HTTP_204_NO_CONTENT implies success even if it was already gone.
            # However, if fm.remove_feed returned False because it wasn't found, this is okay.
            # If it returned False for another reason while feed existed, the 500 above is better.
            # For simplicity, if not found and remove_feed said False, it's effectively a 204 or 404.  
            # The `status_code=status.HTTP_204_NO_CONTENT` handles the success case (even if already gone).
            # If remove_feed specifically indicates not_found vs other error, we could return 404.
            pass # Let it return 204 if not found by remove_feed
    return # No content on success

@router.get("/{feed_id}", response_model=FeedStatus, summary="Get Status of a Specific Feed")
async def get_specific_feed_status(
    feed_id: str,
    fm: FeedManager = Depends(get_feed_manager),
    current_user: Optional[dict] = Depends(get_current_active_user_optional) # Optional auth
) -> FeedStatus:
    """Endpoint to get the current status of a specific feed."""
    if current_user:
        logger.info(f"User {current_user.get('uid', 'unknown_user_uid')} requesting status for feed {feed_id}")
    else:
        logger.info(f"Anonymous user requesting status for feed {feed_id}")
        
    feed_status = await fm.get_feed_status(feed_id) # This method needs to be implemented in FeedManager
    if not feed_status:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Feed with ID '{feed_id}' not found.")
    return feed_status

@router.get("/{feed_id}/kpis", summary="Get latest KPIs for a specific feed")
async def get_feed_kpis(
    feed_id: str,
    fm: FeedManager = Depends(get_feed_manager),
    current_user: Optional[dict] = Depends(get_current_active_user_optional)
):
    """Get the latest KPIs/metrics for a specific feed (including sample video)."""
    if current_user:
        logger.info(f"User {current_user.get('uid', 'unknown_user_uid')} requesting KPIs for feed {feed_id}")
    else:
        logger.info(f"Anonymous user requesting KPIs for feed {feed_id}")
    feed_status = await fm.get_feed_status(feed_id)
    if not feed_status:
        raise HTTPException(status_code=404, detail=f"Feed with ID '{feed_id}' not found.")
    metrics = getattr(feed_status, 'latest_metrics', None)
    if not metrics:
        return JSONResponse(content={"message": "No metrics available yet."}, status_code=202)
    return JSONResponse(content=metrics)