# /content/drive/MyDrive/R1v0.1/backend/app/websocket/connection_manager.py

import asyncio
import logging
from typing import List, Dict, Any, Set, Optional
from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
import json
from datetime import datetime
import firebase_admin # For auth checking in WS
from firebase_admin import auth

from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum, ErrorNotification, GeneralNotification # Import new models
# from app.dependencies import get_current_active_user_ws # We'll define a similar function here or call directly

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
        """Sends a Pydantic model as JSON over WebSocket."""
        try:
            if self.websocket.client_state == WebSocketState.CONNECTED:
                await self.websocket.send_json(message.model_dump(mode='json'))
            else:
                logger.warning(f"Attempted to send to non-connected websocket: {self.client_id}, state: {self.websocket.client_state}")
        except Exception as e: # Catch potential errors if socket is already closed
            logger.error(f"Error sending JSON model to {self.client_id}: {e}")
            # Should trigger disconnect logic if this fails repeatedly

    async def close(self, code: int = 1000, reason: Optional[str] = None):
        closed_by_this_call = False
        try:
            if self.websocket.client_state == WebSocketState.CONNECTED:
                await self.websocket.close(code=code, reason=reason)
                closed_by_this_call = True
                logger.debug(f"WebSocket {self.client_id} closed by close() method call.")
        except Exception as e:
            logger.warning(f"Exception during explicit close for {self.client_id}: {e}. State: {self.websocket.client_state}")
        finally:
            # Always ensure the manager removes the connection, even if already closed or error during close
            self.manager.disconnect(self.client_id)
            if closed_by_this_call:
                 logger.info(f"ActiveWebSocketConnection {self.client_id} gracefully closed and disconnected.")
            else:
                 logger.info(f"ActiveWebSocketConnection {self.client_id} ensured disconnected by manager (was potentially already closed or error on close).")

    async def handle_incoming_message(self, data_raw: Any):
        """Handles incoming messages, parsing, authentication, and command dispatch."""
        try:
            if isinstance(data_raw, str):
                data = json.loads(data_raw)
            elif isinstance(data_raw, bytes): # Handle bytes if necessary
                 data = json.loads(data_raw.decode('utf-8'))
            else: # Assuming it's already a dict (e.g. from websocket.receive_json())
                data = data_raw
        except json.JSONDecodeError:
            logger.error(f"Failed to decode JSON message from {self.client_id}: {data_raw}")
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
            logger.error(f"Error processing incoming message from {self.client_id}: {e}", exc_info=True)
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
        except Exception as e: # Pydantic validation error or other
            logger.warning(f"Invalid WebSocketMessage structure from {self.client_id}: {data}. Error: {e}")
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
            token = message.payload.get("token") if isinstance(message.payload, dict) else None
            if token:
                user = await self.manager._verify_firebase_token(token)
                if user:
                    self.user_info = user
                    self.auth_pending = False
                    logger.info(f"Client {self.client_id} authenticated successfully. UID: {user.get('uid')}")
                    await self.send_json_model(
                        WebSocketMessage(
                            event_type=WebSocketMessageTypeEnum.GENERAL_NOTIFICATION,
                            payload=GeneralNotification(
                                message_type="auth_success",
                                message="Authentication successful."
                            )
                        )
                    )
                else:
                    logger.warning(f"Client {self.client_id} authentication failed.")
                    await self.send_json_model(
                        WebSocketMessage(
                            event_type=WebSocketMessageTypeEnum.ERROR,
                            payload=ErrorNotification(
                                code="AUTH_FAILED",
                                message="Authentication failed. Invalid token."
                            )
                        )
                    )
                    # Optionally, close connection after failed auth attempt
                    # await self.close(code=4001, reason="Authentication Failed")
            else:
                await self.send_json_model(
                    WebSocketMessage(
                        event_type=WebSocketMessageTypeEnum.ERROR,
                        payload=ErrorNotification(
                            code="AUTH_TOKEN_MISSING",
                            message="Authentication token missing."
                        )
                    )
                )
            return

        # All further messages require authentication
        if self.auth_pending and not self.user_info:
            logger.warning(f"Client {self.client_id} attempted action before authentication. Message: {message.event_type}")
            await self.send_json_model(
                WebSocketMessage(
                    event_type=WebSocketMessageTypeEnum.ERROR,
                    payload=ErrorNotification(
                        code="AUTH_REQUIRED",
                        message="Authentication required before sending other messages."
                    )
                )
            )
            return

        # Handle other message types (subscriptions, commands, etc.)
        if message.event_type == WebSocketMessageTypeEnum.SUBSCRIBE:
            topic = message.payload.get("topic") if isinstance(message.payload, dict) else None
            if topic and isinstance(topic, str):
                self.subscriptions.add(topic)
                logger.info(f"Client {self.client_id} subscribed to {topic}. Current subscriptions: {self.subscriptions}")
                await self.send_json_model(
                    WebSocketMessage(
                        event_type=WebSocketMessageTypeEnum.GENERAL_NOTIFICATION,
                        payload=GeneralNotification(
                            message_type="subscription_update",
                            message=f"Subscribed to {topic}"
                        )
                    )
                )
            else:
                 await self.send_json_model(
                    WebSocketMessage(
                        event_type=WebSocketMessageTypeEnum.ERROR,
                        payload=ErrorNotification(code="INVALID_SUBSCRIPTION_TOPIC", message="Invalid or missing topic for subscription.")
                    )
                )

        elif message.event_type == WebSocketMessageTypeEnum.UNSUBSCRIBE:
            topic = message.payload.get("topic") if isinstance(message.payload, dict) else None
            if topic and isinstance(topic, str) and topic in self.subscriptions:
                self.subscriptions.remove(topic)
                logger.info(f"Client {self.client_id} unsubscribed from {topic}. Current subscriptions: {self.subscriptions}")
                await self.send_json_model(
                    WebSocketMessage(
                        event_type=WebSocketMessageTypeEnum.GENERAL_NOTIFICATION,
                        payload=GeneralNotification(
                            message_type="subscription_update",
                            message=f"Unsubscribed from {topic}"
                        )
                    )
                )
            else:
                await self.send_json_model(
                    WebSocketMessage(
                        event_type=WebSocketMessageTypeEnum.ERROR,
                        payload=ErrorNotification(code="INVALID_UNSUBSCRIPTION_TOPIC", message="Invalid, missing, or not subscribed topic for unsubscription.")
                    )
                )
        
        elif message.event_type == WebSocketMessageTypeEnum.PING:
            await self.send_json_model(
                WebSocketMessage(
                    event_type=WebSocketMessageTypeEnum.PONG,
                    payload={"timestamp": datetime.utcnow().isoformat()}
                )
            )

        else:
            logger.warning(f"Unhandled message type from {self.client_id}: {message.event_type}")
            await self.send_json_model(
                 WebSocketMessage(
                    event_type=WebSocketMessageTypeEnum.ERROR,
                    payload=ErrorNotification(code="UNHANDLED_MESSAGE_TYPE", message=f"Message type '{message.event_type}' not handled.")
                )
            )

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
            logger.info(f"Client {client_id} connected. Total connections: {len(self.active_connections)}")
        except WebSocketDisconnect:
            logger.warning(f"Client {client_id} disconnected before connection could be fully established.")
            # Ensure no lingering connection object if accept fails
            if client_id in self.active_connections: # Should not happen if accept is first
                del self.active_connections[client_id]
            # Optionally call connection.close() if it has resources to clean up even without full connect
            # await connection.close() # This would trigger disconnect again, so be careful

    def disconnect(self, client_id: str):
        connection = self.active_connections.pop(client_id, None)
        if connection:
            logger.info(f"Client {client_id} removed from ConnectionManager. Remaining connections: {len(self.active_connections)}")
            # DO NOT call connection.close() here to avoid recursion if disconnect is called from connection.close()
        else:
            logger.debug(f"Attempted to disconnect non-existent or already removed client: {client_id}")

    async def _verify_firebase_token(self, token: str) -> Optional[Dict[str, Any]]:
        if not firebase_admin._DEFAULT_APP_NAME in firebase_admin._apps:
            logger.error("Firebase Admin SDK default app not initialized. Cannot authenticate WebSocket user.")
            return None
        try:
            decoded_token = auth.verify_id_token(token, check_revoked=True)
            return decoded_token
        except auth.RevokedIdTokenError:
            logger.warning("WebSocket Auth: Token has been revoked.")
        except auth.UserDisabledError:
            logger.warning("WebSocket Auth: User account is disabled.")
        except auth.InvalidIdTokenError as e:
            logger.warning(f"WebSocket Auth: Invalid ID token: {e}")
        except Exception as e:
            logger.error(f"WebSocket Auth: Error verifying Firebase ID token: {e}", exc_info=True)
        return None

    async def handle_incoming_message(self, client_id: str, data_raw: Any):
        # This method is called by the FastAPI endpoint
        connection = self.active_connections.get(client_id)
        if connection:
            await connection.handle_incoming_message(data_raw)
        else:
            logger.warning(f"Received message for unknown or disconnected client {client_id}. Ignoring.")

    async def broadcast_message_model(self, message: WebSocketMessage, specific_topic: Optional[str] = None):
        logger.debug(f"Broadcasting model (type: {message.event_type}, topic: {specific_topic or 'all'}) to {len(self.active_connections)} potential clients.")
        
        # Create a list of connections to iterate over, in case connections are modified during iteration
        connections_to_send_to = list(self.active_connections.values())

        for connection in connections_to_send_to:
            # Check if the connection is still valid/active before attempting to send
            if connection.client_id not in self.active_connections:
                logger.debug(f"Skipping broadcast to {connection.client_id} as it was disconnected during broadcast.")
                continue

            should_send = False
            if specific_topic:
                if specific_topic in connection.subscriptions:
                    should_send = True
            else: # Broadcast to all (potentially filtered by auth status)
                if not connection.auth_pending or message.event_type in [WebSocketMessageTypeEnum.GENERAL_NOTIFICATION, WebSocketMessageTypeEnum.ERROR, WebSocketMessageTypeEnum.PONG]:
                    should_send = True
            
            if should_send:
                if connection.websocket.client_state == WebSocketState.CONNECTED:
                    await connection.send_json_model(message)
                else:
                    logger.warning(f"Skipping broadcast to {connection.client_id}: WebSocket not connected. State: {connection.websocket.client_state}")
                    # Consider triggering disconnect if consistently not connected, though send_json_model might handle it
                    # or the main receive loop will catch disconnect.

    async def send_personal_message_model(self, client_id: str, message: WebSocketMessage):
        connection = self.active_connections.get(client_id)
        if connection:
            await connection.send_json_model(message)
            logger.debug(f"Sent personal model message (type: {message.event_type}) to client {client_id}")
        else:
            logger.warning(f"Attempted to send personal model to unknown or non-connected client: {client_id}")

    async def disconnect_all(self):
        logger.info(f"Initiating disconnect for all {len(self.active_connections)} active WebSocket connections...")
        # Iterate over a copy of client_ids for safe removal
        client_ids_to_disconnect = list(self.active_connections.keys())
        
        for client_id in client_ids_to_disconnect:
            connection = self.active_connections.get(client_id) # Get an up-to-date reference
            if connection:
                logger.debug(f"Requesting close for connection {client_id} during disconnect_all.")
                await connection.close(code=1001) # 1001: Going Away
                # connection.close() will call manager.disconnect(client_id)
            else:
                logger.debug(f"Connection {client_id} already removed before explicit close in disconnect_all.")
        
        if not self.active_connections:
            logger.info("All WebSocket connections have been closed and removed.")
        else:
            logger.warning(f"{len(self.active_connections)} connections still remain after disconnect_all. This might indicate an issue.")
            # Forcing removal if any linger due to unforeseen issues with close not triggering disconnect
            for client_id in list(self.active_connections.keys()):
                 logger.warning(f"Forcibly removing lingering connection: {client_id}")
                 self.disconnect(client_id)

    def get_connection_info(self, client_id: str) -> Optional[Dict[str, Any]]:
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
                 logger.error(f"Error sending personal json to {client_id}: {e}")
        else:
            logger.warning(f"Attempted to send personal json to unknown or non-connected client: {client_id}")

    async def broadcast_json(self, data: dict):
        disconnected_clients: List[str] = []
        for client_id, websocket in list(self.active_connections.items()):
            if websocket.client_state == WebSocketState.CONNECTED:
                try:
                    await websocket.send_json(data)
                except Exception as e:
                    logger.error(f"Error sending broadcast json to client {client_id}: {e}")
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