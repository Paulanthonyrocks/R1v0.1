import logging
import json
import asyncio
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
from app.dependency_injection import get_feed_manager, get_connection_manager
from app.services.feed_manager import FeedManager
from app.websocket.connection_manager import ConnectionManager
from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum, FeedIdData
from app.dependencies.auth import get_current_user_ws
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()

async def keepalive_sender(websocket: WebSocket):
    """
    Sends a keepalive message every 15 seconds.
    Stops immediately if the socket closes.
    """
    # Create the JSON string once to save CPU in the loop
    pong_message = WebSocketMessage(type=WebSocketMessageTypeEnum.INTERNAL_PONG, data={}).model_dump_json()
    
    try:
        while True:
            if websocket.client_state == WebSocketState.DISCONNECTED:
                break
            
            await asyncio.sleep(15)
            await websocket.send_text(pong_message)
            # logger.debug("Sent keepalive pong.") # Commented out to reduce log noise
    except (WebSocketDisconnect, ConnectionResetError):
        pass  # Normal closure
    except Exception as e:
        logger.error(f"Keepalive sender error: {e}")

async def message_receiver(
    websocket: WebSocket,
    client_id: str,
    connection_manager: ConnectionManager,
    feed_manager: FeedManager,
):
    """Receives and processes messages from the client."""
    try:
        while True:
            # receive_text raises WebSocketDisconnect if client closes
            raw_data = await websocket.receive_text()

            # 1. Quick check for heartbeat strings (often sent by browsers directly)
            if raw_data == "ping" or raw_data == "pong":
                continue

            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                logger.warning(f"Client {client_id} sent invalid JSON.")
                continue

            # 2. Parse into Pydantic Model
            try:
                message = WebSocketMessage(**data)
            except Exception as e:
                logger.warning(f"Invalid message format from {client_id}: {e}")
                continue

            # 3. Handle Message Types
            if message.type == WebSocketMessageTypeEnum.INTERNAL_PING:
                continue # Just a heartbeat

            elif message.type == WebSocketMessageTypeEnum.GET_INITIAL_FEED_STATUSES:
                statuses = await feed_manager.get_all_statuses()
                response = WebSocketMessage(
                    type=WebSocketMessageTypeEnum.INITIAL_FEED_STATUSES,
                    data={"feeds": statuses}
                )
                await websocket.send_text(response.model_dump_json())
                continue

            # For feed control messages, ensure we have valid FeedIdData
            # Pydantic (in WebSocketMessage) should have already parsed 'data' into FeedIdData
            # but we verify it has the attribute to be safe.
            feed_id = getattr(message.data, 'feed_id', None)

            if not feed_id and message.type in [
                WebSocketMessageTypeEnum.SUBSCRIBE_TO_FEED,
                WebSocketMessageTypeEnum.UNSUBSCRIBE_FROM_FEED,
                WebSocketMessageTypeEnum.START_FEED,
                WebSocketMessageTypeEnum.STOP_FEED,
                WebSocketMessageTypeEnum.RESTART_FEED
            ]:
                logger.warning(f"Client {client_id} sent {message.type} without a valid feed_id.")
                continue

            if message.type == WebSocketMessageTypeEnum.SUBSCRIBE_TO_FEED:
                logger.info(f"Client {client_id} subscribing to {feed_id}")
                await connection_manager.subscribe_to_topic(client_id, f"video:{feed_id}")
                await connection_manager.subscribe_to_topic(client_id, f"feed:{feed_id}")
                await connection_manager.subscribe_to_topic(client_id, f"feed_alerts:{feed_id}")

            elif message.type == WebSocketMessageTypeEnum.UNSUBSCRIBE_FROM_FEED:
                logger.info(f"Client {client_id} unsubscribing from {feed_id}")
                await connection_manager.unsubscribe_from_topic(client_id, f"video:{feed_id}")
                await connection_manager.unsubscribe_from_topic(client_id, f"feed:{feed_id}")
                await connection_manager.unsubscribe_from_topic(client_id, f"feed_alerts:{feed_id}")

            elif message.type == WebSocketMessageTypeEnum.START_FEED:
                logger.info(f"Client {client_id} requesting START {feed_id}")
                await feed_manager.start_feed(feed_id)

            elif message.type == WebSocketMessageTypeEnum.STOP_FEED:
                logger.info(f"Client {client_id} requesting STOP {feed_id}")
                await feed_manager.stop_feed(feed_id)
            
            elif message.type == WebSocketMessageTypeEnum.RESTART_FEED:
                logger.info(f"Client {client_id} requesting RESTART {feed_id}")
                await feed_manager.restart_feed(feed_id)

    except WebSocketDisconnect:
        logger.info(f"Client {client_id} disconnected.")
    except Exception as e:
        logger.error(f"Error in message_receiver for {client_id}: {e}", exc_info=True)


@router.websocket("/ws/{client_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    client_id: str,
    connection_manager: ConnectionManager = Depends(get_connection_manager),
    feed_manager: FeedManager = Depends(get_feed_manager),
    user: User = Depends(get_current_user_ws),
):
    if not user:
        # Auth failed (usually caught by dependency, but safe double check)
        await websocket.close(code=1008)
        return

    await connection_manager.connect(websocket, client_id, user.username)

    try:
        # Create tasks for sending and receiving
        sender_task = asyncio.create_task(keepalive_sender(websocket))
        receiver_task = asyncio.create_task(
            message_receiver(websocket, client_id, connection_manager, feed_manager)
        )

        # Wait until EITHER the sender fails/stops OR the receiver fails/stops (client disconnects)
        done, pending = await asyncio.wait(
            [sender_task, receiver_task],
            return_when=asyncio.FIRST_COMPLETED
        )

        # Cancel whichever task is still running
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    except Exception as e:
        logger.error(f"Critical WebSocket error for {client_id}: {e}", exc_info=True)
    finally:
        await connection_manager.disconnect(client_id, websocket)