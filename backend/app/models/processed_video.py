from sqlalchemy import Column, Integer, String, Float, DateTime
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()


class ProcessedVideo(Base):
    __tablename__ = "processed_videos"

    id = Column(Integer, primary_key=True, index=True)
    stream_id = Column(String, index=True, nullable=False)
    file_path = Column(String, nullable=False)
    start_time = Column(DateTime, default=datetime.now, nullable=False)
    end_time = Column(DateTime, default=datetime.now, nullable=False)
    duration = Column(Float, nullable=False)

    def __repr__(self):
        return f"<ProcessedVideo(id={self.id}, stream_id='{self.stream_id}', file_path='{self.file_path}')>"
