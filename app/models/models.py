import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, BigInteger, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from app.db.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class Client(Base):
    __tablename__ = "clients"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    storage_limit_bytes = Column(BigInteger, nullable=False)
    used_storage_bytes = Column(BigInteger, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    files = relationship("FileRecord", back_populates="client", cascade="all, delete-orphan")

    @property
    def remaining_storage_bytes(self) -> int:
        return max(0, self.storage_limit_bytes - self.used_storage_bytes)

    def has_enough_storage(self, required_bytes: int) -> bool:
        return self.remaining_storage_bytes >= required_bytes

    def __repr__(self):
        return f"<Client id={self.id} name={self.name} limit={self.storage_limit_bytes}>"


class FileRecord(Base):
    __tablename__ = "file_records"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    client_id = Column(String, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    file_name = Column(String(512), nullable=False)
    file_size_bytes = Column(BigInteger, nullable=False)
    file_path = Column(String(1024), nullable=False)  # Logical reference only
    mime_type = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    client = relationship("Client", back_populates="files")

    __table_args__ = (
        Index("ix_file_records_client_id", "client_id"),
    )

    def __repr__(self):
        return f"<FileRecord id={self.id} name={self.file_name} size={self.file_size_bytes}>"
