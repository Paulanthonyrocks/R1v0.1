from typing import Optional, List
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from app.dependencies import (
    get_feed_manager,
    get_current_active_user,
    get_current_active_user_optional,
    get_current_admin,
)
from app.services.feed_manager import (
    FeedManager,
    FeedNotFoundError,
    FeedOperationError,
    ResourceLimitError,
)
from app.exceptions import ResourceNotFound, OperationFailed, BadRequest, Forbidden
from app.models.common import APIResponse
from app.models.feeds import (
    FeedStatus,
    FeedCreateRequest,
    FeedCreateResponse,
    StandardResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/",
    response_model=APIResponse[List[FeedStatus]],
    summary="Get Status of All Feeds",
    description="Retrieves the current status, source, FPS, and potential errors for all known feeds.",
)
async def get_all_feeds_status(
    fm: FeedManager = Depends(get_feed_manager),
    current_user: Optional[dict] = Depends(get_current_active_user_optional),
) -> APIResponse[List[FeedStatus]]:
    """
    Endpoint to get the status of all registered feeds.
    """
    logger.info("Received request for get_all_feeds_status")
    if current_user:
        logger.info(
            f"User {current_user.get('uid', 'unknown_user_uid')} requested status of all feeds."
        )
    else:
        logger.info("Anonymous user requested status of all feeds.")
    try:
        statuses = await fm.get_all_statuses()
        return APIResponse.success(
            data=statuses, message="Successfully retrieved feed statuses."
        )
    except Exception as e:
        logger.error(f"Failed to retrieve feed statuses: {e}", exc_info=True)
        raise OperationFailed(detail="Failed to retrieve feed statuses.")


@router.get(
    "/sample-feed-data",
    response_model=APIResponse[dict],
    summary="Get Latest Metrics for Sample Feed",
    description="Returns the latest metrics for the sample video feed.",
)
async def get_sample_feed_data(
    fm: FeedManager = Depends(get_feed_manager),
    current_user: Optional[dict] = Depends(get_current_active_user_optional),
) -> APIResponse[dict]:
    logger.info(
        f"GET /feeds/sample-feed-data endpoint called by user: {current_user.get('email') if current_user else 'anonymous'}"
    )
    if not fm._sample_feed_id or not fm.process_registry.get(fm._sample_feed_id):
        raise ResourceNotFound(detail="Sample feed not found.")

    sample_feed_entry = fm.process_registry[fm._sample_feed_id]

    if sample_feed_entry["status"] != "running":
        raise BadRequest(detail="Sample feed is not running.")

    metrics = sample_feed_entry["latest_metrics"]
    if not metrics:
        return APIResponse.success(
            data={}, message="No metrics available yet for sample feed."
        )
    return APIResponse.success(
        data=metrics, message="Successfully retrieved sample feed metrics."
    )


@router.post(
    "/",
    response_model=APIResponse[FeedCreateResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Add and Start a New Feed",
    description="Adds a new feed source and initiates the processing task.",
)
async def add_and_start_feed(
    request: FeedCreateRequest,
    fm: FeedManager = Depends(get_feed_manager),
    current_user: dict = Depends(get_current_admin),
) -> APIResponse[FeedCreateResponse]:
    logger.info(
        f"POST /feeds endpoint called by admin user: {current_user.get('uid', 'unknown_admin_uid')} to add feed: {request.source}"
    )
    """
    Endpoint to add a new feed source and attempt to start it. Requires authentication.
    """
    logger.info(
        f"Admin user {current_user.get('uid', 'unknown_admin_uid')} attempting to add feed: {request.source}"
    )
    try:
        result = await fm.add_and_start_feed(
            source=request.source,
            latitude=request.latitude,
            longitude=request.longitude,
            name_hint=request.name,
            is_looped=True,  # Assuming new feeds are looped by default, adjust if needed
        )

        response_data = FeedCreateResponse(
            feed_id=result["feed_id"],
            message=f"Feed '{request.name or request.source}' added. Status: {result['status']}.",
            initial_status=result["status"],
        )
        return APIResponse.success(
            data=response_data, message="Feed added and start initiated."
        )
    except ResourceLimitError as e:
        raise Forbidden(detail=str(e))
    except ValueError as e:
        raise BadRequest(detail=str(e))
    except Exception as e:
        logger.error(f"Failed to add and start feed: {e}", exc_info=True)
        raise OperationFailed(detail=f"Failed to add and start feed: {e}")


@router.post(
    "/{feed_id}/start",
    response_model=APIResponse[FeedStatus],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an Existing Stopped Feed",
)
async def start_feed(
    feed_id: str,
    fm: FeedManager = Depends(get_feed_manager),
    current_user: dict = Depends(get_current_active_user),
) -> APIResponse[FeedStatus]:
    logger.info(
        f"POST /feeds/{feed_id}/start endpoint called by user: {current_user.get('uid', 'unknown_user_uid')}"
    )
    """
    Endpoint to start a feed that is currently stopped. Requires authentication.
    """
    logger.info(
        f"User {current_user.get('uid', 'unknown_user_uid')} attempting to start feed: {feed_id}"
    )
    try:
        success = await fm.start_feed(feed_id)
        if not success:
            current_status = await fm.get_feed_status(feed_id)
            error_msg = (
                current_status.error_details
                if current_status
                else "Unknown error during start."
            )
            raise OperationFailed(detail=f"Failed to start feed {feed_id}: {error_msg}")

        updated_status = await fm.get_feed_status(feed_id)
        if not updated_status:
            raise ResourceNotFound(
                detail=f"Feed {feed_id} not found after start attempt."
            )
        return APIResponse.success(
            data=updated_status, message=f"Feed {feed_id} started successfully."
        )
    except FeedNotFoundError:
        raise ResourceNotFound(detail=f"Feed ID '{feed_id}' not found.")
    except FeedOperationError as e:
        raise BadRequest(detail=str(e))
    except ResourceLimitError as e:
        raise Forbidden(detail=str(e))
    except Exception as e:
        logger.error(f"Failed to start feed '{feed_id}': {e}", exc_info=True)
        raise OperationFailed(detail=f"Failed to start feed '{feed_id}': {e}")


@router.post(
    "/{feed_id}/stop",
    response_model=APIResponse[FeedStatus],
    summary="Stop a Running Feed",
)
async def stop_feed(
    feed_id: str,
    fm: FeedManager = Depends(get_feed_manager),
    current_user: dict = Depends(get_current_active_user),
) -> APIResponse[FeedStatus]:
    logger.info(
        f"POST /feeds/{feed_id}/stop endpoint called by user: {current_user.get('uid', 'unknown_user_uid')}"
    )
    """
    Endpoint to stop a feed that is currently running or starting. Requires authentication.
    """
    logger.info(
        f"User {current_user.get('uid', 'unknown_user_uid')} attempting to stop feed: {feed_id}"
    )
    try:
        success = await fm.stop_feed(feed_id)
        if not success:
            current_status = await fm.get_feed_status(feed_id)
            error_msg = (
                current_status.error_details
                if current_status
                else "Unknown error during stop."
            )
            raise OperationFailed(detail=f"Failed to stop feed {feed_id}: {error_msg}")

        updated_status = await fm.get_feed_status(feed_id)
        if not updated_status:
            raise ResourceNotFound(
                detail=f"Feed {feed_id} not found after stop attempt."
            )
        return APIResponse.success(
            data=updated_status, message=f"Feed {feed_id} stopped successfully."
        )
    except FeedNotFoundError:
        raise ResourceNotFound(detail=f"Feed ID '{feed_id}' not found.")
    except FeedOperationError as e:
        raise BadRequest(detail=str(e))
    except Exception as e:
        logger.error(f"Failed to stop feed '{feed_id}': {e}", exc_info=True)
        raise OperationFailed(detail=f"Failed to stop feed '{feed_id}': {e}")


@router.post(
    "/{feed_id}/restart",
    response_model=APIResponse[StandardResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Restart a Feed",
)
async def restart_feed(
    feed_id: str,
    fm: FeedManager = Depends(get_feed_manager),
    current_user: dict = Depends(get_current_admin),
) -> APIResponse[StandardResponse]:
    logger.info(
        f"POST /feeds/{feed_id}/restart endpoint called by admin user: {current_user.get('uid', 'unknown_admin_uid')}"
    )
    """
    Endpoint to stop and then start a feed. Requires authentication.
    """
    logger.info(
        f"Admin user {current_user.get('uid', 'unknown_admin_uid')} attempting to restart feed: {feed_id}"
    )
    try:
        await fm.restart_feed(feed_id)
        return APIResponse.success(message=f"Feed '{feed_id}' restart initiated.")
    except FeedNotFoundError:
        raise ResourceNotFound(detail=f"Feed ID '{feed_id}' not found.")
    except ResourceLimitError as e:
        raise Forbidden(detail=str(e))
    except Exception as e:
        logger.error(f"Failed to restart feed '{feed_id}': {e}", exc_info=True)
        raise OperationFailed(detail=f"Failed to restart feed '{feed_id}': {e}")


@router.post(
    "/stop-all",
    response_model=APIResponse[StandardResponse],
    summary="Stop All Active Feeds",
)
async def stop_all_feeds(
    fm: FeedManager = Depends(get_feed_manager),
    current_user: dict = Depends(get_current_admin),
) -> APIResponse[StandardResponse]:
    """
    Endpoint to stop all feeds that are currently running or starting. Requires authentication.
    """
    logger.info(
        f"Admin user {current_user.get('uid', 'unknown_admin_uid')} attempting to stop all feeds."
    )
    try:
        await fm.stop_all_feeds()
        return APIResponse.success(message="Stopping all feeds initiated.")
    except Exception as e:
        logger.error(f"Failed to stop all feeds: {e}", exc_info=True)
        raise OperationFailed(detail=f"Failed to stop all feeds: {e}")


@router.delete(
    "/{feed_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete a Specific Feed",
)
async def delete_feed(
    feed_id: str,
    fm: FeedManager = Depends(get_feed_manager),
    current_user: dict = Depends(get_current_admin),
):
    """
    Endpoint to delete a specific feed. Requires authentication.
    """
    logger.info(
        f"Admin user {current_user.get('uid', 'unknown_admin_uid')} attempting to delete feed: {feed_id}"
    )
    try:
        success = await fm.remove_feed(feed_id)
        if not success:
            # If remove_feed returns False, it could be because the feed wasn't found
            # or another internal error. We check if it still exists to differentiate.
            if await fm.get_feed_status(feed_id):
                raise OperationFailed(
                    detail=f"Failed to delete feed {feed_id}. It might be in use or encountered an error during removal."
                )
            else:
                # If it doesn't exist, it's effectively a successful deletion from the client's perspective
                return APIResponse.success(
                    message=f"Feed {feed_id} already deleted or not found."
                )
        return APIResponse.success(message=f"Feed {feed_id} deleted successfully.")
    except FeedNotFoundError:
        raise ResourceNotFound(detail=f"Feed ID '{feed_id}' not found.")
    except Exception as e:
        logger.error(f"Failed to delete feed '{feed_id}': {e}", exc_info=True)
        raise OperationFailed(detail=f"Failed to delete feed '{feed_id}': {e}")


@router.get(
    "/{feed_id}",
    response_model=APIResponse[FeedStatus],
    summary="Get Status of a Specific Feed",
)
async def get_specific_feed_status(
    feed_id: str,
    fm: FeedManager = Depends(get_feed_manager),
    current_user: Optional[dict] = Depends(get_current_active_user_optional),
) -> APIResponse[FeedStatus]:
    """Endpoint to get the current status of a specific feed."""
    if current_user:
        logger.info(
            f"User {current_user.get('uid', 'unknown_user_uid')} requesting status for feed {feed_id}"
        )
    else:
        logger.info(f"Anonymous user requesting status for feed {feed_id}")

    feed_status = await fm.get_feed_status(feed_id)
    if not feed_status:
        raise ResourceNotFound(detail=f"Feed with ID '{feed_id}' not found.")
    return APIResponse.success(
        data=feed_status, message=f"Successfully retrieved status for feed {feed_id}."
    )


@router.get("/{feed_id}/kpis", summary="Get latest KPIs for a specific feed")
async def get_feed_kpis(
    feed_id: str,
    fm: FeedManager = Depends(get_feed_manager),
    current_user: Optional[dict] = Depends(get_current_active_user_optional),
):
    """Get the latest KPIs/metrics for a specific feed (including sample video)."""
    if current_user:
        logger.info(
            f"User {current_user.get('uid', 'unknown_user_uid')} requesting KPIs for feed {feed_id}"
        )
    else:
        logger.info(f"Anonymous user requesting KPIs for feed {feed_id}")
    feed_status = await fm.get_feed_status(feed_id)
    if not feed_status:
        raise HTTPException(
            status_code=404, detail=f"Feed with ID '{feed_id}' not found."
        )
    metrics = getattr(feed_status, "latest_metrics", None)
    if not metrics:
        return JSONResponse(
            content={"message": "No metrics available yet."}, status_code=202
        )
    return JSONResponse(content=metrics)
