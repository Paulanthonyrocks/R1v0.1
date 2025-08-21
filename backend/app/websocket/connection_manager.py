import asyncio
import logging
import time
from typing import Dict, Any, Set, Optional
from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
import json
from datetime import datetime

from app.models.websocket import (
    WebSocketMessage,
    WebSocketMessageTypeEnum,
    ErrorNotification,
    GeneralNotification,
    RefreshFeedData,
)  # Import new models
from app.services.exceptions import FeedNotFoundError, FeedOperationError
from app.exceptions import ConnectionLimitExceeded
# from app.dependencies import get_current_active_user_ws # We'll define a similar function here or call directly

from app.utils.service_getters import get_feed_manager  # Import the feed manager getter

logger = logging.getLogger(__name__)  # Initialize logger


class ActiveWebSocketConnection:
    def __init__(
        self,
        websocket: WebSocket,
        client_id: str,
        manager: "ConnectionManager",
        user_info: Optional[Dict[str, Any]] = None,
    ):
        self.websocket = websocket
        self.client_id = client_id
        self.manager = manager
        self.user_info: Optional[Dict[str, Any]] = user_info
        self.subscriptions: Set[str] = set()
        self.last_ping_sent: float = time.time() # Renamed to clarify it's last ping sent by server
        self.last_pong_received: float = time.time() # New: Last time a PONG was received from client
        self.ping_timeout: float = 300.0  # 300 seconds timeout (5 minutes) - increased from 90s for dashboard stability
        self.token_refresh_time: Optional[float] = None
        self.token: Optional[str] = None
        self.token_expiry: Optional[float] = None  # Unix timestamp of token expiration

    async def send_text(self, text: str):
        await self.websocket.send_text(text)

    async def send_json_model(self, message: WebSocketMessage):
        """Sends a Pydantic model as JSON over WebSocket."""
        try:
            if self.websocket.client_state == WebSocketState.CONNECTED:
                logger.debug(
                    f"Client {self.client_id}: Before send_json. WebSocket state: {self.websocket.client_state}"
                )
                await self.websocket.send_json(message.model_dump(mode="json"))
                logger.debug(
                    f"Client {self.client_id}: After send_json. WebSocket state: {self.websocket.client_state}"
                )
            else:
                logger.warning(
                    f"Attempted to send to non-connected websocket: {self.client_id}, state: {self.websocket.client_state}. Message type: {message.type}"
                )
        except (
            RuntimeError,
            WebSocketDisconnect,
        ) as e:  # Catch specific exceptions for closed sockets
            logger.warning(
                f"Attempted to send JSON model to {self.client_id} but socket was already closing or closed: {e}"
            )
        except Exception as e:  # Catch other potential errors
            logger.error(f"Error sending JSON model to {self.client_id}: {e}")

    async def close(self, code: int = 1000, reason: Optional[str] = None):
        closed_by_this_call = False
        try:
            if self.websocket.client_state == WebSocketState.CONNECTED:
                await self.websocket.close(code=code, reason=reason)
                closed_by_this_call = True
                logger.debug(
                    f"WebSocket {self.client_id} closed by close() method call."
                )
        except Exception as e:
            logger.warning(
                f"Exception during explicit close for {self.client_id}: {e}. State: {self.websocket.client_state}"
            )
        finally:
            # Always ensure the manager removes the connection, even if already closed or error during close
            self.manager.disconnect(self.client_id)
            if closed_by_this_call:
                logger.info(
                    f"ActiveWebSocketConnection {self.client_id} gracefully closed and disconnected."
                )
            else:
                logger.info(
                    f"ActiveWebSocketConnection {self.client_id} ensured disconnected by manager (was potentially already closed or error on close)."
                )

    async def handle_incoming_message(self, data_raw: Any):
        """Handles incoming messages, parsing, authentication, and command dispatch."""
        try:
            if isinstance(data_raw, str):
                data = json.loads(data_raw)
            elif isinstance(data_raw, bytes):  # Handle bytes if necessary
                data = json.loads(data_raw.decode("utf-8"))
            else:  # Assuming it's already a dict (e.g. from websocket.receive_json())
                data = data_raw

            # Check for specific malformed messages that only contain 'data' and 'timestamp'
            if (
                isinstance(data, dict)
                and "type" not in data
                and "data" in data
                and "timestamp" in data
                and len(data) == 2
            ):
                logger.debug(
                    f"Ignoring malformed client message (missing 'type', contains only 'data' and 'timestamp'): {data}"
                )
                return

            # Handle the message in the format the client sends
            if data.get("type") == "ping":
                await self.send_json_model(
                    WebSocketMessage(
                        type=WebSocketMessageTypeEnum.PONG,
                        data={"timestamp": datetime.utcnow().isoformat()},
                    )
                )
                return
            elif data.get("type") == "pong":  # Handle incoming PONG from client
                logger.debug(f"Received PONG from client {self.client_id}")
                self.last_pong_received = time.time()  # Update last PONG received time
                return

            # All messages from the client should be in the format {type, data}
            # For PONG messages, 'data' field is optional
            if "type" not in data:
                logger.warning(
                    f"Invalid message format from client {self.client_id}: Missing 'type' field. Raw data: {data_raw}"
                )
                await self.send_json_model(
                    WebSocketMessage(
                        type=WebSocketMessageTypeEnum.ERROR_NOTIFICATION,
                        data={
                            "error_code": "INVALID_FORMAT",
                            "message": "Invalid message format. Expected 'type' field.",
                        },
                    )
                )
                return

            # For PONG messages, 'data' field is optional
            if data.get("type") != WebSocketMessageTypeEnum.PONG and "data" not in data:
                logger.warning(
                    f"Invalid message format from client {self.client_id}: Missing 'data' field for message type {data.get('type')}. Raw data: {data_raw}"
                )
                await self.send_json_model(
                    WebSocketMessage(
                        type=WebSocketMessageTypeEnum.ERROR_NOTIFICATION,
                        data={
                            "error_code": "INVALID_FORMAT",
                            "message": "Invalid message format. Expected 'data' field for this message type.",
                        },
                    )
                )
                return
        except json.JSONDecodeError as e:
            logger.error(
                f"Failed to decode JSON message from {self.client_id}: {e}. Raw data: {data_raw}"
            )
            await self.send_json_model(
                WebSocketMessage(
                    type=WebSocketMessageTypeEnum.ERROR_NOTIFICATION,
                    data=ErrorNotification(
                        code="INVALID_JSON", message="Invalid JSON format."
                    ),
                )
            )
            return
        except Exception as e:
            logger.error(
                f"Error processing incoming message from {self.client_id}: {e}. Raw data: {data_raw}",
                exc_info=True,
            )
            await self.send_json_model(
                WebSocketMessage(
                    type=WebSocketMessageTypeEnum.ERROR_NOTIFICATION,
                    data=ErrorNotification(
                        code="MESSAGE_PROCESSING_ERROR",
                        message="Could not process message.",
                    ),
                )
            )
            return

        logger.debug(f"Parsed message from {self.client_id}: {data}")

        try:
            message = WebSocketMessage(**data)
        except Exception as e:  # Pydantic validation error or other
            logger.warning(
                f"Invalid WebSocketMessage structure from {self.client_id}: {e}. Raw data: {data_raw}"
            )
            await self.send_json_model(
                WebSocketMessage(
                    type=WebSocketMessageTypeEnum.ERROR_NOTIFICATION,
                    data=ErrorNotification(
                        code="INVALID_MESSAGE_STRUCTURE",
                        message=f"Invalid message structure: {str(e)}",
                    ),
                )
            )
            return

        

        # Handle other message types (subscriptions, commands, etc.)
        if message.type == WebSocketMessageTypeEnum.SUBSCRIBE:
            topic = (
                message.data.get("topic") if isinstance(message.data, dict) else None
            )
            if topic and isinstance(topic, str):
                self.subscriptions.add(topic)
                logger.info(
                    f"Client {self.client_id} subscribed to {topic}. Current subscriptions: {self.subscriptions}"
                )
                await self.send_json_model(
                    WebSocketMessage(
                        type=WebSocketMessageTypeEnum.GENERAL_NOTIFICATION,
                        data=GeneralNotification(
                            message_type="subscription_update",
                            message=f"Subscribed to {topic}",
                        ),
                    )
                )
            else:
                await self.send_json_model(
                    WebSocketMessage(
                        type=WebSocketMessageTypeEnum.ERROR_NOTIFICATION,
                        data=ErrorNotification(
                            code="INVALID_SUBSCRIPTION_TOPIC",
                            message="Invalid or missing topic for subscription.",
                        ),
                    )
                )

        elif message.type == WebSocketMessageTypeEnum.UNSUBSCRIBE:
            topic = (
                message.data.get("topic") if isinstance(message.data, dict) else None
            )
            if topic and isinstance(topic, str) and topic in self.subscriptions:
                self.subscriptions.remove(topic)
                logger.info(
                    f"Client {self.client_id} unsubscribed from {topic}. Current subscriptions: {self.subscriptions}"
                )
                await self.send_json_model(
                    WebSocketMessage(
                        type=WebSocketMessageTypeEnum.GENERAL_NOTIFICATION,
                        data=GeneralNotification(
                            message_type="subscription_update",
                            message=f"Unsubscribed from {topic}",
                        ),
                    )
                )
            else:
                await self.send_json_model(
                    WebSocketMessage(
                        type=WebSocketMessageTypeEnum.ERROR_NOTIFICATION,
                        data=ErrorNotification(
                            code="INVALID_UNSUBSCRIPTION_TOPIC",
                            message="Invalid, missing, or not subscribed topic for unsubscription.",
                        ),
                    )
                )

        elif message.type == WebSocketMessageTypeEnum.PING:
            await self.send_json_model(
                WebSocketMessage(
                    type=WebSocketMessageTypeEnum.PONG,
                    data={"timestamp": datetime.utcnow().isoformat()},
                )
            )

        elif message.type == WebSocketMessageTypeEnum.ERROR_NOTIFICATION:
            logger.info(
                f"Client {self.client_id} sent an ERROR_NOTIFICATION: {message.data}"
            )
            # Do not re-send an error notification back to the client for an incoming error notification.
            # Just log it and acknowledge.

        elif message.type == WebSocketMessageTypeEnum.AUTHENTICATE:
            new_token = message.data.get("token") if isinstance(message.data, dict) else None
            if new_token:
                try:
                    user_data = await self.manager._verify_firebase_token(new_token)
                    if user_data:
                        self.token = new_token
                        # Assuming token_expiry can be derived from user_data or is handled by Firebase SDK
                        # For now, we'll just update user_info and log success
                        self.user_info = user_data
                        # Firebase ID tokens are typically valid for 1 hour (3600 seconds)
                        # We can set token_expiry based on 'exp' claim if available, or a default
                        self.token_expiry = time.time() + 3600 # Default to 1 hour from now
                        logger.info(f"Client {self.client_id} successfully re-authenticated.")
                        await self.send_json_model(
                            WebSocketMessage(
                                type=WebSocketMessageTypeEnum.AUTH_SUCCESS,
                                data={"message": "Authentication successful."},
                            )
                        )
                    else:
                        logger.warning(f"Client {self.client_id} authentication failed: Invalid token.")
                        await self.send_json_model(
                            WebSocketMessage(
                                type=WebSocketMessageTypeEnum.AUTH_FAILURE,
                                data=ErrorNotification(
                                    code="AUTH_FAILED", message="Invalid token."
                                ),
                            )
                        )
                except Exception as e:
                    logger.error(f"Error during re-authentication for client {self.client_id}: {e}")
                    await self.send_json_model(
                        WebSocketMessage(
                            type=WebSocketMessageTypeEnum.AUTH_FAILURE,
                            data=ErrorNotification(
                                code="AUTH_ERROR", message=f"Authentication error: {str(e)}"
                            ),
                        )
                    )
            else:
                logger.warning(f"Client {self.client_id} sent AUTHENTICATE message without a token.")
                await self.send_json_model(
                    WebSocketMessage(
                        type=WebSocketMessageTypeEnum.AUTH_FAILURE,
                        data=ErrorNotification(
                            code="MISSING_TOKEN", message="Authentication message missing token."
                        ),
                    )
                )

        elif message.type == WebSocketMessageTypeEnum.REFRESH_FEED:  # Handle the new refresh_feed event type
            if isinstance(message.data, RefreshFeedData):
                feed_id = message.data.feed_id
                logger.info(
                    f"Client {self.client_id} requested refresh for feed: {feed_id}"
                )
                try:
                    # Get the FeedManager instance
                    feed_manager = get_feed_manager()
                    # Call the refresh_feed method on the FeedManager
                    await feed_manager.refresh_feed(feed_id)
                    logger.info(f"Refresh process initiated for feed {feed_id}.")
                    # Send a confirmation back to the client
                    await self.send_json_model(
                        WebSocketMessage(
                            type=WebSocketMessageTypeEnum.GENERAL_NOTIFICATION,
                            data=GeneralNotification(
                                message_type="feed_refresh_initiated",
                                message=f"Refresh initiated for feed {feed_id}.",
                            ),
                        )
                    )
                except (FeedNotFoundError, FeedOperationError) as e:
                    logger.warning(
                        f"Failed to refresh feed {feed_id} as requested by {self.client_id}: {e}"
                    )
                    await self.send_json_model(
                        WebSocketMessage(
                            type=WebSocketMessageTypeEnum.ERROR_NOTIFICATION,
                            data=ErrorNotification(
                                code="FEED_REFRESH_FAILED",
                                message=f"Failed to refresh feed {feed_id}: {e}",
                            ),
                        )
                    )
                except RuntimeError as e:
                    logger.error(
                        f"Failed to get FeedManager to refresh feed {feed_id}: {e}",
                        exc_info=True,
                    )
                    await self.send_json_model(
                        WebSocketMessage(
                            type=WebSocketMessageTypeEnum.ERROR_NOTIFICATION,
                            data=ErrorNotification(
                                code="INTERNAL_ERROR",
                                message="Server error attempting to refresh feed.",
                            ),
                        )
                    )
            else:
                await self.send_json_model(
                    WebSocketMessage(
                        type=WebSocketMessageTypeEnum.ERROR_NOTIFICATION,
                        data=ErrorNotification(
                            code="INVALID_REFRESH_REQUEST",
                            message="Invalid or missing feed_id for refresh.",
                        ),
                    )
                )
        elif message.type == WebSocketMessageTypeEnum.GET_INITIAL_FEED_STATUSES:
            logger.info(f"Client {self.client_id} requested initial feed statuses.")
            try:
                feed_manager = get_feed_manager()
                statuses = await feed_manager.get_all_statuses()
                await self.send_json_model(
                    WebSocketMessage(
                        type=WebSocketMessageTypeEnum.INITIAL_FEED_STATUSES,
                        data={"feeds": [status.model_dump() for status in statuses]},
                    )
                )
            except Exception as e:
                logger.error(f"Error getting initial feed statuses: {e}", exc_info=True)
                await self.send_json_model(
                    WebSocketMessage(
                        type=WebSocketMessageTypeEnum.ERROR_NOTIFICATION,
                        data=ErrorNotification(
                            code="INTERNAL_ERROR",
                            message="Could not retrieve initial feed statuses.",
                        ),
                    )
                )
        elif message.type == WebSocketMessageTypeEnum.SUBSCRIBE_TO_FEED:
            feed_id = message.data.get("feed_id") if isinstance(message.data, dict) else None
            if feed_id:
                self.subscriptions.add(feed_id)
                logger.info(f"Client {self.client_id} subscribed to feed {feed_id}")
                await self.send_json_model(
                    WebSocketMessage(
                        type=WebSocketMessageTypeEnum.GENERAL_NOTIFICATION,
                        data=GeneralNotification(
                            message_type="subscription_update",
                            message=f"Subscribed to feed {feed_id}",
                        ),
                    )
                )
            else:
                await self.send_json_model(
                    WebSocketMessage(
                        type=WebSocketMessageTypeEnum.ERROR_NOTIFICATION,
                        data=ErrorNotification(
                            code="INVALID_SUBSCRIPTION_REQUEST",
                            message="Invalid or missing feed_id for subscription.",
                        ),
                    )
                )

        else:
            logger.warning(
                f"Unhandled message type from {self.client_id}: {message.type}. Data: {message.data}"
            )
            await self.send_json_model(
                WebSocketMessage(
                    type=WebSocketMessageTypeEnum.ERROR_NOTIFICATION,
                    data=ErrorNotification(
                        code="UNHANDLED_MESSAGE_TYPE",
                        message=f"Message type '{message.type}' not handled.",
                    ),
                )
            )

    async def listen_for_messages(self):
        try:
            while True:
                data_raw = await self.websocket.receive_text()
                await self.handle_incoming_message(data_raw)
        except WebSocketDisconnect as e:
            logger.info(
                f"Client {self.client_id} disconnected. Code: {e.code}, Reason: {e.reason}"
            )
            self.manager.disconnect(self.client_id)
        except RuntimeError as e:
            # This can happen if the connection is closed abruptly before the listener loop starts
            logger.warning(
                f"RuntimeError in WebSocket loop for client {self.client_id}: {e}. "
                "This may indicate a client-side race condition or abrupt disconnection."
            )
            self.manager.disconnect(self.client_id)
        except Exception as e:
            logger.error(
                f"Unexpected error in WebSocket loop for client {self.client_id}: {e}",
                exc_info=True,
            )
            # Attempt to close the connection gracefully from server-side if an error occurs
            if (
                self.websocket.client_state == WebSocketState.CONNECTED
            ):
                error_payload = ErrorNotification(
                    code="UNEXPECTED_SERVER_ERROR", message=str(e)
                )
                ws_msg = WebSocketMessage(
                    type=WebSocketMessageTypeEnum.ERROR_NOTIFICATION, data=error_payload
                )
                try:
                    await self.send_json_model(ws_msg)
                except Exception as send_err:
                    logger.error(
                        f"Failed to send error to client {self.client_id} before closing: {send_err}"
                    )
                try:
                    await self.close(
                        code=1011, reason=f"Server error: {str(e)[:100]}"
                    )  # Reason has length limit
                except Exception as close_err:
                    logger.error(
                        f"Error trying to close connection for {self.client_id} after exception: {close_err}"
                    )
            # Ensure cleanup even if close fails
            self.manager.disconnect(self.client_id)
        finally:
            logger.info(f"WebSocket connection for client {self.client_id} is ending.")


class ConnectionManager:
    """Manages active WebSocket connections.""" # Add max_connections to __init__

    def __init__(self, max_connections: int = 1000, token_refresh_interval: int = 300): # 5 minutes
        self.active_connections: Dict[str, ActiveWebSocketConnection] = {}
        self.ping_interval_seconds: int = 15  # Send ping every 15 seconds
        self.ping_task: Optional[asyncio.Task] = None
        self.max_connections = max_connections  # Set the maximum number of connections
        self.token_refresh_interval = token_refresh_interval # Interval to check for token refresh
        logger.info(f"ConnectionManager initialized with max_connections={self.max_connections}, token_refresh_interval={self.token_refresh_interval}.")

    async def connect(self, websocket: WebSocket, client_id: str, user_data: Dict[str, Any]):
        if len(self.active_connections) >= self.max_connections:
            logger.warning(f"Connection limit ({self.max_connections}) exceeded. Rejecting new connection from {client_id}.")
            raise ConnectionLimitExceeded(detail="Maximum number of connections reached.")

        # The websocket is now accepted in the endpoint handler before this method is called.
        # logger.debug(f"Client {client_id}: WebSocket connection accepted.") # This log is now in the handler
        connection = ActiveWebSocketConnection(websocket, client_id, self, user_data)
        self.active_connections[client_id] = connection
        logger.debug(f"Client {client_id}: Added to active connections.")
        logger.info(f"Client {client_id} connected. Total active connections: {len(self.active_connections)}.")
        if not self.ping_task or self.ping_task.done():
            await self.start_ping_task()

    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            logger.info(f"Client {client_id} disconnected. Total active connections: {len(self.active_connections)}")
        if not self.active_connections and self.ping_task:
            self.ping_task.cancel()
            self.ping_task = None
            logger.info("All clients disconnected, ping task stopped.")

    async def disconnect_all(self):
        """Disconnects all active WebSocket connections."""
        logger.info(f"Disconnecting all {len(self.active_connections)} active WebSocket connections.")
        # Iterate over a copy of keys to avoid RuntimeError: dictionary changed size during iteration
        for client_id in list(self.active_connections.keys()):
            connection = self.active_connections.get(client_id)
            if connection:
                try:
                    if connection.websocket.client_state == WebSocketState.CONNECTED:
                        await connection.websocket.close(code=1000, reason="Server shutting down")
                except Exception as e:
                    logger.error(f"Error closing websocket for {client_id} during shutdown: {e}")
                finally:
                    self.disconnect(client_id) # Ensure cleanup

        if self.ping_task:
            self.ping_task.cancel()
            try:
                await self.ping_task
            except asyncio.CancelledError:
                logger.info("ConnectionManager ping task stopped during disconnect_all.")
            self.ping_task = None
        logger.info("All WebSocket connections disconnected.")

    async def broadcast(self, message: WebSocketMessage, exclude_client_id: Optional[str] = None):
        """Broadcasts a message to all active connections, optionally excluding one."""
        for client_id, connection in list(self.active_connections.items()):
            if client_id == exclude_client_id:
                continue
            try:
                await connection.send_json_model(message)
            except Exception as e:
                logger.error(f"Error broadcasting message to {client_id}: {e}")
                self.disconnect(client_id) # Disconnect problematic client

    async def broadcast_to_topic(self, message: WebSocketMessage, topic: str):
        """Broadcasts a message to all clients subscribed to a specific topic."""
        for client_id, connection in list(self.active_connections.items()):
            if topic in connection.subscriptions:
                try:
                    await connection.send_json_model(message)
                except Exception as e:
                    logger.error(f"Error broadcasting message to subscribed client {client_id} for topic {topic}: {e}")
                    self.disconnect(client_id) # Disconnect problematic client

    async def send_to_client(self, client_id: str, message: WebSocketMessage):
        """Sends a message to a specific client."""
        connection = self.active_connections.get(client_id)
        if connection:
            try:
                await connection.send_json_model(message)
            except Exception as e:
                logger.error(f"Error sending message to client {client_id}: {e}")
                self.disconnect(client_id)
        else:
            logger.warning(f"Attempted to send message to non-existent client: {client_id}")

    async def _verify_firebase_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Verifies a Firebase ID token and returns user data if valid."""
        try:
            # Import here to avoid circular dependency at top level
            from app.dependencies import verify_firebase_token
            user_data = await verify_firebase_token(token)
            return user_data
        except Exception as e:
            logger.warning(f"Firebase token verification failed: {e}")
            return None

    async def start_ping_task(self):
        if self.ping_task and not self.ping_task.done():
            self.ping_task.cancel()
        self.ping_task = asyncio.create_task(self._ping_clients_periodically())
        logger.info("ConnectionManager ping task started.")

    async def stop_ping_task(self):
        if self.ping_task:
            self.ping_task.cancel()
            try:
                await self.ping_task
            except asyncio.CancelledError:
                logger.info("ConnectionManager ping task stopped.")
            self.ping_task = None

    async def _ping_clients_periodically(self):
        while True:
            await asyncio.sleep(self.ping_interval_seconds)
            await self._ping_clients()

    async def _ping_clients(self):
        logger.debug(f"Sending PING to {len(self.active_connections)} clients.")
        clients_to_remove = []
        for client_id, connection in list(self.active_connections.items()):
            if connection.websocket.client_state == WebSocketState.CONNECTED:
                try:
                    await connection.send_json_model(
                        WebSocketMessage(
                            type=WebSocketMessageTypeEnum.PING,
                            data={"timestamp": datetime.utcnow().isoformat()},
                        )
                    )
                    # Update last_ping_sent for server-initiated pings
                    connection.last_ping_sent = time.time()
                except Exception as e:
                    logger.warning(f"Failed to send PING to client {client_id}: {e}")
                    clients_to_remove.append(client_id)
            else:
                logger.debug(
                    f"Client {client_id} not connected, marking for removal during ping."
                )
                clients_to_remove.append(client_id)

        # Check for clients that haven't responded to pings or need token refresh
        current_time = time.time()
        for client_id, connection in list(self.active_connections.items()):
            # Use last_pong_received for timeout detection
            if current_time - connection.last_pong_received > connection.ping_timeout:
                logger.warning(
                    f"Client {client_id} timed out (no PONG response). Disconnecting."
                )
                clients_to_remove.append(client_id)
            # Check if token needs refreshing
            if connection.token_expiry and current_time >= (connection.token_expiry - self.token_refresh_interval):
                logger.info(f"Client {client_id} token expiring soon. Requesting refresh.")
                await connection.send_json_model(
                    WebSocketMessage(
                        type=WebSocketMessageTypeEnum.TOKEN_REFRESH_REQUEST,
                        data={"message": "Your token is expiring soon. Please refresh."},
                    )
                )

        for client_id in clients_to_remove:
            self.disconnect(client_id)

