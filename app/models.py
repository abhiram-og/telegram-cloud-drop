import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class BatchState(str, enum.Enum):
    collecting = "collecting"
    finalizing = "finalizing"
    ready = "ready"
    failed = "failed"


class FileState(str, enum.Enum):
    pending = "pending"
    transferring = "transferring"
    ready = "ready"
    failed = "failed"


class Batch(Base):
    __tablename__ = "batches"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    state: Mapped[BatchState] = mapped_column(
        Enum(BatchState), default=BatchState.collecting
    )
    token: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid4, unique=True, index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    files: Mapped[list["StoredFile"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class StoredFile(Base):
    __tablename__ = "stored_files"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"), index=True
    )
    telegram_file_id: Mapped[str] = mapped_column(String(255))
    original_name: Mapped[str] = mapped_column(String(1024))
    mime_type: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    object_key: Mapped[str | None] = mapped_column(String(1024), unique=True)
    state: Mapped[FileState] = mapped_column(
        Enum(FileState), default=FileState.pending, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    batch: Mapped[Batch] = relationship(back_populates="files")
