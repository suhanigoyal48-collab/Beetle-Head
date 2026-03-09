from sqlalchemy import Column, Text, Integer, BigInteger, TIMESTAMP, String
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import declarative_base

from db.base import Base

class PageChunk(Base):
    __tablename__ = "page_chunks"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(Text, index=True, nullable=False)
    url = Column(Text, nullable=False)
    title = Column(Text)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(1536), nullable=False)
    content_hash = Column(String(32), index=True)  # 👈 add this
    created_at = Column(TIMESTAMP, server_default=func.now())