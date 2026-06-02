import logging
import json
import time
import asyncio
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query
from app.dependency_injection import get_feed_manager, get_connection_manager, get_rate_limiter_manager
from app.services.feed_manager import FeedManager
from app.websocket.connection_manager import ConnectionManager, MessagePriority
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
    initial_id: str,
    connection_manager: ConnectionManager,
    feed_manager: FeedManager,
    rate_limiter: RateLimiterManager
):
    """
    Main loop for receiving and processing messages from a connected client.
    """
    is_authenticated = False
    client_id = initial_id
    
    try:
        # --- Initial Authentication Phase ---
        try:
            message_text = await asyncio.wait_for(websocket.receive_text(), timeout=15.0)
            message_dict = json.loads(message_text)
            message = WebSocketMessage.model_validate(message_dict)
            
            if message.type != WebSocketMessageTypeEnum.AUTHENTICATE:
                # ... (error handling)
                try:
                    await websocket.send_text(
                        WebSocketMessage(
                            type=WebSocketMessageTypeEnum.AUTH_FAILURE,
                            data=AuthFailureData(message="First message must be AUTHENTICATE").model_dump()
                        ).model_dump_json()
                    )
                except RuntimeError as re:
                    logger.debug(f"Could not send auth failure to {initial_id} (client already disconnected): {re}")
                try:
                    await websocket.close(code=1008)
                except RuntimeError as re:
                    logger.debug(f"Could not close websocket for {initial_id} (already closed): {re}")
                return

            auth_data = AuthenticateData(**(message.data or {}))
            decoded_token = await verify_firebase_token(auth_data.token)
            username = decoded_token.get("uid") or decoded_token.get("sub")
            if not username:
                raise Exception("Invalid token claims: missing uid/sub")
            
            user = User(
                username=username,
                email=decoded_token.get("email", ""),
                full_name=decoded_token.get("name", username),
                role=decoded_token.get("role", "user"),
            )

            # Generate server-assigned client_id to prevent hijacking
            import uuid
            assigned_id = f"client_{uuid.uuid4().hex[:12]}"
            
            # If we had a temporary ID, we should ensure it's not used as the primary key in the manager
            # though ConnectionManager.connect usually just adds/replaces.
            await connection_manager.connect(websocket, assigned_id, user.username, user.role)
            client_id = assigned_id
            
            # Send AUTH_SUCCESS with the assigned client_id
            try:
                await websocket.send_text(
                    WebSocketMessage(
                        type=WebSocketMessageTypeEnum.AUTH_SUCCESS,
                        data=AuthSuccessData(client_id=assigned_id).model_dump()
                    ).model_dump_json()
                )
            except RuntimeError as re:
                logger.debug(f"Could not send auth success to {initial_id} (client already disconnected): {re}")
            
            is_authenticated = True
            logger.info(f"Client assigned {client_id} authenticated as {user.username}")

        except asyncio.TimeoutError:
            logger.warning(f"Authentication timeout for {initial_id}. Disconnecting.")
            try:
                await websocket.close(code=1008, reason="Auth timeout")
            except RuntimeError as re:
                logger.debug(f"Could not close websocket for {initial_id} (already closed): {re}")
            return
        except WebSocketDisconnect:
            logger.warning(f"Client {initial_id} disconnected before authenticating.")
            return
        except Exception as e:
            logger.warning(f"Initial authentication failed for {initial_id}: {e}")
            try:
                await websocket.send_text(
                    WebSocketMessage(
                        type=WebSocketMessageTypeEnum.AUTH_FAILURE,
                        data=AuthFailureData(message=str(e)).model_dump()
                    ).model_dump_json()
                )
            except RuntimeError as re:
                logger.debug(f"Could not send auth failure to {initial_id} (client already disconnected): {re}")
            try:
                await websocket.close(code=1008)
            except RuntimeError as re:
                logger.debug(f"Could not close websocket for {initial_id} (already closed): {re}")
            return

        # --- Main Message Loop ---
        async for message_text in websocket.iter_text():
            # Log every raw message for debugging purposes
            logger.info(f"RAW message from {client_id}: {message_text}")

            # 0. Inbound Size Limit (Security)
            if len(message_text) > 64_000:
                logger.warning(f"Message from {client_id} exceeds size limit ({len(message_text)} bytes). Dropping.")
                continue

            try:
                # 1. Parse raw JSON
                message_dict = json.loads(message_text)
                
                # 2. Validate basic structure using Pydantic
                try:
                    message = WebSocketMessage.model_validate(message_dict)
                except Exception as e:
                    logger.info(f"Invalid message format from {client_id}: {e} | Data: {message_dict}")
                    continue

                msg_type = message.type
                data = message.data or {}

                # 3. Handle Message Types
                if msg_type == WebSocketMessageTypeEnum.PING:
                    logger.info(f"Received PING from {client_id}, sending direct PONG")
                    pong_msg = WebSocketMessage(
                        type=WebSocketMessageTypeEnum.PONG,
                        data=PongData().model_dump(),
                        correlation_id=message.correlation_id
                    ).model_dump_json()
                    try:
                        await websocket.send_text(pong_msg)
                    except Exception as e:
                        logger.error(f"Direct PONG send failed: {e}")
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
                    # Auth Rate Limiting
                    if not await rate_limiter.is_allowed(f"auth_{client_id}"):
                        logger.warning(f"Auth rate limit exceeded for {client_id}")
                        await connection_manager.send_personal_message(
                            WebSocketMessage(
                                type=WebSocketMessageTypeEnum.ERROR_NOTIFICATION,
                                data={"message": "Authentication requests too frequent. Please wait."}
                            ).model_dump_json(),
                            client_id,
                            priority=MessagePriority.HIGH
                        )
                        continue

                    try:
                        auth_data = AuthenticateData(**data)
                        decoded_token = await verify_firebase_token(auth_data.token)
                        
                        # Re-verify and update the user role upon re-authentication
                        new_role = decoded_token.get("role", "user")
                        connection_manager.update_user_role(client_id, new_role)
                        
                        await connection_manager.send_personal_message(
                            WebSocketMessage(
                                type=WebSocketMessageTypeEnum.AUTH_SUCCESS,
                                data=AuthSuccessData().model_dump()
                            ).model_dump_json(),
                            client_id
                        )
                        logger.info(f"Client {client_id} re-authenticated successfully. Role updated to {new_role}.")
                    except Exception as e:
                        logger.warning(f"Client {client_id} failed re-authentication: {e}")
                        await connection_manager.send_personal_message(
                            WebSocketMessage(
                                type=WebSocketMessageTypeEnum.AUTH_FAILURE,
                                data=AuthFailureData(message=str(e)).model_dump()
                            ).model_dump_json(),
                            client_id
                        )

                elif msg_type == WebSocketMessageTypeEnum.GET_INITIAL_FEED_STATUSES:
                    statuses = await feed_manager.get_all_statuses()
                    response = WebSocketMessage(
                        type=WebSocketMessageTypeEnum.INITIAL_FEED_STATUSES,
                        data=InitialFeedStatusesData(feeds=statuses).model_dump()
                    )
                    # CRITICAL: Use HIGH priority so initial statuses are never dropped
                    # when the client queue is loaded with video frames (LOW priority).
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
                            asyncio.create_task(feed_manager.start_feed(feed_id_data.feed_id))
                        elif msg_type == WebSocketMessageTypeEnum.STOP_FEED:
                            feed_id_data = FeedIdData(**data)
                            asyncio.create_task(feed_manager.stop_feed(feed_id_data.feed_id))
                        elif msg_type == WebSocketMessageTypeEnum.RESTART_FEED:
                            feed_id_data = FeedIdData(**data)
                            asyncio.create_task(feed_manager.restart_feed(feed_id_data.feed_id))
                        elif msg_type == WebSocketMessageTypeEnum.UPDATE_FEED_CONFIG:
                            update_data = UpdateFeedConfigData(**data)
                            asyncio.create_task(feed_manager.update_feed_config(update_data.feed_id, update_data.updates))
                    except Exception as e:
                        logger.error(f"Error scheduling {msg_type}: {e}")
                        await connection_manager.send_personal_message(
                            WebSocketMessage(
                                type=WebSocketMessageTypeEnum.ERROR_NOTIFICATION,
                                data={"message": f"Operation scheduling failed: {str(e)}"}
                            ).model_dump_json(),
                            client_id,
                            priority=MessagePriority.HIGH
                        )

                elif msg_type == WebSocketMessageTypeEnum.SUBSCRIBE:
                    try:
                        sub_data = SubscribeData(**data)
                        
                        # Define a callback to push an immediate update when subscribing to specific topics
                        async def on_subscribe(cid: str):
                            if sub_data.topic == 'kpi':
                                logger.info(f"Triggering immediate KPI push for client {cid} upon subscription")
                                await feed_manager.trigger_kpi_push()
                        
                        await connection_manager.subscribe_to_topic(
                            client_id, 
                            sub_data.topic, 
                            on_subscribe_callback=on_subscribe
                        )
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
):
    """
    WebSocket endpoint. Authentication must be performed via an AUTHENTICATE message as the first frame.
    Server assigns the client_id after successful authentication.
    """
    await websocket.accept()

    # Use the client_id from the path as the temporary ID for tracking before authentication
    temp_client_id = client_id

    try:
        try:
            # Run the receiver loop.
            await message_receiver(websocket, temp_client_id, connection_manager, feed_manager, rate_limiter)

        except Exception as e:
            logger.error(f"Critical WebSocket error for {temp_client_id}: {e}", exc_info=True)
        finally:
            # Disconnect using the actual ID if it was assigned, otherwise the temp ID
            # Note: message_receiver updates the connection_manager mapping.
            # We must ensure we disconnect the correct session.
            await connection_manager.disconnect(temp_client_id, websocket)

    except Exception as e:
        logger.error(f"Error in websocket_endpoint for {temp_client_id}: {e}", exc_info=True)
        try:
            await websocket.close(code=1011)
        except:
            pass