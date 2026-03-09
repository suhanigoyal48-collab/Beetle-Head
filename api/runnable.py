from dotenv import load_dotenv
import os
import json
import tempfile
import subprocess
from pathlib import Path
import assemblyai as aai
import time
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, SystemMessagePromptTemplate, HumanMessagePromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from youtube_transcript_api import YouTubeTranscriptApi
import openai
from concurrent.futures import ThreadPoolExecutor



load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSEMBLYAI_API_KEY=os.getenv("ASSEMBLYAI_API_KEY")
aai.settings.api_key = ASSEMBLYAI_API_KEY

# FASTEST config (accuracy sacrificed for speed)
assembly_config = aai.TranscriptionConfig(
    punctuate=False,
    format_text=False,
    speaker_labels=False,
    disfluencies=False
)

# ======================================================
# 🆕 YOUTUBE VIDEO TRANSCRIPTION MODULE
# ======================================================

def extract_youtube_url(text):
    """Extract YouTube URL from text"""
    import re
    patterns = [
        r'(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            video_id = match.group(1)
            return f"https://www.youtube.com/watch?v={video_id}"
    return None

def get_youtube_captions_fast(url):
    try:
        video_id = url.split("v=")[-1].split("&")[0]
        transcript = YouTubeTranscriptApi.get_transcript(video_id)
        text = " ".join([x["text"] for x in transcript])
        return text
    except:
        return None

def download_youtube_audio(url, output_dir=None):
    """Download YouTube video audio using yt-dlp (synchronous)"""
    if output_dir is None:
        output_dir = tempfile.gettempdir()

    output_path = os.path.join(output_dir, "%(id)s.%(ext)s")

    cmd = [
        "yt-dlp",
        "-f", "bestaudio",
        "-o", output_path,
        "--no-playlist",
        "--quiet",
        url
    ]

    try:
        subprocess.run(cmd, check=True)

        video_id = url.split("v=")[-1].split("&")[0]
        for ext in ["webm", "m4a", "opus"]:
            path = os.path.join(output_dir, f"{video_id}.{ext}")
            if os.path.exists(path):
                return path

        return None
    except Exception as e:
        print(f"Error downloading audio: {e}")
        return None


def get_youtube_audio_stream_url(url: str) -> str | None:
    """
    Returns a direct audio stream URL without downloading the file.
    """
    try:
        cmd = [
            "yt-dlp",
            "-f", "ba[ext=m4a]/ba",
            "-g",  # print stream URL
            "--js-runtimes", "node",
            "--extractor-args", "youtube:player_client=web",
            "--no-playlist",
            url
        ]

        stream_url = subprocess.check_output(
            cmd, stderr=subprocess.DEVNULL
        ).decode().strip()

        return stream_url if stream_url.startswith("http") else None

    except Exception as e:
        print(f"❌ Failed to get stream URL: {e}")
        return None


def split_audio(audio_path, chunk_seconds=150):
    """Split audio file into chunks (synchronous)"""
    import math
    from pathlib import Path

    audio_path = Path(audio_path)

    # Get duration
    try:
        duration = float(subprocess.check_output([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path)
        ]))
    except Exception as e:
        print(f"Error getting audio duration: {e}")
        return []

    chunks = []
    total_chunks = math.ceil(duration / chunk_seconds)

    for i in range(total_chunks):
        out_path = audio_path.with_name(
            f"{audio_path.stem}_part{i}{audio_path.suffix}"
        )

        try:
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", str(audio_path),
                    "-ss", str(i * chunk_seconds),
                    "-t", str(chunk_seconds),
                    str(out_path)
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True
            )
            chunks.append(out_path)
        except Exception as e:
            print(f"Error splitting audio chunk {i}: {e}")

    return chunks

client = openai.OpenAI(api_key=OPENAI_API_KEY)

def transcribe_chunk(path: Path):
    """
    FAST AssemblyAI transcription (blocking, safe for ThreadPoolExecutor)
    """
    try:
        transcriber = aai.Transcriber(config=assembly_config)
        transcript = transcriber.transcribe(str(path))

        if transcript.status == aai.TranscriptStatus.error:
            print(f"❌ AssemblyAI error on {path.name}: {transcript.error}")
            return ""

        return transcript.text or ""

    except Exception as e:
        print(f"❌ AssemblyAI failed chunk {path.name}: {e}")
        return ""
        
def transcribe_parallel(chunks):
    max_workers = min(4, len(chunks))  # avoid API throttling
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(transcribe_chunk, chunks))

    return " ".join(r for r in results if r)
def get_youtube_transcript(url):
    """
    Complete pipeline: Download YouTube audio and transcribe it
    This is now SYNCHRONOUS (not async)
    
    Args:
        url: YouTube video URL
    
    Returns:
        dict with transcript and metadata
    """
    # 1. Try captions first (FASTEST)
    captions = get_youtube_captions_fast(url)
    if captions:
        return {
            "transcript": captions,
            "source": "captions",
            "url": url
        }

    # 2. Download audio
    audio = download_youtube_audio(url)
    if not audio:
        return {"error": "audio download failed", "url": url}

    # 3. Chunk + parallel STT
    chunks = split_audio(audio)
    if not chunks:
        cleanup_files([audio])
        return {"error": "audio splitting failed", "url": url}
    
    # transcript = transcribe_parallel(chunks)
    transcript=transcribe_chunk(audio)

    # 4. Cleanup EVERYTHING
    cleanup_files(chunks + [audio])

    return {
        "transcript": transcript,
        "source": "stt",
        "chunks": len(chunks), 
        "url": url
    }

def extract_videos_from_page(page_context):
    """Extract YouTube video URLs from page context"""
    if not page_context:
        return []
    
    videos = []
    content = page_context.get("content", "")
    
    # Extract from iframe embeds or links
    import re
    patterns = [
        r'(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
    ]
    
    for pattern in patterns:
        matches = re.finditer(pattern, content)
        for match in matches:
            video_id = match.group(1)
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            if video_url not in videos:
                videos.append(video_url)
    
    return videos
    
def cleanup_files(paths):
    """Clean up temporary files"""
    for p in paths:
        try:
            os.remove(p)
        except:
            pass

# ======================================================
# 🆕 VIDEO CONTEXT ANALYZER
# ======================================================

video_context_analyzer_prompt = ChatPromptTemplate.from_template("""
Analyze if the user's query requires VIDEO CONTENT/TRANSCRIPT.

Return TRUE if:
1. User asks about a video ("what is this video about", "summarize video")
2. Asks to explain video content
3. References timestamps or video segments
4. Asks questions about video topic/speaker
5. Wants transcript or subtitles

Return FALSE if:
- General web search
- Page text content (non-video)
- Product queries

Return ONLY this JSON:
{{
  "needs_video_context": true/false,
  "reason": "why video context is needed",
  "extract_all": false
}}

User query: {question}
Page has videos: {has_videos}
""")

video_context_analyzer_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.2,
    streaming=False,
    api_key=OPENAI_API_KEY
)

video_context_analyzer_chain = (
    video_context_analyzer_prompt 
    | video_context_analyzer_llm 
    | JsonOutputParser()
)

# ======================================================
# PAGE CONTEXT ANALYZER (FROM WORKING VERSION)
# ======================================================

context_analyzer_prompt = ChatPromptTemplate.from_template("""
Decide whether the user's question requires the CURRENT PAGE CONTENT.

The page content includes:
- Page title & description
- Visible text (headings, paragraphs, lists)
- Form labels and placeholders
- Quiz / assignment text and questions

Return TRUE if:
1. User refers to "this page", "current page"
2. Asks to summarize or explain page content
3. Mentions assignment, quiz, MCQs, questions
4. Asks to solve or help with questions on the page
5. Asks what is shown / written on the page

Return FALSE if:
- General knowledge
- Tutorials unrelated to page
- Definitions
- Recommendations not tied to page

Return ONLY this JSON:
{{
  "needs_context": true/false,
  "reason": "short reason",
  "context_usage": "full|summary|none"
}}

User query: {question}
""")

context_analyzer_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    streaming=False,
    api_key=OPENAI_API_KEY
)

context_analyzer_chain = context_analyzer_prompt | context_analyzer_llm | JsonOutputParser()

# ======================================================
# CONTEXT-AWARE CHAT WITH VIDEO SUPPORT
# ======================================================

context_aware_chat_prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a helpful AI assistant.\n\n{context_section}\n\n{video_context_section}\n\nSTRICT OUTPUT FORMATTING RULES (MANDATORY):\n- Use VALID Markdown\n- Use ## for question headings\n- Use ### for sub-sections\n- Use bullet points for steps\n- Use numbered lists for answers\n- ALWAYS put equations in LaTeX blocks:\n\n  \\[\n  \\frac{{a}}{{b}}\n  \\]\n\n- NEVER write math like: ( \\frac{{1}}{{2}} )\n- NEVER wrap LaTeX in parentheses\n- Leave ONE blank line before and after headings\n- Do NOT dump raw text blocks\n- Keep answers readable and structured\n\nIf page context is provided:\n- Use ONLY the given page content\n- Do NOT assume anything not present\n- For quizzes or assignments, read questions carefully\n- Explain answers step by step when asked\n\nIf video transcript is provided:\n- Reference the video content accurately\n- You can quote or paraphrase from the transcript\n- Explain video topics clearly\n- Answer questions based on what was said in the video\n\nBe accurate, structured, and cleanly formatted."),
    MessagesPlaceholder(variable_name="chat_history"),
    ("human", "{question}")
])

context_aware_chat_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.1,
    streaming=True,
    api_key=OPENAI_API_KEY
)


def format_questions_with_options(questions):
    """Format questions with their MCQ options into readable text"""
    if not questions:
        return ""
    
    output = ["=" * 60]
    output.append("ASSESSMENT QUESTIONS WITH ANSWER OPTIONS:")
    output.append("=" * 60)
    
    for q in questions[:20]:
        output.append(f"\n{'='*60}")
        output.append(f"QUESTION {q['index']} ({q['type'].upper()}):")
        output.append(f"{'='*60}")
        output.append(f"{q['text'][:600]}")
        
        if q.get('options') and len(q['options']) > 0:
            output.append(f"\nANSWER OPTIONS:")
            for opt in q['options']:
                checkbox = "[x]" if opt.get('checked') else "[ ]"
                opt_text = opt.get('text', '').strip()
                opt_value = opt.get('value', '')
                opt_name = opt.get('name', '')
                
                option_display = f"  {checkbox} Option {opt['index']}: {opt_text}"
                if opt_value and opt_value != opt_text:
                    option_display += f" (value: {opt_value})"
                if opt_name:
                    option_display += f" [name: {opt_name}]"
                    
                output.append(option_display)
        
        output.append("")
    
    output.append("=" * 60)
    return "\n".join(output)


def format_dom_for_llm(dom_tree, max_depth=12, current_depth=0):
    """Recursively format DOM tree into human-readable structure"""
    if not dom_tree or current_depth > max_depth:
        return ""
    
    indent = "  " * current_depth
    output = []
    
    tag = dom_tree.get("tag", "")
    attrs = dom_tree.get("attrs", {})
    text = dom_tree.get("text", "")
    value = dom_tree.get("value", "")
    checked = dom_tree.get("checked", "")
    children = dom_tree.get("children", [])
    
    if not text and not value and not children and not any(
        key in attrs for key in ["href", "src", "action", "for"]
    ):
        return ""
    
    tag_parts = [f"{indent}<{tag}"]
    
    priority_attrs = ["id", "class", "href", "type", "name", "placeholder", "for", "value"]
    for attr in priority_attrs:
        if attr in attrs and attrs[attr]:
            val = attrs[attr]
            if attr == "class" and len(val) > 80:
                val = val[:77] + "..."
            tag_parts.append(f'{attr}="{val}"')
    
    output.append(" ".join(tag_parts) + ">")
    
    if text and len(text.strip()) > 0:
        truncated_text = text.strip()[:300]
        if len(text.strip()) > 300:
            truncated_text += "..."
        output.append(f"{indent}  📝 {truncated_text}")
    
    if value:
        output.append(f"{indent}  💬 value: {value[:150]}")
    
    if checked:
        output.append(f"{indent}  ✓ checked: {checked}")
    
    for child in children:
        child_output = format_dom_for_llm(child, max_depth, current_depth + 1)
        if child_output:
            output.append(child_output)
    
    return "\n".join(output)


def create_context_aware_chain(page_context=None, use_context=False, video_transcripts=None, image_url=None):
    """
    Create context-aware chain with optional video transcripts and image
    """
    context_section = ""
    video_context_section = ""
    
    if use_context and page_context:
        head = page_context.get("head", {})
        content = page_context.get("content", "")

        context_section = "\n".join([
            "=" * 60,
            "CURRENT PAGE CONTENT",
            "=" * 60,
            f"Title: {head.get('title', 'N/A')}",
            f"Description: {head.get('description', '')}",
            "",
            content[:12000],
            "=" * 60
        ])
    
    if video_transcripts:
        video_parts = ["=" * 60, "VIDEO TRANSCRIPTS", "=" * 60, ""]
        
        for idx, video_data in enumerate(video_transcripts[:3], 1):
            if video_data.get("transcript"):
                video_parts.append(f"VIDEO {idx}: {video_data.get('url', 'Unknown URL')}")
                video_parts.append("-" * 60)
                transcript = video_data["transcript"][:8000]
                if len(video_data["transcript"]) > 8000:
                    transcript += "\n... [transcript truncated]"
                video_parts.append(transcript)
                video_parts.append("")
        
        video_parts.append("=" * 60)
        video_context_section = "\n".join(video_parts)

    prompt_template = context_aware_chat_prompt.partial(
        context_section=context_section,
        video_context_section=video_context_section
    )

    if image_url:
        def vision_mapper(inputs):
            question = inputs.get("question", "")
            history = inputs.get("chat_history", [])
            rendered_prompt = prompt_template.format(question=question, chat_history=[]) # Format without history for the text Part
            
            messages = []
            # Add history messages
            messages.extend(history)
            
            # Add the current human message with image
            messages.append(HumanMessage(content=[
                {"type": "text", "text": rendered_prompt},
                {"type": "image_url", "image_url": {"url": image_url}}
            ]))
            
            return messages
        
        return RunnableLambda(vision_mapper) | context_aware_chat_llm
    else:
        return prompt_template | context_aware_chat_llm

# ======================================================
# ORIGINAL CHAT CHAIN (FROM WORKING VERSION)
# ======================================================

chat_prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a helpful AI assistant.\nAnswer clearly and concisely."),
    MessagesPlaceholder(variable_name="chat_history"),
    ("human", "{question}")
])

chat_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.1,
    streaming=True,
    api_key=OPENAI_API_KEY
)

runnable_chain = chat_prompt | chat_llm

# ======================================================
# CONTENT CLASSIFIER (FROM WORKING VERSION)
# ======================================================

classifier_prompt = ChatPromptTemplate.from_template("""
Analyze this user query and determine what type of content is needed.

CLASSIFICATION RULES:

1. VIDEO/YOUTUBE QUERIES (video, watch, playlist, tutorial, course, lesson):
   - needs_rich_content: true
   - content_types: ["youtube"] ONLY
   - NO products, NO images

2. PRODUCT QUERIES (buy, price, best phone, laptop, gadget, review):
   - needs_rich_content: true
   - content_types: ["products", "images"]
   - youtube optional if review-related

3. VISUAL QUERIES (show me, pictures, photos, gallery):
   - needs_rich_content: true
   - content_types: ["images"]
   - NO products unless shopping-related

4. TRAVEL/PLACES (destination, visit, travel, city, country):
   - needs_rich_content: true
   - content_types: ["images", "youtube"]
   - NO products

5. PAGE CONTEXT QUERIES (summarize, what's on page, questions, assignment, solve):
   - needs_rich_content: false
   - content_types: ["none"]
   - These rely on page context, not rich content

6. SIMPLE QUERIES (greetings, facts, questions without visual component):
   - needs_rich_content: false
   - content_types: ["none"]

IMPORTANT:
- Keywords like "video", "watch", "playlist", "tutorial" → ONLY youtube
- Keywords like "buy", "price", "best [product]" → products + images
- Learning content (DSA, programming) → youtube ONLY
- Page-specific queries (summarize, what assignment, solve) → NO rich content
- DO NOT mix products with educational content

Return ONLY this JSON:
{{
  "needs_rich_content": true/false,
  "content_types": ["youtube", "products", "images", "none"],
  "primary_intent": "video|product|visual|page_context|info",
  "reason": "brief explanation"
}}

User query: {question}
""")

classifier_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    streaming=False,
    api_key=OPENAI_API_KEY
)

classifier_chain = classifier_prompt | classifier_llm | JsonOutputParser()

# ======================================================
# RICH CONTENT GENERATOR (FROM WORKING VERSION)
# ======================================================

rich_content_prompt = ChatPromptTemplate.from_template("""
You are a content enrichment AI. Generate rich media suggestions for the user's query.

CRITICAL RULES:
1. ONLY generate content for types listed in content_types
2. If "youtube" in content_types → generate youtube_videos ONLY
3. If "products" in content_types → generate products + optional images
4. If "images" in content_types alone → generate images ONLY
5. NEVER generate products for educational/video queries

Generate 4-6 items per requested category.

Output format:
{{
  "images": [
    {{"query": "search term", "caption": "description", "source": "general"}}
  ],
  "youtube_videos": [
    {{"title": "video title", "query": "search term", "reason": "why relevant"}}
  ],
  "products": [
    {{"title": "product name", "price": "₹X,XXX", "reason": "why recommended", "query": "search term", "platform": "amazon/flipkart"}}
  ]
}}

Content types to generate: {content_types}
User query: {question}
Primary intent: {primary_intent}
""")

rich_content_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.3,
    streaming=False,
    api_key=OPENAI_API_KEY
)

rich_content_chain = rich_content_prompt | rich_content_llm | JsonOutputParser()

# ======================================================
# EXPLAIN CHAIN (FROM WORKING VERSION)
# ======================================================

explain_prompt = ChatPromptTemplate.from_template("""
You are an AI sidebar agent.

Explain the user's query clearly and simply.
Do NOT include links or URLs.
Do NOT mention that you're opening tabs or executing actions.
Just explain what the user is asking about.

User query:
{question}
""")

explain_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.2,
    streaming=True,
    api_key=OPENAI_API_KEY
)

explain_chain = explain_prompt | explain_llm

# ======================================================
# ✅ WORKING AGENT PROMPT (FROM OLD VERSION - TEMPERATURE 0)
# ======================================================

agent_prompt = ChatPromptTemplate.from_template("""
You are an AI action executor that parses complex user queries into multiple browser actions.

CRITICAL: Respond with ONLY valid JSON. NO explanatory text before or after.

ACTION TYPES:
1. open_url - Open a specific URL
2. open_web_search - Google search
3. open_youtube_search - YouTube search
4. open_youtube_playlist - YouTube playlist search
5. search_suggestion - Platform-specific search (amazon, flipkart, spotify, netflix, wikipedia, images)
6. follow_up - Ask a follow-up question

PARSING RULES FOR COMPLEX QUERIES:
- Identify ALL distinct actions in the query
- Look for conjunctions: "and", "then", "also", "too", "or"
- Look for action verbs: "open", "search", "watch", "study", "learn", "find", "play"
- Extract platform names: spotify, youtube, netflix, google, amazon, flipkart
- Extract topics/subjects for search queries
- Generate 1-8 actions depending on query complexity
- Set auto: true for ALL actions (they execute automatically)

PLATFORM URL MAPPINGS:
- spotify → https://open.spotify.com
- netflix → https://www.netflix.com
- youtube → open_youtube_search or search_suggestion with target: youtube
- amazon → search_suggestion with target: amazon
- flipkart → search_suggestion with target: flipkart

EXAMPLES:

Query: "open spotify then search langchain in web and open youtube and netflix"
{{
  "actions": [
    {{
      "type": "open_url",
      "url": "https://open.spotify.com",
      "label": "🎵 Opening Spotify",
      "auto": true
    }},
    {{
      "type": "open_web_search",
      "query": "langchain",
      "label": "🔍 Searching for langchain",
      "auto": true
    }},
    {{
      "type": "open_url",
      "url": "https://www.youtube.com",
      "label": "▶️ Opening YouTube",
      "auto": true
    }},
    {{
      "type": "open_url",
      "url": "https://www.netflix.com",
      "label": "🎬 Opening Netflix",
      "auto": true
    }}
  ]
}}

Query: "play some music and find python tutorials"
{{
  "actions": [
    {{
      "type": "open_url",
      "url": "https://open.spotify.com",
      "label": "🎵 Opening Spotify",
      "auto": true
    }},
    {{
      "type": "open_youtube_search",
      "query": "python tutorial",
      "label": "🎥 Finding Python tutorials",
      "auto": true
    }}
  ]
}}

Query: "best gaming laptop under 80k"
{{
  "actions": [
    {{
      "type": "search_suggestion",
      "query": "gaming laptop under 80000",
      "target": "amazon",
      "label": "🛒 Amazon - Gaming Laptops",
      "auto": true
    }},
    {{
      "type": "search_suggestion",
      "query": "gaming laptop under 80000",
      "target": "flipkart",
      "label": "🛒 Flipkart - Gaming Laptops",
      "auto": true
    }}
  ]
}}

Query: "study react and watch some movies"
{{
  "actions": [
    {{
      "type": "open_youtube_search",
      "query": "react tutorial",
      "label": "📚 React tutorials",
      "auto": true
    }},
    {{
      "type": "open_web_search",
      "query": "react documentation",
      "label": "📖 React docs",
      "auto": true
    }},
    {{
      "type": "open_url",
      "url": "https://www.netflix.com",
      "label": "🎬 Opening Netflix",
      "auto": true
    }}
  ]
}}

Query: "find restaurants near me and book a table"
{{
  "actions": [
    {{
      "type": "open_web_search",
      "query": "restaurants near me",
      "label": "🍽️ Finding restaurants",
      "auto": true
    }},
    {{
      "type": "search_suggestion",
      "query": "restaurant table booking",
      "target": "web",
      "label": "📅 Table booking options",
      "auto": true
    }}
  ]
}}

Query: "open spotify, search for DSA playlist, and maybe watch some coding tutorials"
{{
  "actions": [
    {{
      "type": "open_url",
      "url": "https://open.spotify.com",
      "label": "🎵 Opening Spotify",
      "auto": true
    }},
    {{
      "type": "search_suggestion",
      "query": "DSA playlist",
      "target": "spotify",
      "label": "🎵 Searching DSA in Spotify",
      "auto": true
    }},
    {{
      "type": "open_youtube_search",
      "query": "coding tutorials DSA",
      "label": "🎥 Coding tutorials",
      "auto": true
    }}
  ]
}}

NOW PROCESS THIS QUERY - RESPOND WITH ONLY JSON:

User query: {question}
Primary intent: {primary_intent}
""")

agent_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    streaming=False,
    api_key=OPENAI_API_KEY
)

agent_chain = agent_prompt | agent_llm | JsonOutputParser()

# ======================================================
# ACTION INTENT ANALYZER - Decides if browser actions needed
# ======================================================

action_intent_prompt = ChatPromptTemplate.from_template("""
Analyze if this query requires BROWSER ACTIONS (opening tabs, searching web).

Return TRUE if query contains:
- "open", "search", "find", "show me", "look up"
- "go to", "navigate to", "visit"
- Platform names: spotify, youtube, netflix, amazon, flipkart
- Multiple tasks: "do X and Y", "then do Z"
- Web search intent: "best products", "buy", "watch videos"

Return FALSE if query is about:
- Current page content: "what's on this page", "summarize this"
- Page questions: "solve this", "answer these questions"
- Explanations: "explain", "what is", "how does"
- Current video: "what is this video about"
- Direct content questions without navigation intent

Examples:

Query: "open spotify and search for songs"
Result: {{"needs_actions": true, "reason": "explicit open and search request"}}

Query: "what are the questions on this page?"
Result: {{"needs_actions": false, "reason": "asking about current page content"}}

Query: "summarize this video"
Result: {{"needs_actions": false, "reason": "analyzing current video content"}}

Query: "find best laptops under 50k"
Result: {{"needs_actions": true, "reason": "web search and product browsing needed"}}

Query: "explain machine learning"
Result: {{"needs_actions": false, "reason": "general explanation request"}}

Query: "open youtube and play music then search python tutorial"
Result: {{"needs_actions": true, "reason": "multiple navigation actions requested"}}

User query: {question}
Page context available: {has_context}
Current URL: {current_url}

Return ONLY JSON:
{{
  "needs_actions": true/false,
  "reason": "brief explanation",
  "action_type": "navigation|content_analysis|mixed"
}}
""")

action_intent_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    streaming=False,
    api_key=OPENAI_API_KEY
)

action_intent_chain = action_intent_prompt | action_intent_llm | JsonOutputParser()


# ======================================================
# 🆕 DOM ACTION EXECUTOR PROMPT
# ======================================================

dom_action_prompt = ChatPromptTemplate.from_template("""
You are a BROWSER DOM ACTION PLANNER.

Your task:
Given a user instruction and the CURRENT PAGE DOM STRUCTURE,
output a list of precise DOM actions.

IMPORTANT RULES (MANDATORY):
- Respond with ONLY valid JSON
- NO explanations outside JSON
- NO markdown
- NO comments
- NO extra keys

SUPPORTED ACTION TYPES:
1. click      → Click a button, link, checkbox
2. input      → Fill text in input/textarea
3. select     → Choose dropdown option
4. check      → Check checkbox or radio
5. submit     → Submit a form
6. navigate   → Click anchor link

SELECTOR RULES:
- Prefer id (#id)
- Then name ([name=""])
- Then type + placeholder
- Avoid brittle selectors
- One selector per action

VALUE RULES:
- Only include "value" for input/select
- Never hallucinate values
- If value not specified → OMIT action

DOM CONTEXT:
{dom_context}

USER INSTRUCTION:
{question}

RETURN ONLY JSON IN THIS FORMAT:
{{
  "actions": [
    {{
      "type": "click|input|select|check|submit|navigate",
      "selector": "css selector",
      "value": "optional",
      "reason": "short justification"
    }}
  ]
}}

If NO DOM action is required:
{{ "actions": [] }}
""")


dom_action_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    streaming=False,
    api_key=OPENAI_API_KEY
)

dom_action_chain = dom_action_prompt | dom_action_llm | JsonOutputParser()

def run_dom_action_agent(question: str, page_context: dict):
    """Run DOM action agent to generate browser actions"""
    if not page_context or "dom_tree" not in page_context:
        return {"actions": []}

    dom_context = format_dom_for_llm(page_context["dom_tree"])

    result = dom_action_chain.invoke({
        "question": question,
        "dom_context": dom_context
    })

    return result


# ======================================================
# OPTIMIZED REWRITE CHAIN (SUB-SECOND)
# ======================================================

rewrite_prompt = ChatPromptTemplate.from_messages([
    SystemMessagePromptTemplate.from_template(
        "You are a high-speed text rewriting engine. "
        "Rewrite the input text into the requested formats. "
        "Return ONLY a JSON object with the requested keys. "
        "Keys correspond to the properties requested."
    ),
    HumanMessagePromptTemplate.from_template(
        "PROPERTIES: {properties}\n"
        "TEXT: {text}\n\n"
        "JSON OUTPUT:"
    )
])

rewrite_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    streaming=False,
    api_key=OPENAI_API_KEY,
    model_kwargs={"response_format": {"type": "json_object"}}
)

rewrite_chain = rewrite_prompt | rewrite_llm | JsonOutputParser()

# ======================================================
# DOM CUSTOMIZATION CHAIN - SELECTOR-BASED
# ======================================================

dom_customization_prompt = ChatPromptTemplate.from_messages([
    SystemMessagePromptTemplate.from_template(
        "You are an expert UI/UX Designer and CSS specialist. "
        "You receive a list of page elements identified by stable, unique ID selectors (like [data-ai-id='ai-123']). "
        "Your task is to generate precise CSS modifications based on user requirements.\n\n"
        "RULES:\n"
        "1. Output ONLY a valid JSON object with a 'modifications' array.\n"
        "2. Each modification MUST have 'selector' (exactly as provided) and 'changes' (an object of camelCase CSS properties).\n"
        "3. Focus on high-impact visual changes: colors, typography, spacing, and shadows.\n"
        "4. DESIGN PHILOSOPHY: Use a clean, minimalist, and sober aesthetic. Avoid cartoonish border-radius (keep it under 8px unless a circle is requested) or jarring neon colors.\n"
        "5. READABILITY: Never sacrifice text readability for style. Ensure high contrast.\n"
        "6. SCALE: Only modify a few key elements (5-15) for overall impact. Do not over-style every single div.\n\n"
        "OUTPUT FORMAT (STRICT JSON):\n"
        "{{\n"
        "  \"modifications\": [\n"
        "    {{\n"
        "      \"selector\": \"[data-ai-id='ai-42']\",\n"
        "      \"changes\": {{\n"
        "        \"backgroundColor\": \"#1a73e8\",\n"
        "        \"color\": \"#ffffff\",\n"
        "        \"borderRadius\": \"8px\"\n"
        "      }}\n"
        "    }}\n"
        "  ]\n"
        "}}"
    ),
    HumanMessagePromptTemplate.from_template(
        "USER REQUIREMENTS:\n{requirements}\n\n"
        "PAGE ELEMENTS:\n{elements}\n\n"
        "Generate the CSS modifications to satisfy the requirements."
    )
])


dom_customization_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.2,  # Slightly higher for creative design choices
    streaming=False,
    api_key=OPENAI_API_KEY,
    model_kwargs={"response_format": {"type": "json_object"}}
)

dom_customization_chain = dom_customization_prompt | dom_customization_llm | JsonOutputParser()

# ======================================================
# MICRO MANIFEST / AI VALIDATION CHAIN
# ======================================================

micro_manifest_prompt = ChatPromptTemplate.from_template("""
You are an intelligent browser agent. Your goal is to achieve the user's objective on the current web page.

User Goal: {goal}
Current Page Title: {title}
Current URL: {url}

Page Context (Text/DOM Summary):
{context}

Based on the goal and the page content, generate a "Micro Manifest" of IMMEDIATE actions to perform on this page.
The actions should be precise. DO NOT just "finish" immediately unless you have verified the information.
Perfrom checks, scrolls, and extractions to give the user PINPOINT results.
If the page has useful links, navigation, or search results, exploring them is better than giving up.

AVAILABLE ACTIONS:
- click: {{ "type": "click", "selector": "css_selector", "description": "reason" }}
- type: {{ "type": "type", "selector": "css_selector", "value": "text_to_type", "description": "reason" }}
- scroll: {{ "type": "scroll", "direction": "down|up", "amount": pixels, "description": "reason" }}
- extract: {{ "type": "extract", "selector": "css_selector", "variable": "var_name", "description": "reason" }}
- wait: {{ "type": "wait", "duration": milliseconds, "description": "reason" }}
- finish: {{ "type": "finish", "reason": "Goal achieved or impossible" }}

RULES:
1. Return a JSON object with a "actions" key containing a list of action objects.
2. If the goal is achieved, use the "finish" action.
3. Be specific with selectors.
4. If multiple steps are needed, list them in logical order.

Example Output:
{{
  "reasoning": "The user wants to optimized searh, so I will type the query and click search.",
  "actions": [
    {{ "type": "type", "selector": "#search-input", "value": "optimization", "description": "Type search query" }},
    {{ "type": "click", "selector": "button.search-btn", "description": "Click search button" }}
  ]
}}
""")

micro_manifest_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    streaming=False,
    api_key=OPENAI_API_KEY
)

micro_manifest_chain = micro_manifest_prompt | micro_manifest_llm | JsonOutputParser()

# ======================================================
# SEARCH RESULT FILTERING CHAIN
# ======================================================

filter_results_prompt = ChatPromptTemplate.from_template("""
You are an intelligent research assistant.
Your goal is to select the BEST search results to explore given a user's objective.

User Objective: {goal}
Total Results Found: {count}

Search Results:
{results}

INSTRUCTIONS:
1. Select the most relevant 1-3 results.
2. Prioritize official documentation, authoritative sources, and recent content.
3. Ignore ads, low-quality SEO spam, or forum noise unless specific.

Output JSON:
{{
  "selected_indices": [0, 2], // Indices of selected results (0-based)
  "reason": "docs.langchain.com is the official source."
}}
""")

filter_results_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    streaming=False,
    api_key=OPENAI_API_KEY
)

filter_results_chain = filter_results_prompt | filter_results_llm | JsonOutputParser()