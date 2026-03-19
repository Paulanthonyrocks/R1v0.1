from pydantic import BaseModel
from app.models.validation import SanitizedBaseModel

class User(SanitizedBaseModel):
    username: str
    email: str
    full_name: str
    role: str
