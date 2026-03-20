from langchain_openai import OpenAIEmbeddings
from langchain_ollama import OllamaEmbeddings
import os
from dotenv import load_dotenv

load_dotenv()

# Global instances initialized only if needed or when possible
_openai_embeddings = None
_ollama_embeddings = None

def get_openai_embeddings():
    """
    Retrieves the global OpenAIEmbeddings instance, initializing it if necessary.
    
    Model: text-embedding-3-small
    Dimensions: 1536 (Fixed to match vector database schema)
    Expects: OPENAI_API_KEY environment variable.
    """
    global _openai_embeddings
    if _openai_embeddings is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key:
            _openai_embeddings = OpenAIEmbeddings(
                model="text-embedding-3-small",
                dimensions=1536  # Anchored to 1536 as per DB requirement
            )
    return _openai_embeddings

def get_ollama_embeddings():
    """
    Retrieves the global OllamaEmbeddings instance, initializing it if necessary.
    
    Model: nomic-embed-text
    """
    global _ollama_embeddings
    if _ollama_embeddings is None:
        _ollama_embeddings = OllamaEmbeddings(model="nomic-embed-text")
    return _ollama_embeddings

def ensure_1536_dimensions(vector: list[float]) -> list[float]:
    """
    Normalizes a vector to exactly 1536 dimensions.
    
    Logic:
    - If shorter: Pads with 0.0.
    - If longer: Truncates to 1536 elements.
    - Used to maintain schema consistency across different embedding providers.
    """
    if len(vector) < 1536:
        return vector + [0.0] * (1536 - len(vector))
    elif len(vector) > 1536:
        return vector[:1536]
    return vector

async def embed_text(text: str) -> list[float]:
    """
    Generate an embedding vector for the provided text with automatic provider fallback.
    
    Workflow:
    1. Attempts to use OpenAI (text-embedding-3-small).
    2. If OpenAI fails or key is missing, falls back to local Ollama (nomic-embed-text).
    3. Always ensures the resulting vector is 1536 dimensions.
    4. Returns a zero-vector of 1536 dimensions if all providers fail or text is empty.
    
    Expects: Input text string.
    Returns: A list of 1536 floating point numbers.
    """
    if not text:
        return [0.0] * 1536

    # 1. Try OpenAI first (Cloud-based, higher quality)
    embeddings = get_openai_embeddings()
    if embeddings:
        try:
            vec = await embeddings.aembed_query(text)
            return ensure_1536_dimensions(vec) # Always anchor just in case
        except Exception as e:
            print(f"⚠️ OpenAI embedding failed, falling back to Ollama: {e}")
    
    # 2. Fallback to Ollama (Local, privacy-focused)
    try:
        ollama = get_ollama_embeddings()
        vec = await ollama.aembed_query(text)
        return ensure_1536_dimensions(vec)
    except Exception as ollama_err:
        print(f"❌ Ollama embedding also failed: {ollama_err}")
        return [0.0] * 1536