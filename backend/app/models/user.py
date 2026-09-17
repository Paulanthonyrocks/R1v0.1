from enum import Enum
from pydantic import BaseModel, Field

class UserRole(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    AGENCY = "agency"
    VIEWER = "viewer"
    USER = "user"

class User(BaseModel):
    username: str
    email: str
    full_name: str
    role: UserRole
    signal_ids: list[str] = Field(default_factory=list)
