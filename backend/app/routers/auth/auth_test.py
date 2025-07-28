from fastapi import APIRouter, Depends
from app.dependencies import get_current_active_user

router = APIRouter()


@router.get("/test-auth", summary="Test authentication")
async def test_auth(current_user: dict = Depends(get_current_active_user)):
    """
    Test endpoint to verify authentication is working.
    Returns user information if authentication is successful.
    """
    return {
        "message": "Authentication successful",
        "user": {
            "uid": current_user.get("uid"),
            "email": current_user.get("email"),
            "name": current_user.get("name"),
        },
    }
