from sqlalchemy import Column, String, DateTime, JSON, func
from app.models.base import Base

class UserProfileModel(Base):
    __tablename__ = "user_profiles"

    user_id = Column(String, primary_key=True)
    profile_data = Column(JSON)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
