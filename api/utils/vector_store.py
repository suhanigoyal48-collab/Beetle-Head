import os
import hashlib
from typing import Optional
from langchain_openai import OpenAIEmbeddings
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from db.database import SessionLocal
from db.models.vector_rag import PageChunk
from db.models.chatContext import ChatContext
from sqlalchemy import select
from sqlalchemy.orm import Session
from dotenv import load_dotenv

load_dotenv()


class VectorStoreService:

    VECTOR_DIM = 1536

    def __init__(self):
        openai_key = os.getenv("OPENAI_API_KEY")

        if openai_key:
            self.openai_embeddings = OpenAIEmbeddings(
                model="text-embedding-3-small",
                openai_api_key=openai_key
            )
        else:
            self.openai_embeddings = None

        self.ollama_embeddings = OllamaEmbeddings(model="nomic-embed-text")

        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=800,
            chunk_overlap=100,
            separators=["\n\n", "\n", " ", ""]
        )

    # --------------------------------------------------------
    # Ensure vectors match pgvector dimension (1536)
    # --------------------------------------------------------
    def _ensure_1536_dimensions(self, vector: list[float]) -> list[float]:

        if len(vector) < self.VECTOR_DIM:
            return vector + [0.0] * (self.VECTOR_DIM - len(vector))

        if len(vector) > self.VECTOR_DIM:
            return vector[:self.VECTOR_DIM]

        return vector

    # --------------------------------------------------------
    # Batch embed documents
    # --------------------------------------------------------
    async def _embed_documents_with_fallback(self, texts: list[str]) -> list[list[float]]:

        if self.openai_embeddings:
            try:
                return await self.openai_embeddings.aembed_documents(texts)
            except Exception as e:
                print(f"⚠️ OpenAI batch embedding failed: {e}")

        try:
            print("🔄 Using Ollama for batch embeddings...")
            embeddings = await self.ollama_embeddings.aembed_documents(texts)
            return [self._ensure_1536_dimensions(v) for v in embeddings]

        except Exception as ollama_err:
            print(f"❌ Ollama batch embedding failed: {ollama_err}")
            return [[0.0] * self.VECTOR_DIM for _ in texts]

    # --------------------------------------------------------
    # Query embedding
    # --------------------------------------------------------
    async def _embed_query_with_fallback(self, query: str) -> list[float]:

        if self.openai_embeddings:
            try:
                vec = await self.openai_embeddings.aembed_query(query)
                return self._ensure_1536_dimensions(vec)

            except Exception as e:
                print(f"⚠️ OpenAI query embedding failed: {e}")

        try:
            print("🔄 Using Ollama for query embedding...")
            vec = await self.ollama_embeddings.aembed_query(query)
            return self._ensure_1536_dimensions(vec)

        except Exception as ollama_err:
            print(f"❌ Ollama query embedding failed: {ollama_err}")
            return [0.0] * self.VECTOR_DIM

    # --------------------------------------------------------
    # Context existence check
    # --------------------------------------------------------
    def has_context(self, user_id: str, url: str, conversation_id: int) -> bool:

        db: Session = SessionLocal()

        try:
            exists = db.query(PageChunk).join(
                ChatContext,
                PageChunk.id == ChatContext.chunk_id
            ).filter(
                PageChunk.user_id == str(user_id),
                PageChunk.url == url,
                ChatContext.conversation_id == conversation_id
            ).first()

            return exists is not None

        except Exception as e:
            print(f"Error checking context: {e}")
            return False

        finally:
            db.close()
    async def process_and_save_context(
            self,
            user_id: str,
            conversation_id: int,
            url: str,
            content: str
        ) -> int:

        if not content:
            return 0

        chunks = self.text_splitter.split_text(content)

        if not chunks:
            return 0

        db: Session = SessionLocal()
        saved_count = 0

        try:
            chunk_hashes = [hashlib.md5(c.encode()).hexdigest() for c in chunks]

            existing_chunks = db.query(PageChunk).filter(
                PageChunk.url == url,
            PageChunk.content_hash.in_(chunk_hashes)
        ).all()

            existing_hash_to_id = {c.content_hash: c.id for c in existing_chunks}

            new_chunks_data = []
            seen_new_hashes = set()

            for i, chunk_text in enumerate(chunks):

                content_hash = chunk_hashes[i]

                if (
                    content_hash not in existing_hash_to_id
                    and content_hash not in seen_new_hashes
                ):
                    new_chunks_data.append((i, chunk_text, content_hash))
                    seen_new_hashes.add(content_hash)

            new_chunk_objects = []
            if new_chunks_data:

                texts_to_embed = [data[1] for data in new_chunks_data]

                embeddings = await self._embed_documents_with_fallback(texts_to_embed)

                new_chunk_objects = []

                for idx, (original_i, text, c_hash) in enumerate(new_chunks_data):

                    new_chunk = PageChunk(
                        user_id=str(user_id),
                        url=url,
                        chunk_index=original_i,
                        content=text,
                        content_hash=c_hash,
                        embedding=self._ensure_1536_dimensions(embeddings[idx])
                    )

                    new_chunk_objects.append(new_chunk)

            db.add_all(new_chunk_objects)
            db.flush()

            for nc in new_chunk_objects:
                existing_hash_to_id[nc.content_hash] = nc.id

            if conversation_id:

                chunk_ids_to_link = list({
                    existing_hash_to_id[h]
                    for h in chunk_hashes
                    if h in existing_hash_to_id
                })

                existing_links = db.query(ChatContext).filter(
                    ChatContext.conversation_id == conversation_id,
                    ChatContext.chunk_id.in_(chunk_ids_to_link)
                ).all()

                existing_linked_ids = {link.chunk_id for link in existing_links}

                new_links = []

                for c_id in chunk_ids_to_link:

                    if c_id not in existing_linked_ids:
                        new_links.append(
                            ChatContext(
                                conversation_id=conversation_id,
                                chunk_id=c_id
                            )
                        )

                if new_links:
                    db.add_all(new_links)
                    saved_count = len(new_links)

            db.commit()
            return saved_count

        except Exception as e:

            print(f"❌ Error saving vector context: {e}")
            db.rollback()
            return 0

        finally:
            db.close()

    # --------------------------------------------------------
    # Retrieve relevant chunks
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

            def get_query_embedding_sync(q):

                if self.openai_embeddings:
                    try:
                        vec = self.openai_embeddings.embed_query(q)
                        return self._ensure_1536_dimensions(vec)

                    except Exception as e:
                        print(f"⚠️ OpenAI sync query embedding failed: {e}")

                try:
                    print("🔄 Using Ollama for sync query embedding...")
                    vec = self.ollama_embeddings.embed_query(q)
                    return self._ensure_1536_dimensions(vec)

                except Exception as ollama_err:
                    print(f"❌ Ollama sync query embedding failed: {ollama_err}")
                    return [0.0] * self.VECTOR_DIM

            query_embedding = get_query_embedding_sync(query)

            if conversation_id and current_url:

                linked_chunk_ids = db.query(
                    ChatContext.chunk_id
                ).filter(
                    ChatContext.conversation_id == conversation_id
                ).subquery()

                chunks = db.query(PageChunk).filter(
                    PageChunk.id.in_(select(linked_chunk_ids)),
                    PageChunk.user_id == str(user_id),
                    PageChunk.url == current_url
                ).order_by(
                    PageChunk.embedding.cosine_distance(query_embedding)
                ).limit(limit).all()

                if chunks:
                    return "\n\n".join(c.content for c in chunks)

            if current_url:

                chunks = db.query(PageChunk).filter(
                    PageChunk.user_id == str(user_id),
                    PageChunk.url == current_url
                ).order_by(
                    PageChunk.embedding.cosine_distance(query_embedding)
                ).limit(limit).all()

                if chunks:
                    return "\n\n".join(c.content for c in chunks)

            return ""

        except Exception as e:
            print(f"❌ Error retrieving vector context: {e}")
            return ""

        finally:
            db.close()


vector_store = VectorStoreService()