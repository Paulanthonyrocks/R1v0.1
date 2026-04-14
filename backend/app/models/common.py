from typing import TypeVar, Generic, Optional
from pydantic import BaseModel

T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    status: str
    message: str
    data: Optional[T] = None

    class Config:
        from_attributes = True

    @classmethod
    def success(
        cls, data: Optional[T] = None, message: str = "Operation successful."
    ) -> "APIResponse[T]":
        return cls(status="success", message=message, data=data)

    @classmethod
    def error(
        cls, message: str = "Operation failed.", data: Optional[T] = None
    ) -> "APIResponse[T]":
        return cls(status="error", message=message, data=data)
