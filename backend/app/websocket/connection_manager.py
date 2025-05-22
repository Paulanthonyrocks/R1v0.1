# /content/drive/MyDrive/R1v0.1/backend/app/websocket/connection_manager.py

# import asyncio # F401: Unused import
import logging
from typing import List, Dict, Any, Set, Optional
from fastapi import WebSocket, WebSocketDisconnect, WebSocketState
import json
from datetime import datetime
import firebase_admin
from firebase_admin import auth

from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum, ErrorNotification, GeneralNotification
# from app.dependencies import get_current_active_user_ws # Placeholder

logger = logging.getLogger(__name__)


class ActiveWebSocketConnection:
    def __init__(self, websocket: WebSocket, client_id: str, manager: 'ConnectionManager'):
        self.websocket = websocket
        self.client_id = client_id
        self.manager = manager
        self.user_info: Optional[Dict[str, Any]] = None
        self.subscriptions: Set[str] = set()
        self.auth_pending: bool = True

    async def accept(self):
        await self.websocket.accept()

    async def send_text(self, text: str):
        await self.websocket.send_text(text)

    async def send_json_model(self, message: WebSocketMessage):
        """Sends a Pydantic model as JSON over WebSocket if connected."""
        try:
            if self.websocket.client_state == WebSocketState.CONNECTED:
                await self.websocket.send_json(message.model_dump(mode='json'))
            else:
                logger.warning(f"WS {self.client_id} not connected, state: {self.websocket.client_state}")
        except Exception as e:
            logger.error(f"Error sending JSON model to {self.client_id}: {e}")
            # Consider triggering disconnect logic here if send fails repeatedly

    async def close(self, code: int = 1000, reason: Optional[str] = None):
        """Closes the WebSocket connection and ensures manager cleanup."""
        closed_by_call = False
        current_state = self.websocket.client_state
        if current_state == WebSocketState.CONNECTED:
            try:
                await self.websocket.close(code=code, reason=reason)
                closed_by_call = True
                logger.debug(f"WebSocket {self.client_id} closed by close() call.")
            except Exception as e:
                logger.warning(f"Exception during explicit close for {self.client_id}: {e}. State: {current_state}")
        # Ensure manager cleanup regardless of close success or if already closed
        self.manager.disconnect(self.client_id) # This will pop it from active_connections
        if closed_by_call:
            logger.info(f"WS Connection {self.client_id} gracefully closed and disconnected.")
        elif current_state != WebSocketState.DISCONNECTED : # Log if not already disconnected by other means
            logger.info(f"WS Connection {self.client_id} ensured disconnected (was {current_state}).")


    async def handle_incoming_message(self, data_raw: Any):
        """Handles incoming messages: parsing, authentication, command dispatch."""
        try:
            if isinstance(data_raw, str):
                data = json.loads(data_raw)
            elif isinstance(data_raw, bytes):  # Handle bytes if necessary
                data = json.loads(data_raw.decode('utf-8'))
            # Assuming it's already a dict (e.g. from websocket.receive_json())
            else:
                data = data_raw
        except json.JSONDecodeError:
            logger.error(
                f"Failed to decode JSON message from {self.client_id}: {data_raw}")
            await self.send_json_model(
                WebSocketMessage(
                    event_type=WebSocketMessageTypeEnum.ERROR,
                    payload=ErrorNotification(
                        code="INVALID_JSON",
                        message="Invalid JSON format."
                    )
                )
            )
            return
        except Exception as e:
            logger.error(
                f"Error processing incoming message from {self.client_id}: {e}", exc_info=True)
            await self.send_json_model(
                WebSocketMessage(
                    event_type=WebSocketMessageTypeEnum.ERROR,
                    payload=ErrorNotification(
                        code="MESSAGE_PROCESSING_ERROR",
                        message="Could not process message."
                    )
                )
            )
            return

        logger.debug(f"Parsed message from {self.client_id}: {data}")

        try:
            message = WebSocketMessage(**data)
        except Exception as e:  # Pydantic validation error or other
            logger.warning(
                f"Invalid WebSocketMessage structure from {self.client_id}: {data}. Error: {e}")
            await self.send_json_model(
                WebSocketMessage(
                    event_type=WebSocketMessageTypeEnum.ERROR,
                    payload=ErrorNotification(
                        code="INVALID_MESSAGE_STRUCTURE",
                        message=f"Invalid message structure: {str(e)}"
                    )
                )
            )
            return

        if message.event_type == WebSocketMessageTypeEnum.AUTHENTICATE:
            token = message.payload.get("token") if isinstance(
                message.payload, dict) else None
            if token:
                user = await self.manager._verify_firebase_token(token)
                if user:
                    self.user_info = user
                    self.auth_pending = False
                    logger.info(f"Client {self.client_id} authenticated. UID: {user.get('uid')}")
                    await self.send_json_model(WebSocketMessage(
                        event_type=WebSocketMessageTypeEnum.AUTH_SUCCESS, # Changed to specific type
                        payload=GeneralNotification(message="Authentication successful.")
                    ))
                else:
                    logger.warning(f"Client {self.client_id} authentication failed.")
                    await self.send_json_model(WebSocketMessage(
                        event_type=WebSocketMessageTypeEnum.AUTH_FAILURE, # Changed to specific type
                        payload=ErrorNotification(code="AUTH_FAILED", message="Invalid token.")
                    ))
                    # Optionally close: await self.close(code=4001, reason="Auth Failed")
            else: # No token provided
                await self.send_json_model(WebSocketMessage(
                    event_type=WebSocketMessageTypeEnum.ERROR,
                    payload=ErrorNotification(code="AUTH_TOKEN_MISSING", message="Auth token missing.")
                ))
            return

        # Check authentication for subsequent messages
        if self.auth_pending and not self.user_info: # Should be self.user_info is None
            logger.warning(f"Client {self.client_id} action before auth: {message.event_type}")
            await self.send_json_model(WebSocketMessage(
                event_type=WebSocketMessageTypeEnum.ERROR,
                payload=ErrorNotification(code="AUTH_REQUIRED", message="Authentication required.")
            ))
            return

        # Command handling based on message type
        if message.event_type == WebSocketMessageTypeEnum.SUBSCRIBE:
            topic = message.payload.get("topic") if isinstance(message.payload, dict) else None
            if topic and isinstance(topic, str):
                self.subscriptions.add(topic)
                logger.info(f"Client {self.client_id} subscribed to {topic}. Subs: {self.subscriptions}")
                await self.send_json_model(WebSocketMessage(
                    event_type=WebSocketMessageTypeEnum.GENERAL_NOTIFICATION,
                    payload=GeneralNotification(message=f"Subscribed to {topic}")
                ))
            else:
                await self.send_json_model(WebSocketMessage(
                    event_type=WebSocketMessageTypeEnum.ERROR,
                    payload=ErrorNotification(code="INVALID_TOPIC", message="Invalid topic.")
                ))
        elif message.event_type == WebSocketMessageTypeEnum.UNSUBSCRIBE:
            topic = message.payload.get("topic") if isinstance(message.payload, dict) else None
            if topic and isinstance(topic, str) and topic in self.subscriptions:
                self.subscriptions.remove(topic)
                logger.info(f"Client {self.client_id} unsubscribed from {topic}. Subs: {self.subscriptions}")
                await self.send_json_model(WebSocketMessage(
                    event_type=WebSocketMessageTypeEnum.GENERAL_NOTIFICATION,
                    payload=GeneralNotification(message=f"Unsubscribed from {topic}")
                ))
            else:
                await self.send_json_model(WebSocketMessage(
                    event_type=WebSocketMessageTypeEnum.ERROR,
                    payload=ErrorNotification(code="INVALID_TOPIC", message="Topic not found or invalid.")
                ))
        elif message.event_type == WebSocketMessageTypeEnum.PING:
            await self.send_json_model(WebSocketMessage(
                event_type=WebSocketMessageTypeEnum.PONG,
                payload={"timestamp": datetime.utcnow().isoformat()}
            ))
        else:
            logger.warning(f"Unhandled message type from {self.client_id}: {message.event_type}")
            await self.send_json_model(WebSocketMessage(
                event_type=WebSocketMessageTypeEnum.ERROR,
                payload=ErrorNotification(code="UNHANDLED_TYPE", message=f"Type '{message.event_type}' not handled.")
            ))


class ConnectionManager:
    """Manages active WebSocket connections."""

    def __init__(self):
        self.active_connections: Dict[str, ActiveWebSocketConnection] = {}
        logger.info("ConnectionManager initialized.")

    async def connect(self, websocket: WebSocket, client_id: str):
        connection = ActiveWebSocketConnection(websocket, client_id, self)
        try:
            await connection.accept()
            self.active_connections[client_id] = connection
            logger.info(f"Client {client_id} connected. Total: {len(self.active_connections)}")
        except WebSocketDisconnect: # This exception occurs if client disconnects during accept
            logger.warning(f"Client {client_id} disconnected before full connection establishment.")
            # No need to explicitly delete from active_connections, as it wasn't added or will be handled by disconnect
        except Exception as e:
            logger.error(f"Error during connect for {client_id}: {e}", exc_info=True)
            # Ensure cleanup if accept fails partway
            if client_id in self.active_connections: del self.active_connections[client_id]


    def disconnect(self, client_id: str):
        """Removes a connection from the manager. Called by ActiveWebSocketConnection.close() or if error."""
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            logger.info(f"Client {client_id} removed from ConnectionManager. Remaining: {len(self.active_connections)}")
        else:
            logger.debug(f"Attempted to disconnect already removed/unknown client: {client_id}")


    async def _verify_firebase_token(self, token: str) -> Optional[Dict[str, Any]]:
        # E713: Test for membership should be 'not in'
        if firebase_admin._DEFAULT_APP_NAME not in firebase_admin._apps:
            logger.error("Firebase Admin SDK default app not initialized for WebSocket auth.")
            return None
        try:
            decoded_token = auth.verify_id_token(token, check_revoked=True)
            return decoded_token # Contains 'uid' and other user info
        except auth.RevokedIdTokenError: logger.warning("WS Auth: Token revoked.")
        except auth.UserDisabledError: logger.warning("WS Auth: User account disabled.")
        except auth.InvalidIdTokenError as e: logger.warning(f"WS Auth: Invalid ID token: {e}")
        except Exception as e: logger.error(f"WS Auth: Error verifying Firebase ID token: {e}", exc_info=True)
        return None

    async def handle_incoming_message(self, client_id: str, data_raw: Any):
        """Route incoming message to the appropriate ActiveWebSocketConnection instance."""
        connection = self.active_connections.get(client_id)
        if connection:
            await connection.handle_incoming_message(data_raw)
        else:
            logger.warning(f"Message for unknown/disconnected client {client_id}. Ignoring.")

    async def broadcast_message_model(self, message: WebSocketMessage, specific_topic: Optional[str] = None):
        """Broadcasts a Pydantic model to relevant, connected, and authenticated clients."""
        log_msg = f"Broadcasting model (type: {message.event_type}, topic: {specific_topic or 'all'})"
        logger.debug(f"{log_msg} to {len(self.active_connections)} potential clients.")

        # Iterate over a copy of connections for safe modification if a send fails and leads to disconnect
        connections_to_send_to = list(self.active_connections.values())

        for conn in connections_to_send_to:
            if conn.client_id not in self.active_connections: # Check if still active
                logger.debug(f"Skipping broadcast to {conn.client_id}: disconnected during broadcast.")
                continue

            # Determine if this connection should receive the message
            should_send = False
            if specific_topic: # Topic-specific message
                if specific_topic in conn.subscriptions: should_send = True
            else: # General broadcast (not topic-specific)
                # Send general notifications/errors even if auth is pending for things like auth_failure
                if not conn.auth_pending or message.event_type in [
                    WebSocketMessageTypeEnum.GENERAL_NOTIFICATION,
                    WebSocketMessageTypeEnum.ERROR_NOTIFICATION, # Allow error broadcasts
                    WebSocketMessageTypeEnum.PONG, # Allow pongs
                    WebSocketMessageTypeEnum.AUTH_FAILURE, # Allow auth failure messages
                    # Consider if AUTH_SUCCESS should be broadcast or only personal
                ]:
                    should_send = True

            if should_send:
                await conn.send_json_model(message) # send_json_model handles check for connected state


    async def send_personal_message_model(self, client_id: str, message: WebSocketMessage):
        """Sends a Pydantic model as JSON to a specific client."""
        connection = self.active_connections.get(client_id)
        if connection:
            await connection.send_json_model(message)
            logger.debug(f"Sent personal model (type: {message.event_type}) to client {client_id}")
        else:
            logger.warning(f"Attempted personal model to unknown/disconnected client: {client_id}")

    async def disconnect_all(self):
        """Disconnects all active WebSocket connections."""
        logger.info(f"Initiating disconnect for all {len(self.active_connections)} active WebSocket connections...")
        # Iterate over a copy of client_ids for safe removal from the dictionary
        client_ids_to_disconnect = list(self.active_connections.keys())
        for client_id in client_ids_to_disconnect:
            connection = self.active_connections.get(client_id)
            if connection:
                logger.debug(f"Requesting close for connection {client_id} during disconnect_all.")
                await connection.close(code=1001, reason="Server shutting down") # 1001: Going Away
        if not self.active_connections: # Check if all were removed by connection.close() -> manager.disconnect()
            logger.info("All WebSocket connections have been closed and removed from manager.")
        else: # Should ideally be empty
            logger.warning(f"{len(self.active_connections)} connections linger post disconnect_all. Forcing removal.")
            for client_id in list(self.active_connections.keys()): # Force remove any stragglers
                logger.warning(f"Forcibly removing lingering connection: {client_id}")
                self.disconnect(client_id)


    def get_connection_info(self, client_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves information about a specific connection."""
        connection = self.active_connections.get(client_id)
        if connection:
            return {
                "client_id": client_id,
                "user_info": connection.user_info,
                "subscriptions": list(connection.subscriptions),
                "authenticated": connection.user_info is not None,
                "auth_pending": connection.auth_pending,
                "websocket_state": str(connection.websocket.client_state)
            }
        return None

    def get_all_connections_info(self) -> List[Dict[str, Any]]:
        return [self.get_connection_info(cid) for cid in self.active_connections.keys() if self.get_connection_info(cid) is not None]

# Old JSON methods (can be deprecated or removed if all sending uses models)
    async def send_personal_json(self, client_id: str, data: dict):
        websocket = self.active_connections.get(client_id)
        if websocket and websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.send_json(data)
            except Exception as e:
                logger.error(
                    f"Error sending personal json to {client_id}: {e}")
        else:
            logger.warning(
                f"Attempted to send personal json to unknown or non-connected client: {client_id}")

    async def broadcast_json(self, data: dict):
        disconnected_clients: List[str] = []
        for client_id, websocket in list(self.active_connections.items()):
            if websocket.client_state == WebSocketState.CONNECTED:
                try:
                    await websocket.send_json(data)
                except Exception as e:
                    logger.error(
                        f"Error sending broadcast json to client {client_id}: {e}")
                    disconnected_clients.append(client_id)
            else:
                disconnected_clients.append(client_id)
        for client_id in disconnected_clients:
            self.disconnect(client_id)

# Dependency for WebSocket authentication (placeholder)
# This would need to be similar to backend/app/dependencies.py:get_current_active_user
# but adapted for WebSocket (e.g., token passed in message)
# async def get_current_active_user_ws(token: str = Depends(lambda x: x)) -> Optional[Dict[str, Any]]:
#     if not firebase_admin._DEFAULT_APP_NAME in firebase_admin._apps:
#         logger.error("Firebase Admin SDK not initialized for WebSocket auth.")
#         return None
#     try:
#         decoded_token = auth.verify_id_token(token)
#         return decoded_token
#     except Exception as e:
#         logger.error(f"WebSocket Auth: Invalid Firebase ID token: {e}")
#         return None
