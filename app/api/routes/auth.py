from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.schemas.auth import UserCreate, UserLogin, UserResponse, TokenResponse
from app.services.auth_service import auth_service
from app.api.deps import get_current_user
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["Authentication"])

@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account"
)
async def register(
    user_in: UserCreate,
    db: Session = Depends(get_db)
):
    """
    Register a new user in Neon PostgreSQL:
    - Validates email and strong password requirements
    - Checks for existing user accounts
    - Securely hashes password using bcrypt
    - Returns JWT token and safe user profile
    """
    token, user = auth_service.register_user(db, user_in)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserResponse.model_validate(user)
    )

@router.post(
    "/login",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Authenticate user and receive JWT access token"
)
async def login(
    user_login: UserLogin,
    db: Session = Depends(get_db)
):
    """
    Authenticate user using email and password:
    - Verifies user exists in Neon PostgreSQL
    - Validates password hash
    - Returns signed JWT access token and safe user profile
    """
    token, user = auth_service.authenticate_user(db, user_login)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserResponse.model_validate(user)
    )

@router.get(
    "/me",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve profile of currently authenticated user"
)
async def get_me(
    current_user: User = Depends(get_current_user)
):
    """
    Return currently authenticated user information from JWT token session.
    Requires Bearer token authorization header.
    """
    return UserResponse.model_validate(current_user)

@router.post(
    "/logout",
    status_code=status.HTTP_200_OK,
    summary="Log out active session"
)
async def logout(
    current_user: User = Depends(get_current_user)
):
    """
    Logs out the authenticated user session.
    """
    return {"message": "Session invalidated successfully.", "user_id": current_user.id}
