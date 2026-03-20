import random
from sqlalchemy.orm import sessionmaker
from db.database import engine
from db.models.vector_rag import PageChunk

from dotenv import load_dotenv

load_dotenv()

# Create session
Session = sessionmaker(bind=engine)


def generate_random_embedding(dim=1536):
    """Generate random embedding for testing"""
    return [random.random() for _ in range(dim)]


def test_insert_page_chunk():
    session = Session()

    try:
        # Create test object
        chunk = PageChunk(
            user_id="test_user",
            url="https://example.com",
            chunk_index=0,
            content="This is a test chunk for vector_rag",
            embedding=generate_random_embedding()
        )

        session.add(chunk)
        session.commit()

        print("✅ Test insert successful!")
        print(f"Inserted ID: {chunk.id}")

    except Exception as e:
        session.rollback()
        print(f"❌ Insert failed: {e}")

    finally:
        session.close()


if __name__ == "__main__":
    test_insert_page_chunk()