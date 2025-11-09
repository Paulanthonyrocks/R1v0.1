from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from app.services import auth_service
from app.utils.database import DatabaseManager
from app.database import get_database_manager as get_db

router = APIRouter()


@router.post("/login", summary="Authenticate user and get token")
def login(
 db: DatabaseManager = Depends(get_db), form_data: OAuth2PasswordRequestForm = Depends()
):
    user = auth_service.authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = auth_service.create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}
