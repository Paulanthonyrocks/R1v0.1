import logging
import json
import time
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query
from app.dependency_injection import get_feed_manager, get_connection_manager, get_rate_limiter_manager
from app.services.feed_manager import FeedManager
from app.websocket.connection_manager import ConnectionManager
from app.utils.auth_utils import verify_firebase_token
from app.dependency_injection import is_admin as is_admin_check
from app.models.websocket import (
    WebSocketMessage,
    WebSocketMessageTypeEnum,
    FeedIdData,
    PongData,
    InitialFeedStatusesData,
    SubscribeData,
    UnsubscribeData,
    AuthenticateData,
    AuthSuccessData,
    AuthFailureData,
    UpdateFeedConfigData
)
from app.models.user import User
from app.utils.rate_limiter import RateLimiterManager

logger = logging.getLogger(__name__)
router = APIRouter()

async def message_receiver(
    websocket: WebSocket,
    client_id: str,
    connection_manager: ConnectionManager,
    feed_manager: FeedManager,
    rate_limiter: RateLimiterManager
):
    """
    Main loop for receiving and processing messages from a connected client.
    """
    try:
        async for message_text in websocket.iter_text():
            try:
                # 1. Parse raw JSON
                message_dict = json.loads(message_text)
                
                # 2. Validate basic structure using Pydantic
                # Using construct/dict access for speed if needed, but model_validate is safer
                try:
                    message = WebSocketMessage.model_validate(message_dict)
                except Exception as e:
                    logger.warning(f"Invalid message format from {client_id}: {e}")
                    continue

                msg_type = message.type
                data = message.data or {}

                # 3. Handle Message Types
                if msg_type == WebSocketMessageTypeEnum.PING:
                    logger.debug(f"Received PING from {client_id}")
                    # Echo back the correlation_id for RTT calculation
                    await connection_manager.send_personal_message(
                        WebSocketMessage(
                            type=WebSocketMessageTypeEnum.PONG,
                            data=PongData().model_dump(),
                            correlation_id=message.correlation_id
                        ).model_dump_json(),
                        client_id
                    )
                elif msg_type == WebSocketMessageTypeEnum.PONG:
                    # Client responded to a server PING
                    logger.debug(f"Received PONG from {client_id}")
                    
                    # Calculate RTT if we have a correlation_id and timestamp
                    rtt_ms = None
                    if message.correlation_id and message.timestamp:
                        try:
                            # If client sends timestamp back, we can calculate RTT
                            sent_time = float(message.timestamp)
                            rtt_ms = (time.time() * 1000) - sent_time
                        except (ValueError, TypeError):
                            pass
                            
                    connection_manager.record_pong(client_id, rtt_ms=rtt_ms)
                    pass

                elif msg_type == WebSocketMessageTypeEnum.AUTHENTICATE:
                    try:
                        auth_data = AuthenticateData(**data)
                        await verify_firebase_token(auth_data.token)
                        # If verification succeeds, we assume the user is still valid.
                        # We could update the user_id mapping if the user changed, but typically it's a refresh.
                        await connection_manager.send_personal_message(
                            WebSocketMessage(
                                type=WebSocketMessageTypeEnum.AUTH_SUCCESS,
                                data=AuthSuccessData().model_dump()
                            ).model_dump_json(),
                            client_id
                        )
                        logger.info(f"Client {client_id} re-authenticated successfully.")
                    except Exception as e:
                        logger.warning(f"Client {client_id} failed re-authentication: {e}")
                        await connection_manager.send_personal_message(
                            WebSocketMessage(
                                type=WebSocketMessageTypeEnum.AUTH_FAILURE,
                                data=AuthFailureData(message=str(e)).model_dump()
                            ).model_dump_json(),
                            client_id
                        )
                        # Disconnect if authentication fails
                        # The client should handle the AUTH_FAILURE and maybe redirect to login
                        # but we should close the socket from our end to be safe.
                        # However, we'll let the client close it or the next ping fail if context is lost.

                elif msg_type == WebSocketMessageTypeEnum.GET_INITIAL_FEED_STATUSES:
                    statuses = await feed_manager.get_all_statuses()
                    response = WebSocketMessage(
                        type=WebSocketMessageTypeEnum.INITIAL_FEED_STATUSES,
                        data=InitialFeedStatusesData(feeds=statuses).model_dump()
                    )
                    # CRITICAL: Use HIGH priority so initial statuses are never dropped
                    # when the client queue is loaded with video frames (LOW priority).
                    from app.websocket.connection_manager import MessagePriority
                    await connection_manager.send_personal_message(response.model_dump_json(), client_id, priority=MessagePriority.HIGH)

                elif msg_type in [
                    WebSocketMessageTypeEnum.START_FEED,
                    WebSocketMessageTypeEnum.STOP_FEED,
                    WebSocketMessageTypeEnum.RESTART_FEED,
                    WebSocketMessageTypeEnum.UPDATE_FEED_CONFIG
                ]:
                    # 3a. Rate Limiting for Control Messages
                    if not await rate_limiter.is_allowed(client_id):
                        logger.warning(f"Rate limit exceeded for control messages from {client_id}")
                        from app.websocket.connection_manager import MessagePriority
                        await connection_manager.send_personal_message(
                            WebSocketMessage(
                                type=WebSocketMessageTypeEnum.ERROR_NOTIFICATION,
                                data={"message": "Rate limit exceeded. Please slow down your requests."}
                            ).model_dump_json(),
                            client_id,
                            priority=MessagePriority.HIGH
                        )
                        continue

                    # Check Authorization
                    user_role = connection_manager.get_user_role(client_id)
                    if user_role != "admin":
                        logger.warning(f"Unauthorized feed control attempt by {client_id} (role: {user_role})")
                        from app.websocket.connection_manager import MessagePriority
                        await connection_manager.send_personal_message(
                            WebSocketMessage(
                                type=WebSocketMessageTypeEnum.ERROR_NOTIFICATION,
                                data={"message": "Unauthorized: Admin privileges required for feed control."}
                            ).model_dump_json(),
                            client_id,
                            priority=MessagePriority.HIGH
                        )
                        continue

                    # Process Admin Commands
                    try:
                        if msg_type == WebSocketMessageTypeEnum.START_FEED:
                            feed_id_data = FeedIdData(**data)
                            await feed_manager.start_feed(feed_id_data.feed_id)
                        elif msg_type == WebSocketMessageTypeEnum.STOP_FEED:
                            feed_id_data = FeedIdData(**data)
                            await feed_manager.stop_feed(feed_id_data.feed_id)
                        elif msg_type == WebSocketMessageTypeEnum.RESTART_FEED:
                            feed_id_data = FeedIdData(**data)
                            await feed_manager.restart_feed(feed_id_data.feed_id)
                        elif msg_type == WebSocketMessageTypeEnum.UPDATE_FEED_CONFIG:
                            update_data = UpdateFeedConfigData(**data)
                            await feed_manager.update_feed_config(update_data.feed_id, update_data.updates)
                    except Exception as e:
                        logger.error(f"Error processing {msg_type}: {e}")
                        from app.websocket.connection_manager import MessagePriority
                        await connection_manager.send_personal_message(
                            WebSocketMessage(
                                type=WebSocketMessageTypeEnum.ERROR_NOTIFICATION,
                                data={"message": f"Operation failed: {str(e)}"}
                            ).model_dump_json(),
                            client_id,
                            priority=MessagePriority.HIGH
                        )

                elif msg_type == WebSocketMessageTypeEnum.SUBSCRIBE:
                    try:
                        sub_data = SubscribeData(**data)
                        await connection_manager.subscribe_to_topic(client_id, sub_data.topic)
                    except Exception as e:
                        logger.warning(f"Subscribe error: {e}")

                elif msg_type == WebSocketMessageTypeEnum.UNSUBSCRIBE:
                    try:
                        unsub_data = UnsubscribeData(**data)
                        await connection_manager.unsubscribe_from_topic(client_id, unsub_data.topic)
                    except Exception as e:
                        logger.warning(f"Unsubscribe error: {e}")
                
                elif msg_type == WebSocketMessageTypeEnum.SUBSCRIBE_TO_FEED:
                    try:
                        feed_id_data = FeedIdData(**data)
                        await connection_manager.subscribe_to_feed(client_id, feed_id_data.feed_id)
                    except Exception as e:
                        logger.warning(f"Subscribe to feed error: {e}")
                
                elif msg_type == WebSocketMessageTypeEnum.UNSUBSCRIBE_FROM_FEED:
                    try:
                        feed_id_data = FeedIdData(**data)
                        await connection_manager.unsubscribe_from_feed(client_id, feed_id_data.feed_id)
                    except Exception as e:
                        logger.warning(f"Unsubscribe from feed error: {e}")

            except json.JSONDecodeError:
                logger.warning(f"Received invalid JSON from {client_id}: {message_text}")
            except Exception as e:
                logger.error(f"Error processing message from {client_id}: {e}", exc_info=True)

    except WebSocketDisconnect:
        logger.info(f"Client {client_id} disconnected normally.")
    except Exception as e:
        logger.error(f"Unexpected error in message_receiver for {client_id}: {e}", exc_info=True)

@router.websocket("/ws/{client_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    client_id: str,
    connection_manager: ConnectionManager = Depends(get_connection_manager),
    feed_manager: FeedManager = Depends(get_feed_manager),
    rate_limiter: RateLimiterManager = Depends(get_rate_limiter_manager),
    token: str | None = Query(None),
):
    """
    WebSocket endpoint that accepts connection immediately to handle auth errors gracefully.
    """
    await websocket.accept()

    try:
        if not token:
            logger.warning(f"WebSocket connection attempt without token from {client_id}")
            raise Exception("Token is missing")

        try:
            decoded_token = await verify_firebase_token(token)
            username = decoded_token.get("uid") or decoded_token.get("sub")
            if not username:
                raise Exception("Invalid token claims: missing uid/sub")
            
            user = User(
                username=username,
                email=decoded_token.get("email", ""),
                full_name=decoded_token.get("name", username),
                role=decoded_token.get("role", "user"),
            )
        except Exception as e:
            logger.warning(f"WebSocket auth failed for {client_id}: {e}")
            # Send explicit AUTH_FAILURE message before closing
            await websocket.send_text(
                WebSocketMessage(
                    type=WebSocketMessageTypeEnum.AUTH_FAILURE,
                    data=AuthFailureData(message=str(e)).model_dump()
                ).model_dump_json()
            )
            await websocket.close(code=1008, reason="Authentication failed")
            return

        # Proceed with connection
        await connection_manager.connect(websocket, client_id, user.username, user.role)

        try:
            # Run the receiver loop directly.
            # The ConnectionManager handles keepalives (ping/pong) independently.
            await message_receiver(websocket, client_id, connection_manager, feed_manager, rate_limiter)

        except Exception as e:
            logger.error(f"Critical WebSocket error for {client_id}: {e}", exc_info=True)
        finally:
            await connection_manager.disconnect(client_id, websocket)

    except Exception as e:
        # Catch-all for connection setup errors
        logger.error(f"Error in websocket_endpoint for {client_id}: {e}", exc_info=True)
        try:
            await websocket.close(code=1011) # Internal Error
        except:
            pass