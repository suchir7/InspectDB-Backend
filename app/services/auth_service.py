from typing import Tuple, Optional
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.models.user import User
from app.schemas.auth import UserCreate, UserLogin
from app.repositories.user_repository import user_repository
from app.core.security import get_password_hash, verify_password, create_access_token

class AuthService:
    def register_user(self, db: Session, user_in: UserCreate) -> Tuple[str, User]:
        normalized_email = user_in.email.strip().lower()
        
        # Check if user already exists
        existing_user = user_repository.get_by_email(db, normalized_email)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An account with this email address already exists."
            )
        
        # Hash password securely
        hashed_pwd = get_password_hash(user_in.password)
        
        # Always enforce default role USER
        new_user = User(
            name=user_in.name.strip(),
            email=normalized_email,
            password_hash=hashed_pwd,
            role="USER"
        )
        
        created_user = user_repository.create(db, new_user)
        
        # Generate JWT token
        token = create_access_token(
            subject=created_user.id,
            claims={
                "email": created_user.email,
                "role": created_user.role,
                "name": created_user.name
            }
        )
        
        return token, created_user

    def authenticate_user(self, db: Session, user_login: UserLogin) -> Tuple[str, User]:
        normalized_email = user_login.email.strip().lower()
        user = user_repository.get_by_email(db, normalized_email)
        
        if not user or not verify_password(user_login.password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password.",
                headers={"WWW-Authenticate": "Bearer"}
            )
        
        token = create_access_token(
            subject=user.id,
            claims={
                "email": user.email,
                "role": user.role,
                "name": user.name
            }
        )
        
        return token, user

    def get_current_user_by_id(self, db: Session, user_id: str) -> Optional[User]:
        return user_repository.get_by_id(db, user_id)

auth_service = AuthService()
