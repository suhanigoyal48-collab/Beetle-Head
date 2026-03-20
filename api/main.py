# main.py - Central API entry point and orchestration layer
# This file defines the FastAPI application, LangGraph workflow, and SSE streaming logic.

import json
import asyncio
from typing import Optional, TypedDict, List, Any
from sqlalchemy import select
from bs4 import BeautifulSoup
from fastapi import FastAPI, Depends, HTTPException, Header, BackgroundTasks
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
from db.models.vector_query import QueryHistory
from pydantic import BaseModel, Field
import os
import io
import tempfile
from fastapi.responses import FileResponse, StreamingResponse
from snapshot import (
    capture_page, 
    get_markdown, 
    generate_special_format, 
    generate_smart_pdf, 
    generate_word_doc,
    generate_markdown_report
)

from langgraph.graph import StateGraph, END
from jose import JWTError, jwt
from datetime import datetime, timedelta
from fastapi import Response, BackgroundTasks
import uuid

# =============================
# IMPORT CHAINS
# =============================
from runnable import (
    agent_chain,
    dom_action_chain,
    explain_chain,
    format_dom_for_llm,
    runnable_chain,
    classifier_chain, 
    context_analyzer_chain,
    rich_content_chain,
    create_context_aware_chain,
    video_context_analyzer_chain,
    extract_youtube_url,
    get_youtube_transcript,
    action_intent_chain,
    rewrite_chain,
    dom_customization_chain,
    micro_manifest_chain,
    filter_results_chain,
)
from manifest_gen import manifest_chain, manifest_stream_chain

# [NEW] Import Agent Graph
from agent_graph import agent_runnable
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from html_parser import extract_readable_page
from sync_schemas import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES
from utils.vector_store import vector_store
from langchain_text_splitters import RecursiveCharacterTextSplitter


from embedding import embed_text

# ======================================================
# GRAPH STATE - Defines the shared data structure for LangGraph nodes
# ======================================================

class AgentState(TypedDict, total=False):
    """
    Represents the state of a single AI request/conversation turn.
    Expected by: LangGraph nodes (video_analyzer, page_context_analyzer, etc.)
    """
    question: str                # User's natural language input
    image_url: Optional[str]     # Optional base64 or URL image for vision tasks
    raw_html: Optional[Any]      # Raw DOM/HTML context from the extension
    current_url: Optional[str]   # URL of the active browser tab
    
    # Analysis Flags
    needs_video: bool            # True if the query requires YouTube transcription
    needs_context: bool          # True if the query requires page context
    needs_actions: bool          # True if the query requires browser automation
    classification: dict         # Results from classifier_chain (intent, rich content types)
    youtube_url: Optional[str]   # Detected YouTube video URL if any

    # Processed Data
    video_transcripts: List[dict]# List of transcripts from AssemblyAI/YouTube
    page_context: Optional[dict] # Cleaned and parsed DOM context

    # Decisioning
    chat_mode: str               # "context" or "simple" based on data availability


# ======================================================
# REQUEST SCHEMA - Pydantic models for API validation
# ======================================================

class GenerateRequest(BaseModel):
    """
    Payload for /generate/stream and /chat endpoints.
    Expected from: Frontend extension
    """
    prompt: str                                          # User query
    image_url: Optional[str] = Field(None, alias="imageUrl") # Vision input
    context: Optional[Any] = None                        # Raw DOM context fallback
    current_url: Optional[str] = Field(None, alias="currentUrl") # Active tab URL
    conversation_id: Optional[int] = Field(None, alias="conversationId") # SQL DB key
    user_id: Optional[str] = Field(None, alias="userId") # Auth identifier
    model: str = "openai"                                # Preferred LLM ("openai" | "ollama")
    history: List[dict] = []                              # Previous chat messages for memory
    
    class Config:
        populate_by_name = True


class SnapshotRequest(BaseModel):
    url: str
    format: str  # "pdf", "png", "markdown", "research_paper", "ppt"
    raw_html: Optional[str] = Field(None, alias="rawHtml")
    dom_json: Optional[dict] = Field(None, alias="domJson")

    class Config:
        populate_by_name = True





# ======================================================
# GRAPH NODES - Logic steps executed in the background
# ======================================================

def video_analyzer(state: AgentState):
    """
    Node: Analyzes if a YouTube video is involved and needs transcription.
    Triggers: `video_context_analyzer_chain` in runnable.py
    Expects: `question` and `current_url` in AgentState
    """
    current_url = state.get("current_url") or ""
    yt = extract_youtube_url(current_url)
    
    classification = state.get("classification", {})
    primary_intent = classification.get("primary_intent", "")
    
    # Skip LLM if no video detected and not a video intent
    if not yt and primary_intent != "video":
        return {
            "needs_video": False,
            "youtube_url": None
        }

    # Calls LLM to decide if transcript is actually needed for the specific question
    result = video_context_analyzer_chain.invoke({
        "question": state["question"],
        "has_videos": "true" if yt else "false"
    })
    return {
        "needs_video": result.get("needs_video_context", False),
        "youtube_url": yt
    }


def page_context_analyzer(state: AgentState):
    """
    Node: Detects if the user query refers to the content of the active tab.
    Triggers: `context_analyzer_chain` in runnable.py
    Expects: `question` in AgentState
    """
    # If page_context is already present (e.g. from vector store retrieval), use it.
    if state.get("page_context"):
        return {"needs_context": True}

    # Asks LLM if question requires scraping/reading the page
    result = context_analyzer_chain.invoke({
        "question": state["question"]
    })
    return {
        "needs_context": result.get("needs_context", False)
    }


def action_intent_analyzer(state: AgentState):
    """
    Node: Categorizes if the request requires browser automation (clicks, navigation).
    Triggers: `action_intent_chain` in runnable.py
    Expects: `question`, `raw_html`, `current_url`
    """
    result = action_intent_chain.invoke({
        "question": state["question"],
        "has_context": "true" if state.get("raw_html") else "false",
        "current_url": state.get("current_url") or ""
    })
    return {
        "needs_actions": result.get("needs_actions", False)
    }


def intent_classifier(state: AgentState):
    """
    Node: Primary classifier that determines the general intent (shopping, info, tutorial).
    Triggers: `classifier_chain` in runnable.py
    Expects: `question`
    Returns: `classification` dict containing rich content requirements.
    """
    return {
        "classification": classifier_chain.invoke({
            "question": state["question"]
        })
    }


def transcribe_video(state: AgentState):
    """
    Node: Downloads and transcribes YouTube audio if required.
    Triggers: `get_youtube_transcript` in runnable.py (External API: AssemblyAI)
    Expects: `needs_video` flag and `youtube_url` in state.
    """
    if not state.get("needs_video") or not state.get("youtube_url"):
        return {"video_transcripts": []} 

    transcript = get_youtube_transcript(
        state["youtube_url"]
    )

    if transcript.get("transcript"):
        return {"video_transcripts": [transcript]}

    return {"video_transcripts": []}


def parse_html(state: AgentState):
    """
    Node: Sanitizes and truncates raw DOM trees into LLM-friendly text.
    Triggers: `extract_clean_text_from_dom` and `RecursiveCharacterTextSplitter`.
    Expects: `raw_html` (dict from extractDOMTree or string).
    Returns: `page_context` with head metadata and limited body content.
    """
    if not state.get("needs_context") or not state.get("raw_html"):
        return {}

    raw = state["raw_html"]
    parsed = {}
    
    # Logic Step: Handle pre-structured DOM tree from extension
    if isinstance(raw, dict):
        from utils.text_processing import extract_clean_text_from_dom, limit_context
        
        # 1. Extract clean text (Try direct textContent first, then traverse DOM)
        content = raw.get("textContent") or raw.get("content")
        dom_tree = raw.get("domTree")
        
        if not content:
            content = extract_clean_text_from_dom(dom_tree)
        
        # 2. Token Limit: Split and keep only the first 20 chunks (~10k chars) to avoid LLM context overflow
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=100)
        chunks = text_splitter.split_text(content)
        content = "\n\n".join(chunks[:20])
        
        # Metadata extraction for the prompt
        title = raw.get("title") or ""
        metadata = raw.get("metadata")
        
        if isinstance(metadata, dict):
            head = metadata
            if title and not head.get("title"):
                head["title"] = title
        else:
            head = {"title": title or metadata or "N/A", "description": ""}
        
        parsed = {
            "head": head,
            "content": content,
            "dom_tree": dom_tree
        }
    # Logic Step: Fallback for raw HTML string
    else:
        parsed = extract_readable_page(raw)

    return {"page_context": parsed}

def retrieve_context_node(state: AgentState):
    """
    New node to retrieve context from vector store if URL is present but no direct context.
    """
    if not state.get("needs_context") or state.get("page_context"):
        return {}

    current_url = state.get("current_url")
    if not current_url:
        return {}
        
    # We might not have user_id in state easily unless passed...
    # For now, let's assume we can get it or skip.
    # Actually, main.py's graph execution usually happens inside an endpoint where we have user info.
    # But `agent_runnable` is compiled once. 
    # We need to pass user_id in the config or state.
    
    # Let's verify if we can access the user_id from the state. 
    # Current AgentState definition might not have it.
    # For now, let's skip complex retrieval here and do it in the endpoint (`agent_step_endpoint` or `streamChatResponse`) 
    # BEFORE calling the graph, OR add user_id to AgentState.
    
    return {}


def decide_chat_mode(state: AgentState):
    if state.get("page_context") or state.get("video_transcripts"):
        return {"chat_mode": "context"}
    return {"chat_mode": "simple"}


def decide_agent_mode(state: AgentState):
    """
    Decide which explanation chain agent should use
    """
    if state.get("page_context") or state.get("video_transcripts"):
        return {"chat_mode": "context"}
    return {"chat_mode": "simple"}


# ======================================================
# BUILD LANGGRAPH (NO STREAMING)
# ======================================================

graph = StateGraph(AgentState)

graph.add_node("video_analyze", video_analyzer)
graph.add_node("page_analyze", page_context_analyzer)
graph.add_node("action_analyze", action_intent_analyzer)  # NEW NODE
graph.add_node("classify", intent_classifier)
graph.add_node("transcribe", transcribe_video)
graph.add_node("parse_html", parse_html)
graph.add_node("decide", decide_agent_mode)

graph.set_entry_point("classify")

graph.add_edge("classify", "video_analyze")
graph.add_edge("classify", "page_analyze")
graph.add_edge("classify", "action_analyze")

graph.add_conditional_edges(
    "video_analyze",
    lambda state: "transcribe" if state.get("needs_video") and state.get("youtube_url") else "decide",
    {
        "transcribe": "transcribe",
        "decide": "decide"
    }
)

graph.add_edge("transcribe", "decide")
graph.add_edge("page_analyze", "parse_html")
graph.add_edge("parse_html", "decide")
graph.add_edge("action_analyze", "decide")

graph.add_edge("decide", END)

app_graph = graph.compile()


# ======================================================
# FASTAPI APP
# ======================================================

app = FastAPI(title="Context-Aware AI API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class ContextRequest(BaseModel):
    url: str
    title: Optional[str] = None
    raw_html: Optional[dict] = None  # domTree
    conversation_id: Optional[int] = None

@app.post("/context/save")
async def save_context_endpoint(
    req: ContextRequest,
    authorization: Optional[str] = Header(None)
    ):
    """
    Endpoint: Save scraped page context to the Vector Store.
    Triggered by: Frontend extension on tab selection/refresh.
    Expects: URL and raw_html (domTree JSON).
    Functions: `vector_store.process_and_save_context`
    """
    try:
        # Auth: Verify requester identity
        user, db = await get_user_from_token(authorization)
        
        # Logic Step: Prevent redundant embedding if conversation already has this context
        if req.conversation_id:
            if vector_store.has_context(str(user.id), req.url, req.conversation_id):
                return {"status": "skipped", "message": "Already embedded for this tab/conversation"}
        
        # Logic Step: Pre-process DOM into clean text before vectorizing
        from utils.text_processing import extract_clean_text_from_dom
        
        content = ""
        if req.raw_html:
            content = req.raw_html.get("textContent") or req.raw_html.get("content")
            dom_tree = req.raw_html.get("domTree", req.raw_html) 
            
            if not content:
                content = extract_clean_text_from_dom(dom_tree)
        
        if not content:
            return {"status": "skipped", "message": "No content to save"}
            
        # Logic Step: Chunk, embed, and store in vector database (PageChunk + ChatContext tables)
        count = await vector_store.process_and_save_context(
            user_id=str(user.id),
            conversation_id=req.conversation_id,
            url=req.url,
            content=content
        )
        
        return {"status": "success", "chunks_saved": count}
        
    except Exception as e:
        print(f"Context save error: {e}")
        return {"status": "error", "message": str(e)}


# ======================================================
# AUTH FUNCTIONS
# ======================================================

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# ======================================================
# STREAMING ENDPOINT (CORRECT SSE)
# ======================================================
class LoginRequest(BaseModel):
    googleToken: str
    
    class Config:
        extra = "allow"

from db.database import SessionLocal
from db.models.user import User
from sqlalchemy.orm import Session

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        
@app.post("/login")
async def user_login(req: LoginRequest, response: Response):
    """
    Endpoint: Google OAuth Login / User Synchronization.
    Triggered by: Frontend on successful Google login.
    Expects: `googleToken`.
    Functions: Verifies with Google API, Upserts User in SQL DB, issues JWT.
    """
    db = SessionLocal() 
    try:
        # Step 1: Identity Verification via Google
        async with httpx.AsyncClient() as client:
            google_res = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {req.googleToken}"}
            )
            if google_res.status_code != 200:
                return {"status": "error", "message": "Invalid Google token"}
            
            user_data = google_res.json()
            email = user_data.get("email")
            name = user_data.get("name")
            user_dp = user_data.get("picture")

        # Step 2: Database Persistence (Upsert)
        user = db.query(User).filter(User.email == email).first()
        
        if not user:
            # New User registration
            user = User(
                name=name,
                email=email,
                user_dp=user_dp,
                credits=10.0 
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            message = "User created"
        else:
            # Existing User update (sync name/picture)
            user.name = name 
            user.user_dp = user_dp
            db.commit()
            message = "Logged in"

        # Step 3: Session Management
        # Generate our own internal JWT to avoid passing raw Google tokens in headers
        access_token = create_access_token(data={"sub": user.email})
        
        # Set HttpOnly cookie for security (prevent XSS access to session)
        response.set_cookie(
            key="session_token",
            value=access_token,
            httponly=True,
            max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            samesite="lax",
            secure=False 
        )

        return {
            "status": "success", 
            "message": message, 
            "access_token": access_token,
            "user": {
                "id": user.id, 
                "email": user.email, 
                "name": user.name,
                "user_dp": user.user_dp
            }
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


@app.post("/auth/logout")
async def user_logout(response: Response):
    response.delete_cookie("session_token")
    return {"status": "success", "message": "Logged out"}


# ======================================================
# DATA SYNC ENDPOINTS
# ======================================================

from sync_schemas import get_current_user, ConversationSync, NoteSync, ManifestSync, MessageCreate
from db.models.conversation import Conversation
from db.models.message import Message
from db.models.note import Note
from db.models.agent import AgentManifest

async def get_user_from_token(authorization: Optional[str] = Header(None)):
    """Get user from JWT token"""
    email = await get_current_user(authorization)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return user, db
    except Exception as e:
        db.close()
        raise e

@app.post("/sync/conversations")
async def sync_conversations(
    conversations: List[ConversationSync],
    authorization: Optional[str] = Header(None)
):
    """Sync conversations with messages from extension (Deprecated)"""
    # This endpoint is deprecated as the data model has changed. 
    # Clients should migrate to /conversations and /conversations/{id}/messages
    return {"status": "deprecated", "message": "Endpoint deprecated. Please update extension."}


# Start of Auto-Titling and New Conversation Flow

async def generate_conversation_title(conversation_id: int):
    """Background task to generate title for a conversation"""
    db = SessionLocal()
    try:
        # Check if title is already set (optimization)
        conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if not conversation or conversation.title:
            return
        
        # Get first message
        message = db.query(Message).filter(Message.conversation_id == conversation_id).order_by(Message.created_at).first()
        if not message:
            return

        # Generate title
        prompt = f"Generate a very brief title (max 5 words) for this conversation based on this query: {message.user_query}"
        try:
            result = await runnable_chain.ainvoke({"question": prompt})
            title = result.content if hasattr(result, 'content') else str(result)
            title = title.strip('"').strip()
        except Exception:
            title = "New Chat"

        conversation.title = title
        db.commit()
    except Exception as e:
        print(f"Error generating title: {e}")
    finally:
        db.close()

@app.post("/conversations")
async def create_conversation(
    authorization: Optional[str] = Header(None)
):
    """
    Endpoint: Initialize a new chat session.
    Triggered by: Frontend "+" button or first message in a new window.
    Expects: Auth Header.
    Returns: `conversation_id`.
    """
    user, db = await get_user_from_token(authorization)
    try:
        conversation = Conversation(
            user_id=user.id,
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        return {"status": "success", "conversation_id": conversation.id}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()

@app.get("/conversations")
async def get_conversations(
    authorization: Optional[str] = Header(None)
):
    """Get all conversations for the user (limited to 50 most recent)"""
    user, db = await get_user_from_token(authorization)
    try:
        # Limit to 50 most recent conversations for faster loading
        conversations = db.query(Conversation).filter(
            Conversation.user_id == user.id
        ).order_by(Conversation.updated_at.desc()).limit(50).all()
        
        return {
            "status": "success",
            "conversations": [
                {
                    "id": c.id,
                    "title": c.title or "New Chat",
                    "updated_at": c.updated_at.isoformat(),
                    "created_at": c.created_at.isoformat()
                }
                for c in conversations
            ]
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        db.close()

@app.post("/conversations/{conversation_id}/messages")
async def add_message(
    conversation_id: int,
    message_data: MessageCreate,
    background_tasks: BackgroundTasks,
    authorization: Optional[str] = Header(None)
):
    """
    Endpoint: Persist a finished chat turn (User Query + AI Response).
    Triggered by: Frontend after streaming is complete.
    Expects: `user_query`, `ai_response`.
    Logic: Saves message and triggers background auto-titling if needed.
    """
    user, db = await get_user_from_token(authorization)
    try:
        # Step 1: Ownership Verification
        conversation = db.query(Conversation).filter(
            Conversation.id == conversation_id, 
            Conversation.user_id == user.id
        ).first()
        
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

        # Step 2: Persistence
        message = Message(
            conversation_id=conversation.id,
            user_query=message_data.user_query,
            ai_response=message_data.ai_response
        )
        db.add(message)
        db.commit()

        # Step 3: Progressive Enhancement
        # If the conversation is new, generate an intelligent title from the first message
        if not conversation.title:
            background_tasks.add_task(generate_conversation_title, conversation.id)

        return {"status": "success", "message": "Message added"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


@app.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: int,
    authorization: Optional[str] = Header(None)
):
    """Fetch all messages for a specific conversation"""
    user, db = await get_user_from_token(authorization)
    
    try:
        # Verify conversation belongs to user
        conversation = db.query(Conversation).filter(
            Conversation.id == conversation_id,
            Conversation.user_id == user.id
        ).first()
        
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        # Fetch messages
        messages = db.query(Message).filter(
            Message.conversation_id == conversation_id
        ).order_by(Message.created_at).all()
        
        flat_messages = []
        for msg in messages:
            flat_messages.extend([
                {
                    "id": f"{msg.id}_user",
                    "role": "user",
                    "content": msg.user_query,
                    "created_at": msg.created_at.isoformat()
                },
                {
                    "id": f"{msg.id}_ai", 
                    "role": "assistant",
                    "content": msg.ai_response,
                    "created_at": msg.created_at.isoformat()
                }
            ])
            
        return {
            "status": "success",
            "conversation": {
                "id": conversation.id,
                "title": conversation.title,
                "created_at": conversation.created_at.isoformat(),
                "updated_at": conversation.updated_at.isoformat()
            },
            "messages": flat_messages
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


    # finally:
    #     db.close()

# Note Schemas for API
class NoteCreate(BaseModel): 
    title: Optional[str] = None
    content: str
    note_type: str = "general"
    video_url: Optional[str] = None
    video_title: Optional[str] = None
    timestamp: Optional[str] = None
    thumbnail_url: Optional[str] = None

@app.get("/notes")
async def get_notes(
    authorization: Optional[str] = Header(None)
):
    """Get all notes for the user (limited to 100 most recent)"""
    user, db = await get_user_from_token(authorization)
    try:
        # Limit to 100 most recent notes for faster loading
        notes = db.query(Note).filter(
            Note.user_id == user.id
        ).order_by(Note.created_at.desc()).limit(100).all()
        
        return {
            "status": "success",
            "notes": [
                {
                    "id": note.id,
                    "title": note.title,
                    "content": note.content,
                    "note_type": note.note_type,
                    "video_url": note.video_url,
                    "video_title": note.video_title,
                    "timestamp": note.timestamp,
                    "thumbnail_url": note.thumbnail_url,
                    "created_at": note.created_at.isoformat()
                }
                for note in notes
            ]
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        db.close()

# ======================================================
# [NEW] AGENTIC LOOP ENDPOINT
# ======================================================

class AgentStepRequest(BaseModel):
    goal: str
    dom_state: dict
    history: List[dict] = [] # List of {role: "user"|"assistant"|"system", content: "..."}
    current_url: str

@app.post("/agent/step")
async def agent_step_endpoint(req: AgentStepRequest):
    """
    Endpoint: Agent Action Decision Engine.
    Triggered by: Frontend agent loop during automation tasks.
    Expects: `goal`, `dom_state` (JSON), `history`.
    Function: Invokes `agent_runnable` (LangGraph) to decide the next browser action.
    Returns: JSON with next `tool_call` (click, type, etc.) and a status message.
    """
    try:
        # Step 1: State Restoration
        # Reconstruct message history into LangChain objects
        messages = []
        for msg in req.history:
            if msg["role"] == "user":
                messages.append(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                messages.append(AIMessage(content=msg["content"]))
            elif msg["role"] == "system":
                messages.append(SystemMessage(content=msg["content"]))
        
        # Initial goal as the first human message if history is empty
        if not messages:
             messages = [HumanMessage(content=f"Goal: {req.goal}")]

        # Step 2: Planning Inference
        # Function: agent_runnable.ainvoke executes the agent graph logic
        state = {
            "messages": messages,
            "dom_state": req.dom_state,
            "goal": req.goal,
            "current_url": req.current_url
        }
        
        result = await agent_runnable.ainvoke(state)
        last_message = result["messages"][-1]
        
        # Step 3: Tool Extraction
        # Look for structured tool calls (browser actions) in the LLM response
        tool_call = None
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            lc_tool_call = last_message.tool_calls[0]
            tool_call = {
                "name": lc_tool_call["name"],
                "args": lc_tool_call["args"],
                "id": lc_tool_call["id"]
            }
        
        return {
            "status": "success",
            "tool_call": tool_call,
            "message": last_message.content
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

@app.post("/notes")
async def create_note(
    note_data: NoteCreate,
    authorization: Optional[str] = Header(None)
):
    """Create a single note"""
    user, db = await get_user_from_token(authorization)
    try:
        note = Note(
            user_id=user.id,
            title=note_data.title,
            content=note_data.content,
            note_type=note_data.note_type,
            video_url=note_data.video_url,
            video_title=note_data.video_title,
            timestamp=note_data.timestamp,
            thumbnail_url=note_data.thumbnail_url
        )
        db.add(note)
        db.commit()
        db.refresh(note)
        return {"status": "success", "note_id": note.id, "message": "Note created"}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()

@app.delete("/notes/{note_id}")
async def delete_note(
    note_id: int,
    authorization: Optional[str] = Header(None)
):
    """Delete a note"""
    user, db = await get_user_from_token(authorization)
    try:
        note = db.query(Note).filter(
            Note.id == note_id,
            Note.user_id == user.id
        ).first()
        
        if not note:
            raise HTTPException(status_code=404, detail="Note not found")
            
        db.delete(note)
        db.commit()
        return {"status": "success", "message": "Note deleted"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()

# Deprecated Sync Endpoint - kept for backward compatibility if needed, but redundant now
@app.post("/sync/notes")
async def sync_notes(
    notes: List[NoteSync],
    authorization: Optional[str] = Header(None)
):
    """Sync notes from extension"""
    user, db = await get_user_from_token(authorization)
    
    try:
        synced_count = 0
        for note_data in notes:
            # Check if note already exists (deduplication)
            existing_note = db.query(Note).filter(
                Note.user_id == user.id,
                Note.content == note_data.content,
                Note.timestamp == note_data.timestamp
            ).first()

            if existing_note:
                continue

            note = Note(
                user_id=user.id,
                title=note_data.title,
                content=note_data.content,
                note_type=note_data.note_type,
                video_url=note_data.video_url,
                video_title=note_data.video_title,
                timestamp=note_data.timestamp,
                thumbnail_url=note_data.thumbnail_url,
                created_at=datetime.fromisoformat(note_data.created_at) if note_data.created_at else datetime.utcnow()
            )
            db.add(note)
            synced_count += 1
        
        db.commit()
        return {"status": "success", "synced": synced_count}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()

@app.post("/sync/manifests")
async def sync_manifests(
    manifests: List[ManifestSync],
    authorization: Optional[str] = Header(None)
):
    """Sync agent manifests from extension"""
    user, db = await get_user_from_token(authorization)
    
    try:
        synced_count = 0
        for manifest_data in manifests:
            manifest = AgentManifest(
                user_id=user.id,
                query=manifest_data.query,
                manifest_data=manifest_data.manifest_data,
                created_at=datetime.fromisoformat(manifest_data.created_at) if manifest_data.created_at else datetime.utcnow()
            )
            db.add(manifest)
            synced_count += 1
        
        db.commit()
        return {"status": "success", "synced": synced_count}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


# ======================================================
# MEDIA MANAGEMENT ENDPOINTS
# ======================================================

from db.models.media import Media
from utils.r2_storage import r2_storage
import base64

class MediaUpload(BaseModel):
    file_data: str  # Base64 encoded file
    filename: str
    file_type: str  # 'image', 'pdf', 'docx', etc.
    source: str = "uploaded"  # 'uploaded', 'circle_search', 'snapshot', 'generated'
    file_metadata: Optional[dict] = None

@app.post("/media/upload")
async def upload_media(
    media_data: MediaUpload,
    authorization: Optional[str] = Header(None)
):
    """Upload media file to R2 and save to database"""
    user, db = await get_user_from_token(authorization)
    
    try:
        # Decode base64 file data
        file_bytes = base64.b64decode(media_data.file_data)
        file_size = len(file_bytes)
        
        # Determine folder based on source
        folder_map = {
            "uploaded": "uploads",
            "circle_search": "circle-search",
            "snapshot": "snapshots",
            "generated": "generated"
        }
        folder = folder_map.get(media_data.source, "uploads")
        
        # Upload to R2
        success, file_url, error_msg = r2_storage.upload_file(
            file_bytes=file_bytes,
            filename=media_data.filename,
            folder=folder
        )
        
        if not success:
            return {"status": "error", "message": error_msg}
        
        # Save to database
        media = Media(
            user_id=user.id,
            file_type=media_data.file_type,
            source=media_data.source,
            file_url=file_url,
            original_filename=media_data.filename,
            file_size_bytes=file_size,
            file_metadata=media_data.file_metadata
        )
        db.add(media)
        db.commit()
        db.refresh(media)
        
        return {
            "status": "success",
            "media_id": media.id,
            "file_url": file_url,
            "message": "Media uploaded successfully"
        }
    
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()

@app.get("/media")
async def get_media(
    authorization: Optional[str] = Header(None),
    limit: int = 100,
    file_type: Optional[str] = None
):
    """Get all media for the user (limited to most recent)"""
    user, db = await get_user_from_token(authorization)
    
    try:
        query = db.query(Media).filter(Media.user_id == user.id)
        
        # Filter by file type if specified
        if file_type:
            query = query.filter(Media.file_type == file_type)
        
        # Order by most recent and limit
        media_items = query.order_by(Media.created_at.desc()).limit(limit).all()
        
        return {
            "status": "success",
            "media": [
                {
                    "id": m.id,
                    "file_type": m.file_type,
                    "source": m.source,
                    "file_url": m.file_url,
                    "thumbnail_url": m.thumbnail_url,
                    "original_filename": m.original_filename,
                    "file_size_bytes": m.file_size_bytes,
                    "file_metadata": m.file_metadata,
                    "created_at": m.created_at.isoformat()
                }
                for m in media_items
            ]
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        db.close()

@app.delete("/media/{media_id}")
async def delete_media(
    media_id: int,
    authorization: Optional[str] = Header(None)
):
    """Delete media from R2 and database"""
    user, db = await get_user_from_token(authorization)
    
    try:
        # Find media item
        media = db.query(Media).filter(
            Media.id == media_id,
            Media.user_id == user.id
        ).first()
        
        if not media:
            raise HTTPException(status_code=404, detail="Media not found")
        
        # Delete from R2
        success, error_msg = r2_storage.delete_file(media.file_url)
        if not success:
            print(f"Warning: Failed to delete from R2: {error_msg}")
            # Continue with DB deletion even if R2 deletion fails
        
        # Delete from database
        db.delete(media)
        db.commit()
        
        return {"status": "success", "message": "Media deleted successfully"}
    
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()




# ======================================================
# MESSAGE PERSISTENCE HELPER
# ======================================================

async def save_message_and_summary(conversation_id: int, user_query: str, ai_response: str):
    """Background task to save message and update summary"""
    if not conversation_id:
        return

    db = SessionLocal()
    try:
        # 1. Save Message
        message = Message(
            conversation_id=conversation_id,
            user_query=user_query,
            ai_response=ai_response
        )
        db.add(message)
        
        # 2. Update Summary (Simple concatenation for now, or LLM based later)
        conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if conversation:
            # If no title, generate one
            if not conversation.title:
                conversation.title = user_query[:50] # Simple fallback
            
            # Append to summary or create new
            # For this task, user wants "in conversation a summary of whole convo"
            # We can just append the last interaction for now or re-summarize
            # Let's keep it simple: Append Q&A to a running transcript in summary
            # OR better: Just save the intent/topic as summary. 
            # The requirement "summary of the whole convo" implies we might need to re-run summarization.
            # optimizing: Just saving the last interaction as a "latest activity" summary for now
            # Real summarization would be an expensive LLM call.
            
            # Let's simple use the last user query as a quick summary update or append it.
            current_summary = conversation.summary or ""
            new_entry = f"User: {user_query}\nAI: {ai_response[:100]}..."
            conversation.summary = (current_summary + "\n" + new_entry).strip()
            
            conversation.updated_at = datetime.utcnow()
            
        db.commit()
    except Exception as e:
        print(f"❌ Error saving message: {e}")
        db.rollback()
    finally:
        db.close()


# @app.post("/generate/stream")
# async def generate_stream(req: GenerateRequest, background_tasks: BackgroundTasks,authorization: Optional[str] = Header(None)):

#     async def stream():
#         full_response = ""
#         # print("req", req.context)
        
        
#         # 0️⃣ PREPARE CONTEXT (VECTOR STORE)
#         try:
#             user, _ = await get_user_from_token(authorization)
#             user_id = str(user.id)
#         except:
#             user_id = "default_user"
        
        
#         # 0️⃣ PREPARE CONTEXT (VECTOR STORE)
#         try:
#             user, _ = await get_user_from_token(authorization)
#             user_id = str(user.id)
#         except:
#             user_id = "default_user"
        
#         # A. Retrieval: Get relevant context from Vector DB
#         # We search with the user's prompt
#         retrieved_context = vector_store.get_relevant_context(
#             user_id=user_id,
#             query=req.prompt,
#             conversation_id=req.conversation_id,  # ✅ Primary lookup
#             current_url=req.current_url  # ✅ Fallback
#         )
        
        
#         # B. Current Page Context (Optional/Implicit)
#         # If we still want to support immediate page context if provided (e.g. for fallback), 
#         # but the USER request said "without sending context to backend on tab selection".
#         # Actually, they said "hit an api which will save the page context on tab selection". 
#         # So we should rely on retrieval.
#         print(f"📊 Context retrieval for conversation {req.conversation_id}, URL: {req.current_url}")
#         print(f"📊 Retrieved {len(retrieved_context)} chars of context")

#         asks_about_current_page = any(word in req.prompt.lower() for word in [
#             'this', 'current', 'here', 'page', 'these', 'above', 'product', 'item'
#         ])
        
#         if asks_about_current_page and not retrieved_context:
#             yield json.dumps({
#                 "type": "error",
#                 "data": "No context available for this page. Please save the page context first by switching tabs or refreshing."
#             }) + "\n\n"
#             yield json.dumps({"type": "done"}) + "\n\n"
#             return


#         final_context_text = ""
#         if retrieved_context:
#             final_context_text += f"\n\n[RETRIEVED CONTEXT]:\n{retrieved_context}"
        
#         # If frontend still sends context for some reason, we could use it, 
#         # but we should definitely remove the saving part.
#         if req.context and not retrieved_context:
#              # Fallback if retrieval is empty but context was sent (not expected after frontend change)
#              from utils.text_processing import extract_clean_text_from_dom
#              raw = req.context
#              if isinstance(raw, dict):
#                 content = raw.get("textContent") or raw.get("content")
#                 if not content:
#                     content = extract_clean_text_from_dom(raw.get("domTree"))
#                 final_context_text += f"\n\n[PAGE CONTENT]:\n{content[:8000]}"
#              else:
#                 final_context_text += f"\n\n[PAGE CONTENT]:\n{str(raw)[:8000]}"

#         context_payload = {
#             "content": final_context_text,
#             "title": "Context",
#             "metadata": {"source": "vector_db" if retrieved_context else "direct"}
#         }
#         # print("final_context_text", final_context_text)
#         # 1️⃣ RUN GRAPH ONCE (NO STREAMING)
#         state = await app_graph.ainvoke({
#             "question": req.prompt,
#             "image_url": req.image_url,
#             "raw_html": final_context_text, # logic changed to use pre-processed context
#             "current_url": req.current_url,
#         })
#         print("DEBUG raw_html:", state.get("raw_html")[:200] if state.get("raw_html") else None)
#         print("DEBUG page_context:", state.get("page_context"))
#         print("DEBUG needs_context:", state.get("needs_context"))

#         # Send video analysis result
#         if state.get("needs_video"):
#             yield json.dumps({
#                 "type": "video_analysis",
#                 "data": {
#                     "needs_video_context": True,
#                     "reason": "Video content detected"
#                 }
#             }) + "\n\n"
            
#             # Send transcription status if we have transcripts
#             if state.get("video_transcripts"):
#                 yield json.dumps({
#                     "type": "status",
#                     "data": "Video transcribed successfully"
#                 }) + "\n\n"

#         # 2️⃣ PICK CHAIN
#         if state.get("chat_mode") == "context" or req.image_url:
#             chain = create_context_aware_chain(
#                 page_context=state.get("page_context"),
#                 use_context=state.get("needs_context"),
#                 video_transcripts=state.get("video_transcripts"),
#                 image_url=req.image_url
#             )
#         else:
#             chain = runnable_chain

#         # 3️⃣ STREAM TOKENS
#         async for msg in chain.astream({"question": req.prompt}):
#             if msg and msg.content:
#                 yield json.dumps({
#                     "type": "text",
#                     "data": msg.content
#                 }) + "\n\n"
#                 full_response += msg.content

#         # 4️⃣ RICH CONTENT (OPTIONAL)
#         cls = state.get("classification", {})
#         if cls.get("needs_rich_content"):
#             rich = rich_content_chain.invoke({
#                 "question": req.prompt,
#                 "content_types": cls.get("content_types", []),
#                 "primary_intent": cls.get("primary_intent", "info"),
#             })
#             yield json.dumps({
#                 "type": "rich_blocks",
#                 "data": rich
#             }) + "\n\n"

#         # 5️⃣ DONE
#         yield json.dumps({"type": "done"}) + "\n\n"
        
#         # 6️⃣ SAVE TO DB
#         if req.conversation_id:
#             background_tasks.add_task(
#                 save_message_and_summary, 
#                 req.conversation_id, 
#                 req.prompt, 
#                 full_response
#             )

#     return StreamingResponse(
#         stream(),
#         media_type="text/event-stream",
#         headers={
#             "Cache-Control": "no-cache",
#             "Connection": "keep-alive",
#             "X-Accel-Buffering": "no",
#         },
#     )

SIMILARITY_THRESHOLD = 0.15

async def get_memory_context(user_id: str, conversation_id: int, query_embedding: list[float]):

    db = SessionLocal()

    try:

        stmt = (
            select(QueryHistory)
            .where(QueryHistory.user_id == user_id)
            .where(QueryHistory.conversation_id == conversation_id)
            .order_by(
                QueryHistory.query_embedding.cosine_distance(query_embedding)
            )
            .limit(5)
        )

        results = db.execute(stmt).scalars().all()

        memory = ""

        for chat in results:
            memory += f"User: {chat.query}\nAssistant: {chat.response}\n\n"

        return memory

    finally:
        db.close()

async def get_similar_chat(user_id: str, query: str):

    query_embedding = await embed_text(query)

    db: Session = SessionLocal()

    try:

        stmt = (
            select(
                QueryHistory,
                QueryHistory.query_embedding.cosine_distance(query_embedding).label("distance")
            )
            .where(QueryHistory.user_id == user_id)
            .order_by("distance")
            .limit(1)
        )

        result = db.execute(stmt).first()

        if result:
            chat, distance = result

            if distance < SIMILARITY_THRESHOLD:
                return chat.response

        return None

    finally:
        db.close()
async def save_chat(
    user_id: str,
    url: str,
    query: str,
    response: str
):

    combined_text = f"Query: {query}\nResponse: {response}"
    combined_embedding = await embed_text(combined_text)

    db: Session = SessionLocal()

    try:

        chat = QueryHistory(
            user_id=user_id,
            url=url,
            query=query,
            query_embedding=combined_embedding,
            response=response,
            response_embedding=combined_embedding
        )

        db.add(chat)
        db.commit()

    finally:
        db.close()




# ============================================================
# generate_stream endpoint
# ============================================================

# Keywords that indicate the user is asking about the current page

# Keywords that indicate the user is asking specifically about the context of the active browser tab.
# This heuristic prevents unnecessary Vector DB lookups for general talk (e.g., "Hi", "Tell me a joke").
PAGE_CONTEXT_KEYWORDS = [
    "this", "current", "here", "page", "these", "above",
    "product", "item", "summarize", "summarise", "summary", "explain this",
    "what does it say", "on this", "listed", "shown", "describe",
    "content", "contents", "page contents", "the content",
    "the contents", "material", "text", "texts", "the text",
    "information", "details", "data", "info",
    "this page", "this content", "this text", "this material",
    "this info", "this information", "this thing",
    "that page", "that content", "that text",
    "the above", "above content", "above text",
    "below", "below content", "this one", "that one",
    "the previous", "the next",
    "summarize this", "summarise this", "explain this",
    "explain it", "describe this", "describe it",
    "tell me about this", "tell me about it",
    "what is this", "what's this",
    "what does this say", "what does it say",
    "give summary", "give a summary",
    "brief this", "analyze this", "analyse this",
    "this product", "this item", "this listing",
    "the product", "the item", "the listing",
    "product details", "item details",
    "it", "this one", "that one", "the above one",
    "shown here", "shown above", "shown below",
    "on the page", "from the page",
    "from here", "from this page",
    "read this", "read it", "interpret this",
    "what is written", "what's written",
    "what is mentioned", "what's mentioned",
    "what is described", "what's described",
]

def likely_page_context(query: str) -> bool:
    """
    Step: Intent detection for RAG (Retrieval-Augmented Generation).
    Function: Scans user query for demonstrative pronouns and page-related verbs.
    Expects: `query` string.
    Returns: bool (True if context should be fetched).
    """
    q = query.lower()

    # Case 1: Direct keyword hit
    if any(k in q for k in PAGE_CONTEXT_KEYWORDS):
        return True

    # Case 2: Short, vague queries like "summarize it" or "explain this"
    if len(q.split()) <= 4 and any(
        word in q for word in ["this", "it", "above", "here"]
    ):
        return True

    return False


@app.post("/generate/stream")
async def generate_stream(
    req: GenerateRequest,
    background_tasks: BackgroundTasks,
    authorization: Optional[str] = Header(None)
    ):
    """
    Primary API: Real-time Streaming Chat with context awareness.
    Triggered by: Extension "Send" button.
    Expects: `GenerateRequest` (prompt, context, history, models).
    Returns: SSE stream of JSON frames (text, analysis, done).
    """

    async def stream():
        full_response = ""

        # Step 1: Identity & Authorization
        try:
            user, _ = await get_user_from_token(authorization)
            user_id = str(user.id)
        except:
            user_id = "default_user"

        # Step 2: Memory Alignment (Windowing)
        # Keeps last 20 messages for context, preventing prompt size explosion
        raw_history = req.history or []
        windowed_history = raw_history[-20:] if len(raw_history) > 20 else raw_history

        chat_history = []
        for msg in windowed_history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "user":
                chat_history.append(HumanMessage(content=content))
            elif role in ["assistant", "ai", "bot"]:
                chat_history.append(AIMessage(content=content))

        # Step 3: Lazy RAG Retrieval
        retrieved_context = ""
        prompt_lower = req.prompt.lower()
        
        # Only query Vector DB if user specifically asks about the page
        asks_about_current_page = any(keyword in prompt_lower for keyword in PAGE_CONTEXT_KEYWORDS)

        if asks_about_current_page and (req.current_url or req.conversation_id):
            # Function: Cosine similarity search in ChromaDB
            retrieved_context = vector_store.get_relevant_context(
                user_id=user_id,
                query=req.prompt,
                conversation_id=req.conversation_id,
                current_url=req.current_url
            )
            print(f"📊 Retrieved {len(retrieved_context)} chars of page context")

        # Step 4: Early Exit for Missing Context
        if asks_about_current_page and not retrieved_context:
            yield json.dumps({
                "type": "text",
                "data": "I don't have access to this page yet. Please refresh or describe it."
            }) + "\n\n"
            yield json.dumps({"type": "done"}) + "\n\n"
            return

        # Step 5: Chain Orchestration
        # Priority: current image_url wins → vision chain
        # Then: search history for an image (vision follow-up)
        # Then: retrieved_context → context-aware chain
        # Fallback: plain runnable_chain
        
        effective_image_url = req.image_url
        
        # Check if this is a follow-up in a vision session
        if not effective_image_url and req.history:
            for msg in reversed(req.history):
                if msg.get("imageUrl"):
                    effective_image_url = msg.get("imageUrl")
                    print(f"👁️ Found image in history — resuming vision session")
                    break

        if effective_image_url:
            print(f"🖼️ Vision active (model: qwen3.5:2b)")
            chain = create_context_aware_chain(
                page_context=None,
                use_context=False,
                video_transcripts=None,
                image_url=effective_image_url
            )
        elif retrieved_context:
            chain = create_context_aware_chain(
                page_context={
                    "head": {"title": "Page", "description": ""},
                    "content": retrieved_context
                },
                use_context=True,
                video_transcripts=None,
                image_url=None
            )
        else:
            chain = runnable_chain

        # Step 6: Token Streaming (Modern SSE)
        # Function: astream() sends LLM tokens as they arrive
        async for msg in chain.astream({
            "question": req.prompt,
            "chat_history": chat_history
        }, config={"configurable": {"model": req.model}}):
            if msg and msg.content:
                full_response += msg.content
                yield json.dumps({
                    "type": "text",
                    "data": msg.content
                }) + "\n\n"

        yield json.dumps({"type": "done"}) + "\n\n"

        # Step 7: Post-Chat Persistence
        # Fire-and-forget task to save the turn to the main SQL database
        if req.conversation_id:
            background_tasks.add_task(
                save_message_and_summary,
                req.conversation_id,
                req.prompt,
                full_response
            )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
# AGENT MODE ENDPOINT (FIXED - NOW SENDS INTENT ANALYSIS)
# -------------------------

async def run_agent_actions(prompt, primary_intent, model="openai"):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: agent_chain.invoke(
            {"question": prompt, "primary_intent": primary_intent},
            config={"configurable": {"model": model}}
        )
    )

async def run_dom_actions(prompt, page_context, model="openai"):
    loop = asyncio.get_running_loop()
    dom_context = format_dom_for_llm(page_context.get("dom_tree"))

    return await loop.run_in_executor(
        None,
        lambda: dom_action_chain.invoke(
            {"question": prompt, "dom_context": dom_context},
            config={"configurable": {"model": model}}
        )
    )


async def run_rich_content(prompt, content_types, primary_intent, model="openai"):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: rich_content_chain.invoke(
            {"question": prompt, "content_types": content_types, "primary_intent": primary_intent},
            config={"configurable": {"model": model}}
        )
    )




@app.post("/agent/stream")
async def agent_stream(
    req: GenerateRequest, 
    background_tasks: BackgroundTasks,
    authorization: Optional[str] = Header(None)
):

    async def stream():
        full_response = ""
        try:
            # 0️⃣ PREPARE CONTEXT (VECTOR STORE)
            try:
                user, _ = await get_user_from_token(authorization)
                user_id = str(user.id)
            except:
                user_id = "default_user"

            retrieved_context = vector_store.get_relevant_context(
                user_id, 
                req.prompt, 
                req.conversation_id
            )

            final_context_text = ""
            if req.context:
                from utils.text_processing import extract_clean_text_from_dom
                raw = req.context
                if isinstance(raw, dict):
                    content = raw.get("textContent") or raw.get("content")
                    if not content:
                        content = extract_clean_text_from_dom(raw.get("domTree"))
                    final_context_text += f"\n\n[CURRENT PAGE CONTENT]:\n{content[:8000]}"
                else:
                    final_context_text += f"\n\n[CURRENT PAGE CONTENT]:\n{str(raw)[:8000]}"
            
            if retrieved_context:
                final_context_text += f"\n\n[RELEVANT RETRIEVED CONTEXT]:\n{retrieved_context}"
                
            context_payload = {
                "content": final_context_text,
                "title": "Context",
                "metadata": {"source": "mixed"}
            }

            # 1️⃣ RUN GRAPH ONCE (PLANNING)
            # ======================================================
            state = await app_graph.ainvoke({
                "question": req.prompt,
                "raw_html": context_payload,
                "current_url": req.current_url,
            }, config={"configurable": {"model": req.model}})

            classification = state.get("classification", {})
            primary_intent = classification.get("primary_intent", "info")
            content_types = classification.get("content_types", [])
            needs_actions = state.get("needs_actions", False)

            # ======================================================
            # 2️⃣ SEND INTENT ANALYSIS
            # ======================================================
            yield json.dumps({
                "type": "intent_analysis",
                "data": {
                    "needs_actions": needs_actions,
                    "action_type": "navigation" if needs_actions else "content_analysis",
                    "reason": (
                        "Browser actions needed"
                        if needs_actions
                        else "Analyzing content only"
                    )
                }
            }) + "\n\n"

            # ======================================================
            # 3️⃣ VIDEO ANALYSIS STATUS
            # ======================================================
            if state.get("needs_video"):
                yield json.dumps({
                    "type": "video_analysis",
                    "data": {
                        "needs_video_context": True,
                        "reason": "Video content detected"
                    }
                }) + "\n\n"

                if state.get("video_transcripts"):
                    yield json.dumps({
                        "type": "status",
                        "data": "Video transcribed successfully"
                    }) + "\n\n"

            # ======================================================
            # 4️⃣ CHOOSE EXPLANATION CHAIN
            # ======================================================
            has_page_context = bool(state.get("page_context"))
            has_video_transcripts = bool(state.get("video_transcripts"))

            if has_page_context or has_video_transcripts:
                explain_chain_used = create_context_aware_chain(
                    page_context=state.get("page_context"),
                    use_context=has_page_context,
                    video_transcripts=state.get("video_transcripts"),
                )
            else:
                explain_chain_used = explain_chain

            # ======================================================
            # 5️⃣ START BACKGROUND TASKS
            # ======================================================
            actions_task = None
            dom_actions_task = None
            rich_task = None

            if needs_actions:
                # 🌐 Navigation-level actions
                actions_task = asyncio.create_task(
                    run_agent_actions(req.prompt, primary_intent, model=req.model)
                )

                # 🖱️ DOM-level actions (ONLY if page context exists)
                if state.get("page_context"):
                    dom_actions_task = asyncio.create_task(
                        run_dom_actions(req.prompt, state["page_context"], model=req.model)
                    )

            if classification.get("needs_rich_content"):
                rich_task = asyncio.create_task(
                    run_rich_content(req.prompt, content_types, primary_intent, model=req.model)
                )

            actions_sent = False
            dom_actions_sent = False
            rich_sent = False

            # ======================================================
            # 6️⃣ STREAM EXPLANATION TOKENS
            # ======================================================
            raw_history = req.history or []
            windowed_history = raw_history[-10:] if len(raw_history) > 10 else raw_history
            chat_history = []
            for msg in windowed_history:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "user":
                    chat_history.append(HumanMessage(content=content))
                elif role in ["assistant", "ai", "bot"]:
                    chat_history.append(AIMessage(content=content))

            async for msg in explain_chain_used.astream({
                "question": req.prompt,
                "chat_history": chat_history
            }, config={"configurable": {"model": req.model}}):
                if msg and msg.content:
                    yield json.dumps({
                        "type": "text",
                        "data": msg.content
                    }) + "\n\n"
                    full_response += msg.content

                # 🚀 Send navigation actions ASAP
                if actions_task and not actions_sent and actions_task.done():
                    actions_response = await actions_task
                    actions = actions_response.get("actions", [])
                    for action in actions:
                        action["auto"] = True

                    yield json.dumps({
                        "type": "actions",
                        "data": actions
                    }) + "\n\n"

                    actions_sent = True

                # 🖱️ Send DOM actions ASAP
                if dom_actions_task and not dom_actions_sent and dom_actions_task.done():
                    dom_response = await dom_actions_task
                    dom_actions = dom_response.get("actions", [])

                    if dom_actions:
                        yield json.dumps({
                            "type": "dom_actions",
                            "data": dom_actions
                        }) + "\n\n"

                    dom_actions_sent = True

                # 🧩 Send rich content ASAP
                if rich_task and not rich_sent and rich_task.done():
                    rich = await rich_task
                    yield json.dumps({
                        "type": "rich_blocks",
                        "data": rich
                    }) + "\n\n"

                    rich_sent = True

            # ======================================================
            # 7️⃣ FINAL SAFETY SENDS
            # ======================================================
            if actions_task and not actions_sent:
                actions_response = await actions_task
                actions = actions_response.get("actions", [])
                for action in actions:
                    action["auto"] = True

                yield json.dumps({
                    "type": "actions",
                    "data": actions
                }) + "\n\n"

            if dom_actions_task and not dom_actions_sent:
                dom_response = await dom_actions_task
                dom_actions = dom_response.get("actions", [])
                if dom_actions:
                    yield json.dumps({
                        "type": "dom_actions",
                        "data": dom_actions
                    }) + "\n\n"

            if rich_task and not rich_sent:
                rich = await rich_task
                yield json.dumps({
                    "type": "rich_blocks",
                    "data": rich
                }) + "\n\n"

            # ======================================================
            # 8️⃣ DONE
            # ======================================================
            yield json.dumps({"type": "done"}) + "\n\n"

            # 9️⃣ SAVE TO DB
            if req.conversation_id:
                background_tasks.add_task(
                    save_message_and_summary, 
                    req.conversation_id, 
                    req.prompt, 
                    full_response
                )

        except Exception as e:
            yield json.dumps({
                "type": "error",
                "data": str(e)
            }) + "\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

# -------------------------
# URL PREVIEW ENDPOINT
# -------------------------
@app.post("/preview")
async def preview(data: dict):
    url = data["url"]

    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.get(url, follow_redirects=True)

    soup = BeautifulSoup(res.text, "html.parser")

    def og(prop):
        tag = soup.find("meta", property=prop)
        return tag["content"] if tag else None

    return {
        "url": url,
        "title": og("og:title") or soup.title.string if soup.title else url,
        "description": og("og:description"),
        "image": og("og:image"),
        "site": og("og:site_name")
    }

# Add this endpoint to main.py

@app.post("/fill-form-from-chat")
async def fill_form_from_chat(data: dict):
    """
    Extract form field values from natural language user message
    Example: "Fill form with name John Smith, email john@example.com, phone 555-0123"
    """
    try:
        user_message = data.get("user_message", "")
        form_fields = data.get("form_fields", [])
        form_url = data.get("form_url", "")
        
        if not form_fields:
            return {"success": False, "error": "No form fields provided"}
        
        if not user_message:
            return {"success": False, "error": "No user message provided"}
        
        # Build form description
        field_descriptions = []
        for field in form_fields:
            label = field.get("label", "")
            field_type = field.get("type", "text")
            required = "REQUIRED" if field.get("required") else "optional"
            placeholder = field.get("placeholder", "")
            
            desc = f"- {label}: {field_type} ({required})"
            if placeholder:
                desc += f" [placeholder: {placeholder}]"
            field_descriptions.append(desc)
        
        form_desc = "\n".join(field_descriptions)
        
        # Build extraction prompt
        prompt = f"""You are a form-filling AI assistant. Extract field values from the user's message and match them to form fields.

USER MESSAGE:
"{user_message}"

FORM FIELDS TO FILL:
{form_desc}

EXTRACTION RULES:
1. Extract all personal information from the user's message
2. Match extracted info to form field labels intelligently
3. Handle variations (e.g., "name" matches "Full Name", "Your Name", "Name")
4. For emails: extract email addresses
5. For phones: extract phone numbers (preserve format)
6. For addresses: extract address components
7. If a field is mentioned but no value given, use a realistic placeholder
8. If a field is not mentioned and not required, you can skip it
9. Return ONLY valid JSON, no markdown, no explanations

IMPORTANT: 
- Field labels are case-sensitive in the output
- Use the EXACT label names from the form fields above
- If user says "name is John Smith", match it to the "Name" field (or whatever the exact label is)

Example:
User: "Fill with name John Smith, email john@test.com, phone 555-1234"
Output:
{{
  "Name": "John Smith",
  "Email": "john@test.com",  
  "Phone": "555-1234"
}}

NOW EXTRACT VALUES AND RETURN ONLY JSON:"""

        # Call LLM
        result = runnable_chain.invoke({"question": prompt})
        
        # Extract JSON from response
        response_text = result.content if hasattr(result, 'content') else str(result)
        
        # Remove markdown code blocks if present
        response_text = response_text.replace('```json', '').replace('```', '').strip()
        
        # Try to parse JSON
        import re
        json_match = re.search(r'\{[\s\S]*\}', response_text)
        if json_match:
            filled_values = json.loads(json_match.group(0))
            
            # Validate that keys match form field labels
            valid_labels = {field.get("label") for field in form_fields}
            filtered_values = {k: v for k, v in filled_values.items() if k in valid_labels}
            
            return {
                "success": True,
                "filled_values": filtered_values,
                "form_url": form_url,
                "extracted_count": len(filtered_values)
            }
        else:
            return {
                "success": False,
                "error": "Could not parse AI response as JSON",
                "raw_response": response_text[:500]
            }
            
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/fill-form-ai")
async def fill_form_ai(data: dict):
    """
    AI-powered form filling endpoint
    Receives user details and form structure, returns filled values
    """
    try:
        user_details = data.get("user_details", "")
        form_fields = data.get("form_fields", [])
        form_url = data.get("form_url", "")
        
        if not form_fields:
            return {"error": "No form fields provided"}
        
        # Build form description
        field_descriptions = []
        for field in form_fields:
            label = field.get("label", "")
            field_type = field.get("type", "text")
            required = "REQUIRED" if field.get("required") else "optional"
            placeholder = field.get("placeholder", "")
            
            desc = f"- {label}: {field_type} ({required})"
            if placeholder:
                desc += f" [placeholder: {placeholder}]"
            field_descriptions.append(desc)
        
        form_desc = "\n".join(field_descriptions)
        
        # Build prompt for Claude
        prompt = f"""You are helping fill out a web form with the user's personal information.

USER DETAILS:
{user_details}

FORM FIELDS:
{form_desc}

INSTRUCTIONS:
1. Extract relevant information from user details
2. Match user info to form fields appropriately
3. Generate realistic values for any missing fields
4. Return ONLY a JSON object with field labels as keys
5. Ensure email format is valid, phone has proper format
6. For required fields, always provide a value

Example output:
{{
  "Name": "John Smith",
  "Email": "john.smith@example.com",
  "Phone": "+1-555-0123"
}}

Return PURE JSON only, no markdown or explanations."""

        # Call LLM
        result = runnable_chain.invoke({"question": prompt})
        
        # Extract JSON from response
        response_text = result.content if hasattr(result, 'content') else str(result)
        
        # Try to parse JSON
        import re
        json_match = re.search(r'\{[\s\S]*\}', response_text)
        if json_match:
            filled_values = json.loads(json_match.group(0))
            return {
                "success": True,
                "filled_values": filled_values,
                "form_url": form_url
            }
        else:
            return {
                "success": False,
                "error": "Could not parse AI response",
                "raw_response": response_text[:500]
            }
            
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/get-stored-forms")
async def get_stored_forms():
    """
    Endpoint to retrieve all stored form data
    Useful for AI context about what forms user has encountered
    """
    # This would be called from extension with localStorage data
    # Return any server-side stored form templates if needed
    return {
        "message": "Send form data from localStorage via request body",
        "example": {
            "forms": {},
            "values": {}
        }
    }


class ManifestRequest(BaseModel):
    prompt: str

@app.post("/agent/generate-manifest")
async def generate_manifest(req: ManifestRequest):
    """
    Generate a browsing agent executable manifest OR a chat response.
    Streams events: "status", "text", "manifest", "done".
    """
    async def stream():
        try:
            # 1. Classify Intent
            classification = await classifier_chain.ainvoke({"question": req.prompt})
            
            # Use heuristics to detect if action is needed
            # The classifier_chain returns { "needs_rich_content": bool, "content_types": [], "primary_intent": "..." }
            # We can also use action_intent_chain if available, but classifier is handy.
            
            primary_intent = classification.get("primary_intent", "info")
            content_types = classification.get("content_types", [])
            
            # Simple heuristic: If intent is "video", "product", "visual" OR needs rich content, 
            # OR if the user explicitly asks for a plan/action, TREAT AS AGENT REQUEST.
            # But the user asked: "if normal text -> chat, if actions -> manifest".
            
            is_agent_task = False
            if primary_intent in ["product", "visual", "travel"] or "youtube" in content_types:
                is_agent_task = True
            
            # Also check for explicit action keywords not covered by classifier
            action_keywords = ["plan", "book", "find", "search", "scrape", "buy", "compare"]
            if any(k in req.prompt.lower() for k in action_keywords):
                is_agent_task = True
                
            if is_agent_task:
                # === AGENT MODE ===
                yield json.dumps({"type": "status", "data": "🤔 Analyzing your request..."}) + "\n\n"
                await asyncio.sleep(0.5)
                
                yield json.dumps({"type": "status", "data": "⚙️  Identifying necessary browser actions..."}) + "\n\n"
                await asyncio.sleep(0.5)
                
                yield json.dumps({"type": "status", "data": "📝 Constructing execution manifest..."}) + "\n\n"
                
                # Stream the manifest JSON token by token
                async for chunk in manifest_stream_chain.astream({"query": req.prompt}):
                    if chunk and chunk.content:
                        yield json.dumps({
                            "type": "manifest_chunk",
                            "data": chunk.content
                        }) + "\n\n"
                
                # Optional: Send a specific 'manifest_done' event if the frontend needs it,
                # though 'done' at the end covers it.
                yield json.dumps({"type": "manifest_done"}) + "\n\n"
                
            else:
                # === CHAT MODE ===
                yield json.dumps({"type": "status", "data": "💬 responding..."}) + "\n\n"
                
                async for msg in runnable_chain.astream({"question": req.prompt}):
                    if msg and msg.content:
                        yield json.dumps({
                            "type": "text",
                            "data": msg.content
                        }) + "\n\n"
                        
            yield json.dumps({"type": "done"}) + "\n\n"

        except Exception as e:
            yield json.dumps({"type": "error", "data": str(e)}) + "\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

class GrammarRequest(BaseModel):
    text: str

@app.post("/grammar/check")
async def check_grammar(req: GrammarRequest):
    """
    Check text for spelling and grammar errors.
    Returns structured JSON with error positions and suggestions.
    """
    try:
        prompt = f"""Analyze the following text for spelling and grammatical errors. 
For each error, provide the start and end character indices (0-indexed), the type of error ('error' or 'improve'), the original text, and a better suggestion.

TEXT:
"{req.text}"

OUTPUT FORMAT (JSON ONLY):
{{
  "errors": [
    {{ "start": 0, "end": 5, "type": "error", "text": "...", "suggestion": "..." }},
    ...
  ]
}}

If no errors are found, return an empty list of errors.
ONLY return valid JSON."""

        result = runnable_chain.invoke({"question": prompt})
        
        # Parse JSON from response
        text_res = result.content if hasattr(result, 'content') else str(result)
        import re
        json_match = re.search(r'\{[\s\S]*\}', text_res)
        
        if json_match:
            data = json.loads(json_match.group(0))
            return {"success": True, "errors": data.get("errors", [])}
        else:
            return {"success": True, "errors": []}
            
    except Exception as e:
        return {"success": False, "error": str(e)}


# -------------------------
# DOM CUSTOMIZATION ENDPOINT
# -------------------------

class DomElementModel(BaseModel):
    tag: str
    attrs: dict = Field(default_factory=dict)
    style: Optional[dict] = None
    text: Optional[str] = None
    children: List['DomElementModel'] = []

# Re-evaluate for recursive model support in Pydantic v2
DomElementModel.model_rebuild()

class CustomizeRequest(BaseModel):
    elements: List[DomElementModel]
    requirements: str

@app.post("/dom/customize")
async def customize_dom(req: CustomizeRequest):
    """
    Customize DOM elements using selector-based approach.
    LLM returns targeted modifications (selector + style changes) instead of full element tree.
    """
    try:
        # Convert Pydantic models to dict for the chain
        elements_data = [el.model_dump() for el in req.elements]
        
        # Sort elements by "importance" (tag weight + area)
        tag_weights = {
            'body': 100, 'header': 90, 'nav': 90, 'main': 90, 'footer': 80,
            'h1': 85, 'h2': 80, 'h3': 75, 'button': 70, 'a': 60, 'input': 65, 
            'form': 50, 'section': 40, 'article': 40
        }
        
        def get_element_score(el):
            tag = el.get("tag", "").lower()
            score = tag_weights.get(tag, 10)
            if el.get("text"): score += 20
            rect = el.get("rect", {})
            if rect.get("width", 0) * rect.get("height", 0) > 10000: score += 15
            return score

        elements_data.sort(key=get_element_score, reverse=True)
        
        # Format top 100 most important elements for LLM
        formatted_elements = []
        for el in elements_data[:100]:
            formatted_el = {
                "selector": el.get("selector"),
                "tag": el.get("tag"),
                "text": el.get("text", "")[:60] if el.get("text") else None,
                "currentStyles": {
                    k: v for k, v in el.get("style", {}).items()
                    if k in ["color", "backgroundColor", "fontSize", "padding", "borderRadius"]
                }
            }
            # Clean None values
            formatted_el = {k: v for k, v in formatted_el.items() if v is not None}
            formatted_elements.append(formatted_el)

        
        result = await dom_customization_chain.ainvoke({
            "elements": json.dumps(formatted_elements, indent=2),
            "requirements": req.requirements
        })
        
        # Ensure result has modifications key
        if "modifications" not in result:
            # LLM might return elements or other format, try to adapt
            modifications = result.get("elements", [])
        else:
            modifications = result["modifications"]
        
        print(f"✅ Generated {len(modifications)} DOM modifications")
        return {"success": True, "modifications": modifications}

    except Exception as e:
        print(f"❌ DOM customization error: {str(e)}")
        return {"success": False, "error": str(e)}

class RewriteRequest(BaseModel):
    text: str
    properties: List[str] = Field(default_factory=lambda: ["corrected", "professional", "concise", "explained"])

@app.post("/text/rewrite")
async def rewrite_text(req: RewriteRequest):
    """
    Rewrite text into multiple versions based on requested properties.
    Supported properties: 'corrected', 'professional', 'concise', 'explained', etc.
    """
    try:
        props_str = ", ".join(req.properties)
        prompt = f"""Rewrite the following text into several distinct versions based on these properties: {props_str}.

TEXT:
"{req.text}"

For each requested property, provide a high-quality rewritten version.
If 'corrected' is requested, fix all grammar and spelling.
If 'professional' is requested, use a formal, business-like tone.
If 'concise' is requested, make it as short and clear as possible.
If 'explained' is requested, elaborate on the meaning clearly.

OUTPUT FORMAT (JSON ONLY):
{{
  "versions": {{
    "corrected": "...",
    "professional": "...",
    "concise": "...",
    "explained": "..."
  }}
}}

ONLY return the JSON object for the requested properties.
"""

        result = await rewrite_chain.ainvoke({
            "text": req.text,
            "properties": ", ".join(req.properties)
        })
        
        return {"success": True, "versions": result}

    except Exception as e:
        return {"success": False, "error": str(e)}

# ==========================================
# 🧠 AI AGENT VALIDATION ENDPOINT
# ==========================================

class MicroManifestRequest(BaseModel):
    goal: str
    context: str  # DOM or Text summary
    url: str
    title: str
 
@app.post("/agent/validate")
async def validate_agent_action(req: MicroManifestRequest):
    """
    Analyzes the current page context and generates a micro-manifest of actions
    to achieve the specific goal on this page.
    """
    try:
        print(f"🧠 [AI-AGENT] Validating Action for URL: {req.url}")
        print(f"🎯 [AI-AGENT] Goal: {req.goal}")
        print(f"📄 [AI-AGENT] Context Length: {len(req.context)} chars")
        
        result = await micro_manifest_chain.ainvoke({
            "goal": req.goal,
            "context": req.context,
            "url": req.url,
            "title": req.title
        })

        print(f"✅ [AI-AGENT] Generated Micro-Manifest:")
        print(json.dumps(result, indent=2))
        
        return {"status": "success", "micro_manifest": result}

    except Exception as e:
        print(f"❌ Validation Error: {e}")
        return {"status": "error", "message": str(e)}

class FilterRequest(BaseModel):
    goal: str
    results: list

@app.post("/agent/filter-results")
async def filter_search_results(req: FilterRequest):
    """
    Intelligently filters search results to find the most relevant ones.
    """
    try:
        print(f"🔍 [AI-AGENT] Filtering {len(req.results)} results for: {req.goal}")
        
        # Format results for LLM
        results_text = ""
        for i, res in enumerate(req.results):
            results_text += f"[{i}] {res.get('title', 'No Title')} ({res.get('url', 'No URL')})\n{res.get('description', '')}\n\n"

        result = await filter_results_chain.ainvoke({
            "goal": req.goal,
            "count": len(req.results),
            "results": results_text
        })
        
        print(f"✅ [AI-AGENT] Selected indices: {result.get('selected_indices')}")
        return {"status": "success", "selection": result}

    except Exception as e:
        print(f"❌ Filter Error: {e}")
        return {"status": "error", "message": str(e)}# ==================================================
# TAB GROUPING ENDPOINT
# ==================================================

class TabData(BaseModel):
    id: int
    title: str
    url: str
    text: str
    description: Optional[str] = ""
    favIconUrl: Optional[str] = None

class TabGroup(BaseModel):
    topic: str
    tabs: List[TabData]

class TabsRequest(BaseModel):
    tabs: List[TabData]

@app.post("/tabs/analyze-content")
async def analyze_tab_content(req: TabsRequest):
    """Analyze and group tabs by content similarity using LLM"""
    try:
        if len(req.tabs) < 2:
            return {"success": False, "error": "Need at least 2 tabs to group"}

        # Prepare tab summaries for LLM
        tab_info = []
        for tab in req.tabs:
            summary = {
                "id": tab.id,
                "title": tab.title,
                "url": tab.url,
                "domain": tab.url.split("/")[2] if len(tab.url.split("/")) > 2 else tab.url,
                "text_preview": tab.text[:500] if tab.text else tab.description[:200] if tab.description else ""
            }
            tab_info.append(summary)

        # Create prompt for LLM
        prompt = f"""Analyze these {len(tab_info)} browser tabs and group them into logical topic categories.

Tabs:
{json.dumps(tab_info, indent=2)}

Instructions:
- Create 2-5 topic groups based on content similarity
- Each tab should belong to exactly one group
- Topic names should be short and descriptive (2-4 words)
- Consider: subject matter, domain, intent, and content type
- Examples: "Shopping & Products", "News & Articles", "Video Content", "Social Media", "Documentation"

Return a JSON array of groups. Each group must have:
- "topic": string (the topic/category name)
- "tab_ids": array of integers (the tab IDs belonging to this group)

Example format:
[
  {{"topic": "Shopping & Products", "tab_ids": [1, 3, 5]}},
  {{"topic": "News Articles", "tab_ids": [2, 4]}}
]

Return ONLY the JSON array, no other text."""

        # Call LLM
        response = await runnable_chain.ainvoke({"question": prompt})
        result_text = response.content if hasattr(response, 'content') else str(response)
        
        # Parse JSON response
        result_text = result_text.strip()
        if result_text.startswith("```json"):
            result_text = result_text[7:]
        if result_text.startswith("```"):
            result_text = result_text[3:]
        if result_text.endswith("```"):
            result_text = result_text[:-3]
        result_text = result_text.strip()

        groups_data = json.loads(result_text)
        
        # Build groups with full tab data
        groups = []
        tab_map = {tab.id: tab for tab in req.tabs}
        
        for group_info in groups_data:
            topic = group_info.get("topic", "Ungrouped")
            tab_ids = group_info.get("tab_ids", [])
            
            group_tabs = []
            for tab_id in tab_ids:
                if tab_id in tab_map:
                    group_tabs.append(tab_map[tab_id])
            
            if group_tabs:
                groups.append(TabGroup(topic=topic, tabs=group_tabs))

        return {"success": True, "groups": [g.dict() for g in groups]}

    except json.JSONDecodeError as e:
        return {"success": False, "error": f"Failed to parse LLM response: {str(e)}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ======================================================
# CIRCLE TO SEARCH - VISION ANALYSIS
# ======================================================

class CircleSearchRequest(BaseModel):
    image_data: str  # Base64 encoded image
    page_url: Optional[str] = None
    page_title: Optional[str] = None

@app.post("/vision/analyze")
async def analyze_circle_search_image(
    req: CircleSearchRequest,
    authorization: Optional[str] = Header(None)
):
    """
    Feature: Circle to Search (Vision Analysis).
    Triggered by: Circle Search UI snippet capture.
    Logic: Two-Stage Inference.
    Stage 1: GPT-4o vision identifies objects/text.
    Stage 2: GPT-4o-mini generates actionable context (shopping, wiki, etc.).
    Returns: JSON with description, intent, and resource links.
    """
    try:
        from langchain_openai import ChatOpenAI
        from langchain_ollama import ChatOllama
        from langchain_core.messages import HumanMessage
        
        # Stage 1: Visual Feature Extraction
        vision_prompt = f"""... extraction instructions ..."""
        
        # Decision: Use Vision-capable models (Ollama 3.2-vision or GPT-4o)
        model_pref = getattr(req, "model", "openai")
        
        if model_pref == "ollama":
            vision_model = ChatOllama(model="llama3.2-vision:latest", temperature=0.2, num_predict=500)
        else:
            vision_model = ChatOpenAI(
                model="gpt-4o",
                temperature=0.2,
                api_key=os.getenv("OPENAI_API_KEY")
            ).with_fallbacks([ChatOllama(model="llama3.2-vision:latest", temperature=0.2, num_predict=500)])
        
        vision_message = HumanMessage(
            content=[
                {"type": "text", "text": vision_prompt},
                {"type": "image_url", "image_url": {"url": req.image_data}}
            ]
        )
        
        vision_response = await vision_model.ainvoke([vision_message])
        
        def _clean_json(text):
            text = text.strip()
            if text.startswith("```json"): text = text[7:]
            if text.startswith("```"): text = text[3:]
            if text.endswith("```"): text = text[:-3]
            return text.strip()

        vision_analysis = json.loads(_clean_json(vision_response.content))
        
        # Stage 2: Knowledge Mapping
        explanation_prompt = f"""You are analyzing what a user circled/highlighted in their browser.
Vision AI found: {vision_analysis}
Infer what the user wants and provide an explanation and suggestions with FULL URLs."""
        
        # Inference: Standard LLM mapping visual concepts to URLs
        def _get_explanation_model(pref):
            if pref == "ollama":
                return ChatOllama(model="llama3.2:latest", temperature=0.7, num_predict=500)
            return ChatOpenAI(model="gpt-4o-mini", temperature=0.7, api_key=os.getenv("OPENAI_API_KEY"))

        explanation_model = _get_explanation_model(model_pref)
        explanation_response = await explanation_model.ainvoke([{"role": "user", "content": explanation_prompt}])
        explanation_analysis = json.loads(_clean_json(explanation_response.content))
        
        return {
            "status": "success",
            "content_type": vision_analysis.get("content_type", "general"),
            "description": vision_analysis.get("description", ""),
            "user_intent": explanation_analysis.get("user_intent", ""),
            "suggestions": explanation_analysis.get("suggestions", [])
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
        
    except json.JSONDecodeError as e:
        return {
            "status": "error",
            "message": f"Failed to parse AI response: {str(e)}"
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "status": "error",
            "message": f"Vision analysis failed: {str(e)}"
        }

@app.post("/vision/analyze/stream")
async def analyze_circle_search_stream(
    req: CircleSearchRequest,
    authorization: Optional[str] = Header(None)
):
    """
    Streaming version of circle search analysis
    """
    async def stream():
        try:
            from langchain_openai import ChatOpenAI
            from langchain_ollama import ChatOllama
            from langchain_core.messages import HumanMessage
            
            # ========== STAGE 1: Vision Analysis (GPT-5-nano) - Quick ==========
            vision_prompt = f"""Analyze this image and extract key information, especially any visible products.

Context:
- Page URL: {req.page_url or 'Unknown'}
- Page Title: {req.page_title or 'Unknown'}

Extract:
1. Content type (product/location/question/text/person/object/general)
2. Any visible text
3. Brief description (1-2 sentences)
4. Main subject/focus
5. **Detected Products** - Identify any visible products in the image:
   - Wearables: glasses/sunglasses, watches, jewelry, hats
   - Clothing: shirts, pants, dresses, jackets, shoes
   - Accessories: bags, backpacks, wallets, belts
   - Electronics: phones, laptops, headphones
   - Other recognizable products

For each product, note its type and a brief description (color, style, etc.)

Return JSON only:
{{
  "content_type": "type",
  "detected_text": "text or empty",
  "description": "what you see",
  "main_subject": "primary subject",
  "detected_products": [
    {{"name": "product name", "description": "brief details"}},
    ...
  ]
}}"""
            
            model_pref = getattr(req, "model", "openai")
            
            if model_pref == "ollama":
                vision_model = ChatOllama(model="llama3.2-vision:latest", temperature=0.2, num_predict=500)
            else:
                vision_model = ChatOpenAI(
                    model="gpt-4o",
                    temperature=0.2,
                    api_key=os.getenv("OPENAI_API_KEY")
                ).with_fallbacks([ChatOllama(model="llama3.2-vision:latest", temperature=0.2, num_predict=500)])
            
            vision_message = HumanMessage(
                content=[
                    {"type": "text", "text": vision_prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": req.image_data}
                    }
                ]
            )
            
            vision_response = await vision_model.ainvoke([vision_message])
            vision_text = vision_response.content.strip()
            
            # Clean and parse vision response
            if vision_text.startswith("```json"):
                vision_text = vision_text[7:]
            if vision_text.startswith("```"):
                vision_text = vision_text[3:]
            if vision_text.endswith("```"):
                vision_text = vision_text[:-3]
            vision_text = vision_text.strip()
            
            vision_analysis = json.loads(vision_text)
            
            # Send vision analysis immediately
            yield json.dumps({
                "type": "vision_complete",
                "data": {
                    "content_type": vision_analysis.get("content_type", "general"),
                    "detected_text": vision_analysis.get("detected_text", ""),
                    "description": vision_analysis.get("description", ""),
                    "detected_products": vision_analysis.get("detected_products", [])
                }
            }) + "\n\n"
            
            # ========== STAGE 2: Stream Explanation (GPT-4o-mini) ==========
            explanation_prompt = f"""You are analyzing what a user circled/highlighted in their browser.

Vision AI found:
- Type: {vision_analysis.get('content_type', 'unknown')}
- Text: {vision_analysis.get('detected_text', 'none')}
- Description: {vision_analysis.get('description', '')}
- Subject: {vision_analysis.get('main_subject', '')}
- Page context: {req.page_title or req.page_url or 'unknown page'}

Provide a clear, helpful explanation (2-3 sentences) about what the user is looking at and what they might want to do with it."""
            
            if model_pref == "ollama":
                explanation_model = ChatOllama(model="llama3.2:latest", temperature=0.7, num_predict=500)
                # explanation_model = ChatOllama(model="smollm:135m", temperature=0.7)
            else:
                explanation_model = ChatOpenAI(
                    model="gpt-4o-mini",
                    temperature=0.7,
                    api_key=os.getenv("OPENAI_API_KEY")
                ).with_fallbacks([ChatOllama(model="llama3.2:latest", temperature=0.7, num_predict=500)])
            
            # Stream explanation
            full_explanation = ""
            async for chunk in explanation_model.astream([{"role": "user", "content": explanation_prompt}]):
                if chunk.content:
                    yield json.dumps({
                        "type": "explanation_chunk",
                        "data": chunk.content
                    }) + "\n\n"
                    full_explanation += chunk.content
            
            # Show loading indicator before generating suggestions
            yield json.dumps({
                "type": "suggestions_loading",
                "data": "Finding helpful resources and shopping links..."
            }) + "\n\n"
            
            # ========== STAGE 3: Generate Suggestions with Shopping Links ==========
            # Build product context for better suggestions
            detected_products = vision_analysis.get('detected_products', [])
            products_text = ""
            if detected_products:
                products_text = "\n- Detected Products: " + ", ".join(
                    [f"{p.get('name', 'Unknown')} ({p.get('description', '')})"
                     for p in detected_products]
                )
            
            suggestions_prompt = f"""Based on this analysis:
- Type: {vision_analysis.get('content_type', 'unknown')}
- Text: {vision_analysis.get('detected_text', 'none')}
- Description: {vision_analysis.get('description', '')}
- Explanation: {full_explanation}{products_text}
- Page: {req.page_title or req.page_url or 'unknown'}

Generate 5-8 helpful, actionable suggestions with FULL URLs.

**PRIORITY**: If products were detected, include shopping suggestions for them!
- For each detected product, create at least one shopping link
- Include Amazon, Google Shopping, or brand-specific sites
- Use specific search terms based on product description (e.g., "black round sunglasses", "leather messenger bag")

Return JSON only:
{{
  "user_intent": "what user probably wants to do",
  "suggestions": [
    {{
      "type": "shopping|map|wiki|search|answer|info",
      "title": "clear action title",
      "url": "FULL URL with https://",
      "description": "why this helps"
    }}
  ]
}}

Example URLs:
- Amazon: https://www.amazon.com/s?k=black+round+sunglasses
- Google Shopping: https://www.google.com/search?tbm=shop&q=leather+messenger+bag
- Amazon: https://www.amazon.com/s?k=query
- Google Shopping: https://www.google.com/search?tbm=shop&q=query
- Google Maps: https://www.google.com/maps/search/location+name
- Wikipedia: https://en.wikipedia.org/wiki/Article
- Google Search: https://www.google.com/search?q=query"""
            
            suggestions_response = await explanation_model.ainvoke([{"role": "user", "content": suggestions_prompt}])
            suggestions_text = suggestions_response.content.strip()
            
            # Clean and parse
            if suggestions_text.startswith("```json"):
                suggestions_text = suggestions_text[7:]
            if suggestions_text.startswith("```"):
                suggestions_text = suggestions_text[3:]
            if suggestions_text.endswith("```"):
                suggestions_text = suggestions_text[:-3]
            suggestions_text = suggestions_text.strip()
            
            suggestions_data = json.loads(suggestions_text)
            
            # Send suggestions
            yield json.dumps({
                "type": "suggestions",
                "data": {
                    "user_intent": suggestions_data.get("user_intent", ""),
                    "suggestions": suggestions_data.get("suggestions", [])
                }
            }) + "\n\n"
            
            # Done
            yield json.dumps({"type": "done"}) + "\n\n"
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield json.dumps({
                "type": "error",
                "data": str(e)
            }) + "\n\n"
    
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ======================================================
# ASYNC SNAPSHOT TASKS
# ======================================================

snapshot_tasks = {}

async def run_snapshot_task(task_id: str, req: SnapshotRequest):
    try:
        async def progress_callback(progress: int, message: str):
            snapshot_tasks[task_id]["progress"] = progress
            snapshot_tasks[task_id]["message"] = message

        if req.format == "markdown":
            if req.raw_html:
                md_content = get_markdown(req.url, req.raw_html)
            else:
                md_content = await generate_markdown_report(req.url)
            
            # For markdown, we just store the content in a temporary file
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".md")
            with open(temp_file.name, "w") as f:
                f.write(md_content)
            output_path = temp_file.name
        
        elif req.format in ["pdf", "marketing_pdf", "business_report"]:
            template = "smart"
            if req.format == "marketing_pdf": template = "marketing"
            elif req.format == "business_report": template = "business"
            output_path = await generate_smart_pdf(req.url, template=template, html=req.raw_html, progress_callback=progress_callback)
            
        elif req.format == "docx":
            output_path = await generate_word_doc(req.url, html=req.raw_html)
            
        elif req.format == "png":
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
            output_path = temp_file.name
            temp_file.close()
            await capture_page(req.url, output_path, "png", html=req.raw_html)
            
        elif req.format in ["research_paper", "ppt"]:
            output_path = await generate_special_format(req.url, target_format=req.format, html=req.raw_html)
        else:
            snapshot_tasks[task_id]["status"] = "error"
            snapshot_tasks[task_id]["error"] = "Invalid format"
            return

        snapshot_tasks[task_id]["status"] = "completed"
        snapshot_tasks[task_id]["progress"] = 100
        snapshot_tasks[task_id]["file_path"] = output_path
        snapshot_tasks[task_id]["filename"] = os.path.basename(output_path)

    except Exception as e:
        import traceback
        traceback.print_exc()
        snapshot_tasks[task_id]["status"] = "error"
        snapshot_tasks[task_id]["error"] = str(e)


@app.post("/snapshot")
async def get_website_snapshot(req: SnapshotRequest, background_tasks: BackgroundTasks):
    """
    Initiate a background task to generate a snapshot.
    """
    task_id = str(uuid.uuid4())
    snapshot_tasks[task_id] = {
        "status": "processing",
        "progress": 0,
        "message": "Initializing...",
        "format": req.format,
        "url": req.url,
        "file_path": None
    }
    
    background_tasks.add_task(run_snapshot_task, task_id, req)
    return {"task_id": task_id, "status": "processing"}


@app.get("/snapshot/status/{task_id}")
async def get_snapshot_status(task_id: str):
    if task_id not in snapshot_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return snapshot_tasks[task_id]


@app.get("/snapshot/preview/{task_id}")
async def preview_snapshot(task_id: str):
    if task_id not in snapshot_tasks or snapshot_tasks[task_id]["status"] != "completed":
        raise HTTPException(status_code=404, detail="File not ready or task not found")
    
    file_path = snapshot_tasks[task_id]["file_path"]
    media_type = "application/pdf"
    if snapshot_tasks[task_id]["format"] == "png": media_type = "image/png"
    elif snapshot_tasks[task_id]["format"] == "markdown": media_type = "text/markdown"
    
    return FileResponse(file_path, media_type=media_type)


@app.get("/snapshot/download/{task_id}")
async def download_snapshot(task_id: str):
    if task_id not in snapshot_tasks or snapshot_tasks[task_id]["status"] != "completed":
        raise HTTPException(status_code=404, detail="File not ready or task not found")
    
    file_path = snapshot_tasks[task_id]["file_path"]
    format = snapshot_tasks[task_id]["format"]
    
    filename = f"snapshot_{task_id[:8]}"
    if format == "pdf": filename += ".pdf"
    elif format == "png": filename += ".png"
    elif format == "markdown": filename += ".md"
    elif format == "docx": filename += ".docx"
    else: filename += ".pdf"

    return FileResponse(file_path, filename=filename)


@app.delete("/snapshot/{task_id}")
async def delete_snapshot(task_id: str):
    if task_id not in snapshot_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    file_path = snapshot_tasks[task_id].get("file_path")
    if file_path and os.path.exists(file_path):
        os.remove(file_path)
    
    del snapshot_tasks[task_id]
    return {"status": "success", "message": "Snapshot deleted"}
