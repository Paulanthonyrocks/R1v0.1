# backend/app/routers/feeds.py
import logging
from fastapi import APIRouter, Depends, HTTPException, status # Query not used
from fastapi.responses import JSONResponse

from app.services.feed_manager import FeedManager, FeedNotFoundError, FeedOperationError, ResourceLimitError
# Removed get_current_active_user and Query, added get_current_active_user_optional
from app.dependencies import get_feed_manager, get_current_active_user, get_current_active_user_optional
# Imported FeedStatusData and FeedOperationalStatusEnum
from app.models.feeds import FeedStatusData, FeedOperationalStatusEnum, FeedCreateRequest, FeedCreateResponse, StandardResponse
from typing import List, Optional # Removed Dict, Any, datetime, BaseModel, Field


logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/",
    response_model=List[FeedStatusData],
    summary="Get Status of All Feeds",
    description="Retrieves status, source, FPS, and errors for all feeds.",
)
async def get_feeds_status(
    fm: FeedManager = Depends(get_feed_manager)
) -> List[FeedStatusData]:
    """Endpoint to get the status of all registered feeds."""
    logger.info('Received request for get_feeds_status')
    try:
        statuses = await fm.get_all_feed_statuses()
        return statuses
    except Exception as e:
        logger.error(f"Failed to retrieve feed statuses: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Failed to retrieve feed statuses")


@router.post(
    "/",
    response_model=FeedCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add and Start a New Feed",
    description="Adds a new feed source and initiates processing.",
)
async def add_and_start_feed(
    request: FeedCreateRequest,
    fm: FeedManager = Depends(get_feed_manager),
    current_user: dict = Depends(get_current_active_user)  # noqa F841 for auth
) -> FeedCreateResponse:
    """Adds a new feed and attempts to start it. Requires authentication."""
    # logger.info(f"User {current_user.get('email')} adding feed: {request.source}")
    try:
        feed_id = await fm.add_feed(feed_config=request.model_dump())
        if not feed_id:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail="Failed to add feed.")
        start_success = await fm.start_feed(feed_id)
        current_status = await fm.get_feed_status(feed_id)
        # Ensure request.name exists or handle if it's name_hint
        feed_name = request.name if hasattr(request, 'name') else request.name_hint
        return FeedCreateResponse(
            feed_id=feed_id,
            message=f"Feed '{feed_name}' added. Start: {'OK' if start_success else 'Fail'}.",
            initial_status=current_status.status if current_status else FeedOperationalStatusEnum.ERROR # type: ignore
        )
    except ResourceLimitError as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to add/start feed: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"Failed to start feed: {e}")


@router.post(
    "/{feed_id}/start",
    response_model=FeedStatusData,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an Existing Stopped Feed",
)
async def start_feed(
    feed_id: str,
    fm: FeedManager = Depends(get_feed_manager),
    current_user: dict = Depends(get_current_active_user)  # noqa F841 for auth
) -> FeedStatusData:
    """Starts a stopped feed. Requires authentication."""
    # logger.info(f"User {current_user.get('email')} starting feed: {feed_id}")
    try:
        success = await fm.start_feed(feed_id)
        if not success:
            status_data = await fm.get_feed_status(feed_id)
            err_detail = status_data.error_details if status_data else "Unknown start error."
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail=f"Failed to start feed {feed_id}: {err_detail}")
        updated_status = await fm.get_feed_status(feed_id)
        if not updated_status:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail=f"Feed {feed_id} not found after start.")
        return updated_status
    except FeedNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Feed '{feed_id}' not found.")
    except FeedOperationError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ResourceLimitError as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to start feed '{feed_id}': {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"Failed to start feed '{feed_id}': {e}")


@router.post(
    "/{feed_id}/stop",
    response_model=FeedStatusData,
    summary="Stop a Running Feed",
)
async def stop_feed(
    feed_id: str,
    fm: FeedManager = Depends(get_feed_manager),
    current_user: dict = Depends(get_current_active_user)  # noqa F841 for auth
) -> FeedStatusData:
    """Stops a running/starting feed. Requires authentication."""
    # logger.info(f"User {current_user.get('email')} stopping feed: {feed_id}")
    try:
        success = await fm.stop_feed(feed_id)
        if not success:
            status_data = await fm.get_feed_status(feed_id)
            err_detail = status_data.error_details if status_data else "Unknown stop error."
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail=f"Failed to stop feed {feed_id}: {err_detail}")
        updated_status = await fm.get_feed_status(feed_id)
        if not updated_status:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail=f"Feed {feed_id} not found after stop.")
        return updated_status
    except FeedNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Feed '{feed_id}' not found.")
    except FeedOperationError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to stop feed '{feed_id}': {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"Failed to stop feed '{feed_id}': {e}")


@router.post(
    "/{feed_id}/restart",
    response_model=StandardResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Restart a Feed",
)
async def restart_feed(
    feed_id: str,
    fm: FeedManager = Depends(get_feed_manager),
    current_user: dict = Depends(get_current_active_user)  # noqa F841 for auth
) -> StandardResponse:
    """Stops and then starts a feed. Requires authentication."""
    # logger.info(f"User {current_user.get('email')} restarting feed: {feed_id}")
    try:
        await fm.restart_feed(feed_id)
        return StandardResponse(message=f"Feed '{feed_id}' restart initiated.")
    except FeedNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Feed '{feed_id}' not found.")
    except ResourceLimitError as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to restart feed '{feed_id}': {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"Failed to restart feed '{feed_id}': {e}")


@router.post(
    "/stop-all",
    response_model=StandardResponse,
    summary="Stop All Active Feeds",
)
async def stop_all_feeds(
    fm: FeedManager = Depends(get_feed_manager),
    current_user: dict = Depends(get_current_active_user)  # noqa F841 for auth
) -> StandardResponse:
    """Stops all running/starting feeds. Requires authentication."""
    # logger.info(f"User {current_user.get('email')} stopping all feeds.")
    try:
        await fm.stop_all_feeds()
        return StandardResponse(message="Stopping all feeds initiated.")
    except Exception as e:
        logger.error(f"Failed to stop all feeds: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"Failed to stop all feeds: {e}")


@router.delete("/{feed_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a Specific Feed")
async def delete_feed(
    feed_id: str,
    fm: FeedManager = Depends(get_feed_manager),
    current_user: dict = Depends(get_current_active_user)  # noqa F841 for auth
):
    """Deletes a specific feed. Requires authentication."""
    # logger.info(f"User {current_user.get('email')} deleting feed: {feed_id}")
    success = await fm.remove_feed(feed_id)
    if not success:
        if await fm.get_feed_status(feed_id): # Check if still exists after failed remove
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail=f"Failed to delete feed {feed_id}. Error during removal.")
        # If not found by get_feed_status, it means remove_feed handled it or it never existed.
        # HTTP 204 is appropriate for "successfully processed, no content to return",
        # which includes "successfully deleted" or "was already gone".
    return  # No content on success


@router.get("/{feed_id}", response_model=FeedStatusData, summary="Get Status of a Specific Feed")
async def get_specific_feed_status(
    feed_id: str,
    fm: FeedManager = Depends(get_feed_manager),
    current_user: Optional[dict] = Depends(get_current_active_user_optional) # noqa F841
) -> FeedStatusData:
    """Gets the current status of a specific feed. Optional authentication."""
    # if current_user: logger.info(f"User requesting status for feed {feed_id}")
    # else: logger.info(f"Anonymous user requesting status for feed {feed_id}")
    feed_status = await fm.get_feed_status(feed_id)
    if not feed_status:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Feed with ID '{feed_id}' not found.")
    return feed_status


@router.get("/{feed_id}/kpis", summary="Get latest KPIs for a specific feed")
async def get_feed_kpis(
    feed_id: str,
    fm: FeedManager = Depends(get_feed_manager),
    current_user: Optional[dict] = Depends(get_current_active_user_optional) # noqa F841
):
    """Gets latest KPIs/metrics for a specific feed. Optional authentication."""
    # if current_user: logger.info(f"User requesting KPIs for feed {feed_id}")
    # else: logger.info(f"Anonymous user requesting KPIs for feed {feed_id}")
    feed_status = await fm.get_feed_status(feed_id)
    if not feed_status:
        raise HTTPException(status_code=404, detail=f"Feed with ID '{feed_id}' not found.")
    metrics = getattr(feed_status, 'latest_metrics', None)
    if not metrics:
        return JSONResponse(content={"message": "No metrics available yet."}, status_code=202)
    return JSONResponse(content=metrics)
