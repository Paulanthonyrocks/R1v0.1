from fastapi import Request
from app.websocket.connection_manager import ConnectionManager

async def get_connection_manager(request: Request) -> ConnectionManager:
    return request.app.state.connection_manager
