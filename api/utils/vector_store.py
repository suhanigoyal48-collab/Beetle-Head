# ============================================================
# vector_store.py
# ============================================================

import os
import hashlib
from typing import Optional
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from db.database import SessionLocal
from db.models.vector_rag import PageChunk
from db.models.chatContext import ChatContext
from sqlalchemy import select
from sqlalchemy.orm import Session
from dotenv import load_dotenv

load_dotenv()


class VectorStoreService:

    def __init__(self):
        self.embeddings = OpenAIEmbeddings(
            model="text-embedding-3-small",
            openai_api_key=os.getenv("OPENAI_API_KEY")
        )
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=800,
            chunk_overlap=100,
            separators=["\n\n", "\n", " ", ""]
        )

    # --------------------------------------------------------
    # INGEST: Split, embed, save chunks, link to conversation
    # --------------------------------------------------------
    def process_and_save_context(
        self,
        user_id: str,
        conversation_id: int,
        url: str,
        content: str
    ) -> int:

        if not content:
            return 0

        chunks = self.text_splitter.split_text(content)

        db: Session = SessionLocal()
        saved_count = 0

        try:
            for i, chunk_text in enumerate(chunks):

                # Deduplicate by content hash (more reliable than index)
                content_hash = hashlib.md5(chunk_text.encode()).hexdigest()

                existing_chunk = db.query(PageChunk).filter(
                    PageChunk.url == url,
                    PageChunk.content_hash == content_hash
                ).first()

                if existing_chunk:
                    chunk_id = existing_chunk.id
                else:
                    embedding = self.embeddings.embed_query(chunk_text)
                    new_chunk = PageChunk(
                        user_id=str(user_id),
                        url=url,
                        chunk_index=i,
                        content=chunk_text,
                        content_hash=content_hash,
                        embedding=embedding
                    )
                    db.add(new_chunk)
                    db.flush()  # get ID without committing yet
                    chunk_id = new_chunk.id

                # Link chunk to conversation if not already linked
                if conversation_id:
                    existing_link = db.query(ChatContext).filter(
                        ChatContext.conversation_id == conversation_id,
                        ChatContext.chunk_id == chunk_id
                    ).first()

                    if not existing_link:
                        db.add(ChatContext(
                            conversation_id=conversation_id,
                            chunk_id=chunk_id
                        ))
                        saved_count += 1

            db.commit()  # single commit after all chunks
            return saved_count

        except Exception as e:
            print(f"❌ Error saving vector context: {e}")
            db.rollback()
            return 0

        finally:
            db.close()

    # --------------------------------------------------------
    # RETRIEVE: Get relevant chunks for a query
    # Only called when query is page-related
    # --------------------------------------------------------
    def get_relevant_context(
        self,
        user_id: str,
        query: str,
        conversation_id: Optional[int] = None,
        current_url: Optional[str] = None,
        limit: int = 5
    ) -> str:

        try:
            db: Session = SessionLocal()
            query_embedding = self.embeddings.embed_query(query)

            # Strategy 1: Current URL + conversation scoped (MOST ACCURATE)
            # Only get chunks from the CURRENT tab that belong to this conversation
            if conversation_id and current_url:
                linked_chunk_ids = db.query(ChatContext.chunk_id).filter(
                    ChatContext.conversation_id == conversation_id
                ).subquery()

                chunks = db.query(PageChunk).filter(
                    PageChunk.id.in_(select(linked_chunk_ids)),
                    PageChunk.user_id == str(user_id),
                    PageChunk.url == current_url  # 👈 lock to current tab
                ).order_by(
                    PageChunk.embedding.cosine_distance(query_embedding)
                ).limit(limit).all()

                if chunks:
                    print(f"✅ {len(chunks)} chunks from current tab + conversation")
                    return "\n\n".join(c.content for c in chunks)

            # Strategy 2: Current URL only (no conversation filter)
            if current_url:
                chunks = db.query(PageChunk).filter(
                    PageChunk.user_id == str(user_id),
                    PageChunk.url == current_url
                ).order_by(
                    PageChunk.embedding.cosine_distance(query_embedding)
                ).limit(limit).all()

                if chunks:
                    print(f"✅ {len(chunks)} chunks from current tab URL")
                    return "\n\n".join(c.content for c in chunks)

            print(f"⚠️ No chunks found for current tab")
            return ""

        except Exception as e:
            print(f"❌ Error retrieving vector context: {e}")
            return ""

        finally:
            db.close()

vector_store = VectorStoreService()