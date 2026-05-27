from enum import Enum
from pydantic import BaseModel

class UserRole(str, Enum):
    ADMIN = "admin"
    VIEWER = "viewer"
    USER = "user"

class User(BaseModel):
    username: str
    email: str
    full_name: str
    role: UserRole
