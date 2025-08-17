# backend/app/services/external_api_client.py
import httpx
import logging
from typing import Dict, Any, Optional
from httpx import AsyncClient

logger = logging.getLogger(__name__)


class ExternalAPIError(Exception):
    """Custom exception for external API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None, details: Any = None):
        self.message = message
        self.status_code = status_code
        self.details = details
        super().__init__(self.message)

    def __str__(self):
        if self.status_code:
            return f"ExternalAPIError: [Status {self.status_code}] {self.message}"
        return f"ExternalAPIError: {self.message}"


class BaseApiClient:
    """
    Base class for external API clients with common HTTP request handling.
    """

    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url
        self._client: Optional[AsyncClient] = None
        self._timeout = timeout
        logger.debug(f"BaseApiClient initialized with base_url: {base_url}")

    async def _get_client(self) -> AsyncClient:
        """Gets or initializes the httpx AsyncClient."""
        if self._client is None:
            self._client = AsyncClient(base_url=self.base_url, timeout=self._timeout)
            logger.debug(f"httpx AsyncClient initialized for {self.base_url}")
        return self._client

    async def _make_request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> httpx.Response:
        """
        Makes an asynchronous HTTP request and handles common errors.
        """
        try:
            client = await self._get_client()
            response = await client.request(
                method=method, url=url, params=params, json=json, data=data, headers=headers
            )
            response.raise_for_status()  # Raise an exception for 4xx or 5xx status codes
            return response
        except httpx.TimeoutException as e:
            logger.error(f"Request to {self.base_url}{url} timed out: {e}")
            raise ExternalAPIError("Request timed out", details=str(e)) from e
        except httpx.NetworkError as e:
            logger.error(f"Network error during request to {self.base_url}{url}: {e}")
            raise ExternalAPIError("Network error", details=str(e)) from e
        except httpx.HTTPStatusError as e:
            logger.error(
                f"HTTP error during request to {self.base_url}{url}: {e.response.status_code} - {e.response.text}"
            )
            raise ExternalAPIError(
                f"HTTP error: {e.response.status_code}",
                status_code=e.response.status_code,
                details=e.response.text,
            ) from e
        except Exception as e:
            logger.error(f"An unexpected error occurred during request to {self.base_url}{url}: {e}", exc_info=True)
            raise ExternalAPIError("An unexpected error occurred", details=str(e)) from e

    async def get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> httpx.Response:
        """Convenience method for GET requests."""
        return await self._make_request("GET", url, params=params, headers=headers)

    async def post(
        self,
        url: str,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> httpx.Response:
        """Convenience method for POST requests."""
        return await self._make_request("POST", url, json=json, data=data, headers=headers)

    # Add other HTTP methods (PUT, DELETE, etc.) as needed

    async def close(self):
        """Closes the httpx AsyncClient session."""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.debug(f"httpx AsyncClient closed for {self.base_url}")

# Example Usage (within another service class)
# from .external_api_client import BaseApiClient, ExternalAPIError
#
# class MyService(BaseApiClient):
#    def __init__(self, api_url: str, api_key: str):
#        super().__init__(base_url=api_url)
#        self._api_key = api_key
#
#    async def fetch_data(self, endpoint: str, item_id: str):
#        try:
#            response = await self.get(
#                f"/{endpoint}/{item_id}",
#                headers={"Authorization": f"Bearer {self._api_key}"}
#            )
#            return response.json()
#        except ExternalAPIError as e:
#            logger.error(f"Failed to fetch data: {e}")
#            # Handle the error, maybe raise a specific service exception

# Remember to call service.close() during application shutdown
