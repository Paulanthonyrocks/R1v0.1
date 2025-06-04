import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException, status
from unittest.mock import MagicMock, patch
from typing import Dict, Any

from app.main import app  # FastAPI app instance
from app.services.personalized_routing_service import PersonalizedRoutingService
from app.dependencies import get_personalized_routing_service, get_current_active_user
# Import the request model if needed for constructing payloads explicitly,
# or for response model validation if you add such tests.
# from app.routers.personalized_routes import SuggestionFeedbackRequest

# Sample user for authenticated routes
MOCK_USER_VALID = {"uid": "test-user-id-123", "email": "test@example.com"}
MOCK_USER_UNAUTHENTICATED = None # Or a user dict that would fail authentication checks

@pytest.fixture(scope="function")
def mock_personalized_routing_service_fixture():
    # Use a new MagicMock for each test function to ensure isolation
    service = MagicMock(spec=PersonalizedRoutingService)
    return service

@pytest.fixture(scope="function")
def client_fixture(mock_personalized_routing_service_fixture: MagicMock):
    # Store original overrides to restore them later, ensuring test isolation
    original_overrides = app.dependency_overrides.copy()

    app.dependency_overrides[get_personalized_routing_service] = lambda: mock_personalized_routing_service_fixture
    app.dependency_overrides[get_current_active_user] = lambda: MOCK_USER_VALID

    with TestClient(app) as test_client:
        yield test_client

    # Restore original overrides
    app.dependency_overrides = original_overrides


# API Path
FEEDBACK_ENDPOINT_URL = "/api/routes/suggestions/feedback"

def test_record_suggestion_feedback_success(client_fixture: TestClient, mock_personalized_routing_service_fixture: MagicMock):
    mock_personalized_routing_service_fixture.record_suggestion_feedback.return_value = True
    payload = {
        "suggestion_id": "sugg_success_123",
        "interaction_status": "accepted",
        "feedback_text": "This was a great suggestion!",
        "rating": 5
    }
    response = client_fixture.post(FEEDBACK_ENDPOINT_URL, json=payload)

    assert response.status_code == 200
    assert response.json() == {"message": "Feedback recorded successfully"}
    mock_personalized_routing_service_fixture.record_suggestion_feedback.assert_called_once_with(
        suggestion_id="sugg_success_123",
        user_id=MOCK_USER_VALID["uid"],
        interaction_status="accepted",
        feedback_text="This was a great suggestion!",
        rating=5
    )

def test_record_suggestion_feedback_suggestion_not_found(client_fixture: TestClient, mock_personalized_routing_service_fixture: MagicMock):
    mock_personalized_routing_service_fixture.record_suggestion_feedback.return_value = False # Simulate service returning False
    payload = {
        "suggestion_id": "sugg_not_found_456",
        "interaction_status": "ignored"
    }
    response = client_fixture.post(FEEDBACK_ENDPOINT_URL, json=payload)

    assert response.status_code == 404
    assert "detail" in response.json()
    # The exact message depends on what the endpoint raises, based on personalized_routes.py:
    assert response.json()["detail"] == "Suggestion ID not found, user mismatch, or invalid data. Feedback not recorded."
    mock_personalized_routing_service_fixture.record_suggestion_feedback.assert_called_once()


def test_record_suggestion_feedback_validation_error_rating_out_of_bounds(client_fixture: TestClient):
    payload_too_low = {
        "suggestion_id": "sugg_invalid_rating_789",
        "interaction_status": "rejected",
        "rating": 0 # Invalid rating
    }
    response_low = client_fixture.post(FEEDBACK_ENDPOINT_URL, json=payload_too_low)
    assert response_low.status_code == 422 # Unprocessable Entity for Pydantic validation errors
    assert "rating" in response_low.text # Check that the error message mentions 'rating'

    payload_too_high = {
        "suggestion_id": "sugg_invalid_rating_012",
        "interaction_status": "accepted",
        "rating": 6 # Invalid rating
    }
    response_high = client_fixture.post(FEEDBACK_ENDPOINT_URL, json=payload_too_high)
    assert response_high.status_code == 422
    assert "rating" in response_high.text

def test_record_suggestion_feedback_missing_required_fields(client_fixture: TestClient):
    # Missing suggestion_id
    payload_missing_sugg_id = {
        # "suggestion_id": "test_id",
        "interaction_status": "accepted"
    }
    response = client_fixture.post(FEEDBACK_ENDPOINT_URL, json=payload_missing_sugg_id)
    assert response.status_code == 422
    assert "suggestion_id" in response.text

    # Missing interaction_status
    payload_missing_status = {
        "suggestion_id": "test_id_2",
        # "interaction_status": "accepted"
    }
    response = client_fixture.post(FEEDBACK_ENDPOINT_URL, json=payload_missing_status)
    assert response.status_code == 422
    assert "interaction_status" in response.text

def test_record_suggestion_feedback_unauthenticated(client_fixture: TestClient, mock_personalized_routing_service_fixture: MagicMock):
    # Temporarily override get_current_active_user for this specific test
    # to simulate unauthenticated access by raising HTTPException(401)

    def mock_unauthenticated_user():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    app.dependency_overrides[get_current_active_user] = mock_unauthenticated_user

    payload = {
        "suggestion_id": "sugg_auth_fail",
        "interaction_status": "accepted",
    }
    response = client_fixture.post(FEEDBACK_ENDPOINT_URL, json=payload)

    assert response.status_code == 401
    assert "Not authenticated" in response.json()["detail"] # Or whatever detail your auth setup returns

    # Clean up the specific override for this test if not using client_fixture's general cleanup
    # (client_fixture already handles general cleanup by restoring original_overrides)
    # However, since we modified it *after* client_fixture setup, we should restore it to what client_fixture set up
    app.dependency_overrides[get_current_active_user] = lambda: MOCK_USER_VALID


def test_record_suggestion_feedback_service_exception(client_fixture: TestClient, mock_personalized_routing_service_fixture: MagicMock):
    # Simulate an unexpected exception in the service layer
    mock_personalized_routing_service_fixture.record_suggestion_feedback.side_effect = Exception("Unexpected service error")

    payload = {
        "suggestion_id": "sugg_service_ex",
        "interaction_status": "accepted",
    }
    response = client_fixture.post(FEEDBACK_ENDPOINT_URL, json=payload)

    assert response.status_code == 500 # As per the endpoint's general exception handler
    assert "An unexpected error occurred while recording feedback" in response.json()["detail"]
    assert "Unexpected service error" in response.json()["detail"] # Check if original error message is included

# Add more tests for other interaction_status values, edge cases for feedback_text, etc.
# For example, test with only required fields:
def test_record_suggestion_feedback_minimal_payload(client_fixture: TestClient, mock_personalized_routing_service_fixture: MagicMock):
    mock_personalized_routing_service_fixture.record_suggestion_feedback.return_value = True
    payload = {
        "suggestion_id": "sugg_minimal",
        "interaction_status": "ignored"
        # feedback_text and rating are optional
    }
    response = client_fixture.post(FEEDBACK_ENDPOINT_URL, json=payload)

    assert response.status_code == 200
    assert response.json() == {"message": "Feedback recorded successfully"}
    mock_personalized_routing_service_fixture.record_suggestion_feedback.assert_called_once_with(
        suggestion_id="sugg_minimal",
        user_id=MOCK_USER_VALID["uid"],
        interaction_status="ignored",
        feedback_text=None, # Explicitly check optional fields are None
        rating=None
    )

# Example of how to test if user_id from request body (if it were part of SuggestionFeedbackRequest)
# is ignored and the one from auth dependency is used.
# Currently, SuggestionFeedbackRequest does not have user_id, which is good practice.
# This test is more of a thought exercise for other scenarios.
# def test_user_id_from_auth_is_used_over_payload(client: TestClient, mock_personalized_routing_service: MagicMock):
#    pass
#
