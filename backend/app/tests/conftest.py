import pytest
from unittest.mock import patch, MagicMock

@pytest.fixture(autouse=True, scope="session")
def mock_firebase_admin():
    """
    Mocks firebase_admin.initialize_app to prevent errors during test runs
    where the app might be initialized multiple times.
    """
    with patch("firebase_admin.initialize_app") as mock_init_app:
        yield mock_init_app
