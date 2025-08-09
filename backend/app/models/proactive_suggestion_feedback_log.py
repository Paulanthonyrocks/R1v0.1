from sqlalchemy import Column, String, DateTime, JSON, Integer, func
from app.models.base import Base

class ProactiveSuggestionFeedbackLog(Base):
    __tablename__ = "proactive_suggestion_feedback_log"

    id = Column(
        String, primary_key=True
    )  # A unique ID for this feedback entry, e.g., str(uuid.uuid4())
    suggestion_id = Column(
        String, index=True, unique=True
    )  # The ID of the suggestion this feedback is for
    user_id = Column(String, index=True)
    timestamp = Column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )  # Record creation/update time
    suggestion_details = Column(
        JSON
    )  # Store what was suggested, e.g., route, destination, type of suggestion
    interaction_status = Column(
        String
    )  # e.g., "suggested", "accepted", "rejected", "ignored", "modified", "pending_feedback", "error_in_suggestion"
    user_feedback_text = Column(String, nullable=True)
    user_rating = Column(Integer, nullable=True)  # e.g., 1-5 stars
    created_at = Column(DateTime, server_default=func.now())  # Record creation time
