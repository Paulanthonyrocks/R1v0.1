from sqlalchemy import Column, Integer, String, Float, DateTime
from app.models.base import Base
from datetime import datetime, timezone




class ProcessedVideo(Base):
    __tablename__ = "processed_videos"

    id = Column(Integer, primary_key=True, index=True)
    stream_id = Column(String, index=True, nullable=False)
    file_path = Column(String, nullable=False)
    start_time = Column(DateTime, default=datetime.now, nullable=False)
    end_time = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    duration = Column(Float, nullable=False)

    def __repr__(self):
        return f"<ProcessedVideo(id={self.id}, stream_id='{self.stream_id}', file_path='{self.file_path}')>"
