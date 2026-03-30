"""SQLAlchemy models and database engine for the auth system.

Database is stored at $MEDIA_TOOLS_DATA/auth.db (default ~/.media-tools/auth.db).
Uses synchronous SQLAlchemy since the rest of the app uses threads.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Boolean, Column, DateTime, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

_DATA_DIR = Path(os.environ.get("MEDIA_TOOLS_DATA", Path.home() / ".media-tools"))
_DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = _DATA_DIR / "auth.db"

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    # Nullable: OIDC users have no local password
    hashed_pw = Column(String, nullable=True)
    # "user" | "admin"
    role = Column(String, nullable=False, default="user")
    # "local" | "oidc:<provider-name>" — hook point for future OIDC integration
    auth_provider = Column(String, nullable=False, default="local")
    # True for the original admin account — cannot be deleted
    is_permanent = Column(Boolean, nullable=False, default=False)
    created_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )


def init_db() -> None:
    """Create all tables if they don't exist."""
    Base.metadata.create_all(bind=engine)


def get_db() -> Session:
    """Return a new database session. Caller is responsible for closing it."""
    return SessionLocal()
