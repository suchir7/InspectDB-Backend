from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.models.user import User

class UserRepository:
    def get_by_id(self, db: Session, user_id: str) -> Optional[User]:
        return db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()

    def get_by_email(self, db: Session, email: str) -> Optional[User]:
        normalized_email = email.strip().lower() if email else ""
        return db.execute(select(User).where(User.email == normalized_email)).scalar_one_or_none()

    def create(self, db: Session, user: User) -> User:
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    def update(self, db: Session, user: User) -> User:
        db.commit()
        db.refresh(user)
        return user

    def delete(self, db: Session, user_id: str) -> bool:
        user = self.get_by_id(db, user_id)
        if user:
            db.delete(user)
            db.commit()
            return True
        return False

user_repository = UserRepository()
