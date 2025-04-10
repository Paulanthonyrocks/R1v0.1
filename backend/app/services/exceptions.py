# backend/app/services/exceptions.py

class FeedManagerError(Exception):
    """Base exception for FeedManager errors."""
    pass

class FeedNotFoundError(FeedManagerError):
    """Raised when a specific Feed ID is not found."""
    def __init__(self, feed_id: str):
        self.feed_id = feed_id
        super().__init__(f"Feed ID '{feed_id}' not found.")

class FeedOperationError(FeedManagerError):
    """Raised for invalid operations (e.g., stopping a stopped feed)."""
    pass

class ResourceLimitError(FeedManagerError):
    """Raised when resource limits (e.g., memory) are exceeded."""
    pass