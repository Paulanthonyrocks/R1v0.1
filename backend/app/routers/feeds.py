# backend/app/routers/feeds.py

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status

# Import Models (adjust path if models are elsewhere)
from app.models.feeds import FeedStatus, FeedCreateRequest, FeedCreateResponse, StandardResponse
# Import Dependencies
from app.dependencies import get_feed_manager
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
    fm: FeedManager = Depends(get_feed_manager)
) -> List[FeedStatus]:
    """
    Endpoint to get the status of all registered feeds.
    """
    logger.info(f'Received request for get_feeds_status')
    try:
        statuses = await fm.get_all_statuses() # Assume async method
        return statuses
    except Exception as e:
        # Log the exception e
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve feed statuses")

@router.post(
    "/",
    response_model=FeedCreateResponse,
    status_code=status.HTTP_202_ACCEPTED, # Use 202 Accepted as start is async
    summary="Add and Start a New Feed",
    description="Adds a new feed source and initiates the processing task.",
)
async def add_and_start_feed(
    request: FeedCreateRequest,
    fm: FeedManager = Depends(get_feed_manager)
) -> FeedCreateResponse:
    """
    Endpoint to add a new feed source and attempt to start it.
    """
    try:
        feed_id = await fm.add_and_start_feed(request.source, request.name_hint)
        return FeedCreateResponse(id=feed_id, message="Feed submitted for processing.")
    except ResourceLimitError as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))
    except ValueError as e: # E.g., invalid source format
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        # Log the exception e
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to start feed: {e}")

@router.post(
    "/{feed_id}/start",
    response_model=StandardResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an Existing Stopped Feed",
)
async def start_feed(
    feed_id: str,
    fm: FeedManager = Depends(get_feed_manager)
) -> StandardResponse:
    """
    Endpoint to start a feed that is currently stopped.
    """
    try:
        await fm.start_feed(feed_id)
        return StandardResponse(message=f"Feed '{feed_id}' start initiated.")
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
    response_model=StandardResponse,
    summary="Stop a Running Feed",
)
async def stop_feed(
    feed_id: str,
    fm: FeedManager = Depends(get_feed_manager)
) -> StandardResponse:
    """
    Endpoint to stop a feed that is currently running or starting.
    """
    try:
        await fm.stop_feed(feed_id)
        return StandardResponse(message=f"Feed '{feed_id}' stop initiated.")
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
    fm: FeedManager = Depends(get_feed_manager)
) -> StandardResponse:
    """
    Endpoint to stop and then start a feed.
    """
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
    fm: FeedManager = Depends(get_feed_manager)
) -> StandardResponse:
    """
    Endpoint to stop all feeds that are currently running or starting.
    """
    try:
        await fm.stop_all_feeds()
        return StandardResponse(message="Stopping all feeds initiated.")
    except Exception as e:
        # Log the exception e
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to stop all feeds: {e}")