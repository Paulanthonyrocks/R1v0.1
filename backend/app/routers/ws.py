import logging
import json
import time
import asyncio
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query
from app.dependency_injection import get_feed_manager, get_connection_manager, get_rate_limiter_manager
from app.services.feed_manager import FeedManager
from app.websocket.connection_manager import ConnectionManager, MessagePriority
from app.utils.auth_utils import verify_firebase_token, get_server_role
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
    assigned_id_holder: dict,
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
            # Publish the assigned id to the outer websocket_endpoint so its
            # finally block can call ConnectionManager.disconnect with the
            # correct key (the ConnectionManager's internal dicts are keyed by
            # assigned_id, not by the URL-path id).
            assigned_id_holder["id"] = assigned_id
            
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
            # DO NOT log the raw payload: the AUTHENTICATE frame carries the
            # client's Firebase ID token (a JWT) in cleartext, and the initial
            # and re-auth frames both flow through this loop -- logging the raw
            # text would leak live credentials into the server logs. Log only
            # a size/type hint at debug level; per-message-type handlers below
            # already log structured, token-free info at appropriate levels.
            logger.debug(f"WS recv from {client_id}: {len(message_text)} bytes")

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
                        # Benign race: client disconnected between PING and
                        # PONG (common at shutdown). Not an error.
                        logger.debug(f"Direct PONG send skipped (client gone): {e}")
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
                    logger.info(f"GET_INITIAL_FEED_STATUSES for {client_id}: found {len(statuses)} feeds")
                    response = WebSocketMessage(
                        type=WebSocketMessageTypeEnum.INITIAL_FEED_STATUSES,
                        data=InitialFeedStatusesData(feeds=statuses).model_dump()
                    )
                    response_json = response.model_dump_json()
                    logger.debug(f"Sending INITIAL_FEED_STATUSES to {client_id}: {response_json}")
                    # CRITICAL: Use HIGH priority so initial statuses are never dropped
                    # when the client queue is loaded with video frames (LOW priority).
                    await connection_manager.send_personal_message(response_json, client_id, priority=MessagePriority.HIGH)

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

                    # Check Authorization (server-side, not the cached connect-time
                    # role -- defeats role stickiness on long-lived sessions: a demoted
                    # admin loses control immediately instead of at token expiry).
                    cached_role = connection_manager.get_user_role(client_id)
                    user_id = connection_manager.client_id_to_user_id.get(client_id)
                    user_role = await get_server_role(user_id, fallback=cached_role)

                    # UPDATE_FEED_CONFIG is admin-only because it rewrites per-feed
                    # model + detection settings. The other three (start / stop /
                    # restart) are agency-or-above so they match the surveillance
                    # page guard and don't silently drop agency users' clicks.
                    if msg_type == WebSocketMessageTypeEnum.UPDATE_FEED_CONFIG:
                        required_role = "admin"
                    else:
                        required_role = "agency"
                    role_rank = {"viewer": 0, "agency": 1, "admin": 2}
                    if role_rank.get(user_role, -1) < role_rank.get(required_role, 99):
                        logger.warning(
                            f"Unauthorized {msg_type} attempt by {client_id} "
                            f"(role: {user_role}, required: {required_role})"
                        )
                        await connection_manager.send_personal_message(
                            WebSocketMessage(
                                type=WebSocketMessageTypeEnum.ERROR_NOTIFICATION,
                                data={"message": f"Unauthorized: {required_role.capitalize()} privileges required for {msg_type}."}
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
                            _task = asyncio.create_task(
                                feed_manager.update_feed_config(
                                    update_data.feed_id, update_data.updates
                                )
                            )

                            def _surface_update_error(t: asyncio.Task) -> None:
                                # Retrieve the exception so asyncio doesn't log
                                # a bare "Task exception was never retrieved".
                                # A malformed ROI (or any config error) must
                                # reach the client, not vanish.
                                ex = t.exception()
                                if ex is None:
                                    return
                                logger.error(
                                    f"[{client_id}] update_feed_config failed: {ex}"
                                )
                                try:
                                    asyncio.create_task(
                                        connection_manager.send_personal_message(
                                            WebSocketMessage(
                                                type=WebSocketMessageTypeEnum.ERROR_NOTIFICATION,
                                                data={
                                                    "message": f"Config update failed: {str(ex)}"
                                                },
                                            ).model_dump_json(),
                                            client_id,
                                            priority=MessagePriority.HIGH,
                                        )
                                    )
                                except Exception:
                                    pass

                            _task.add_done_callback(_surface_update_error)
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
                try:
                    await connection_manager.send_personal_message(
                        WebSocketMessage(
                            type=WebSocketMessageTypeEnum.ERROR_NOTIFICATION,
                            data={"message": f"Message rejected: {str(e)}"},
                        ).model_dump_json(),
                        client_id,
                        priority=MessagePriority.HIGH,
                    )
                except Exception:
                    pass

    except (WebSocketDisconnect, RuntimeError) as e:
        # Starlette's underlying receive path can raise RuntimeError instead
        # of WebSocketDisconnect when the protocol state is invalid (e.g. a
        # half-completed handshake where accept() never ran, or a peer that
        # closed before the server accepted). Treat it as a clean disconnect
        # at INFO level, not as an unexpected error with a full traceback —
        # before this fix every WebSocket teardown on a slow tunnel logged an
        # ERROR line + stacktrace, drowning the real signal in the logs.
        msg = str(e)
        if "WebSocket is not connected" in msg or "Need to call" in msg or "accept" in msg:
            logger.info(f"Client {client_id} disconnected (socket closed before accept).")
        else:
            logger.info(f"Client {client_id} disconnected: {e}")
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

    # Hold the *assigned* client_id (set by message_receiver after AUTH_SUCCESS)
    # in a mutable container so the finally block below can read it. Using the
    # FastAPI path-parameter `client_id` directly would be a shadow of the
    # parameter, not a mutation — message_receiver's local `client_id =
    # assigned_id` only rebinds its own scope.
    assigned_id_holder: dict = {"id": None}

    try:
        try:
            # Run the receiver loop. Pass the URL-path id as initial; once
            # AUTHENTICATE succeeds the holder will be updated.
            await message_receiver(websocket, client_id, assigned_id_holder, connection_manager, feed_manager, rate_limiter)

        except Exception as e:
            logger.error(f"Critical WebSocket error for {client_id}: {e}", exc_info=True)
        finally:
            # Disconnect using the assigned id if auth completed, otherwise
            # the URL-path id. The ConnectionManager's internal dicts are
            # keyed by the assigned id (the path id is never inserted after
            # AUTHENTICATE), so passing the wrong one used to skip the
            # per-client dict cleanup, leaking _client_locks and leaving
            # active_connections populated after teardown.
            final_id = assigned_id_holder["id"] or client_id
            await connection_manager.disconnect(final_id, websocket)

    except Exception as e:
        logger.error(f"Error in websocket_endpoint for {client_id}: {e}", exc_info=True)
        try:
            await websocket.close(code=1011)
        except:
            pass