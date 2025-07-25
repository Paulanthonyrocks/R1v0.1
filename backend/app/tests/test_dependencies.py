import pytest
from fastapi import WebSocket
from unittest.mock import AsyncMock, MagicMock
from app.dependencies import get_token_from_query

@pytest.mark.asyncio
async def test_get_token_from_query_clean_token():
    """Test that get_token_from_query returns a clean token when no prefix is present."""
    mock_websocket = MagicMock(spec=WebSocket)
    mock_websocket.query_params = {"token": "your_firebase_token_here"}
    token = await get_token_from_query(mock_websocket)
    assert token == "your_firebase_token_here"

@pytest.mark.asyncio
async def test_get_token_from_query_with_prefix():
    """Test that get_token_from_query strips the '?token=' prefix."""
    mock_websocket = MagicMock(spec=WebSocket)
    mock_websocket.query_params = {"token": "?token=your_firebase_token_here"}
    token = await get_token_from_query(mock_websocket)
    assert token == "your_firebase_token_here"

@pytest.mark.asyncio
async def test_get_token_from_query_no_token():
    """Test that get_token_from_query returns None when no token is present."""
    mock_websocket = MagicMock(spec=WebSocket)
    mock_websocket.query_params = {}
    token = await get_token_from_query(mock_websocket)
    assert token is None
