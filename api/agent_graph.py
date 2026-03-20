from typing import TypedDict, List, Annotated
import operator
from langgraph.graph import StateGraph, END
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from pydantic import BaseModel, Field

# ======================================================
# STATE DEFINITION
# ======================================================

class AgentState(TypedDict):
    """
    Defines the state managed by the LangGraph agent.
    
    Attributes:
        messages: A list of messages in the conversation, using operator.add for accumulation.
        dom_state: A dictionary representing the current state of the DOM (interactive elements).
        goal: The user's research goal or task description.
        current_url: The URL of the page the agent is currently viewing.
    """
    messages: Annotated[List[BaseMessage], operator.add]
    dom_state: dict
    goal: str
    current_url: str

# ======================================================
# TOOL SCHEMAS
# ======================================================

class SearchGoogle(BaseModel):
    """Schema for the search_google tool."""
    query: str = Field(description="Search query to look up on Google. Triggers a Google search in the browser.")

class SearchYoutube(BaseModel):
    """Schema for the search_youtube tool."""
    query: str = Field(description="Search query to find videos on YouTube. Triggers a YouTube search.")

class OpenUrlsInBackground(BaseModel):
    """Schema for the open_urls_in_background tool."""
    urls: List[str] = Field(description="2-4 URLs to open, read, and summarize before deciding where to go.")
    reason: str = Field(description="Why these URLs are relevant to the user's goal.")

class NavigateTo(BaseModel):
    """Schema for the navigate_to tool."""
    url: str = Field(description="URL to navigate to in the active tab.")
    reason: str = Field(description="Why navigating here advances the goal.")

class ClickElement(BaseModel):
    """Schema for the click_element tool."""
    selector: str = Field(description="CSS selector or ID of the element to click. Triggers a click event.")
    reason: str = Field(description="Why clicking this element advances the goal.")

class TypeText(BaseModel):
    """Schema for the type_text tool."""
    selector: str = Field(description="CSS selector or ID of the input field. Triggers a typing event.")
    text: str = Field(description="Text to type into the field.")
    reason: str = Field(description="Why typing this text advances the goal.")

class Scroll(BaseModel):
    """Schema for the scroll tool."""
    direction: str = Field(description="'up' or 'down' direction for scrolling.")
    amount: str = Field(description="Amount to scroll, e.g. '500px', 'page', or 'half'.")

class ReadPageContent(BaseModel):
    """Schema for the read_page_content tool."""
    reason: str = Field(description="Why you need to read the full content of this page.")

class Done(BaseModel):
    """Schema for the done tool."""
    success: bool = Field(description="Whether the user's goal was successfully achieved.")
    summary: str = Field(description="Markdown summary of the research findings, including URLs as clickable links.")

# ======================================================
# TOOL DEFINITIONS (STUBS/LOGGING)
# ======================================================

@tool("search_google", args_schema=SearchGoogle)
def search_google_tool(query: str):
    """
    Navigate to Google and search for a query.
    Expected: A search query string.
    Triggers: Browser navigation to google.com with the search query.
    """
    return "search_google"

@tool("search_youtube", args_schema=SearchYoutube)
def search_youtube_tool(query: str):
    """
    Search YouTube for videos related to the query.
    Expected: A search query string.
    Triggers: Browser navigation to youtube.com with the search results.
    """
    return "search_youtube"

@tool("open_urls_in_background", args_schema=OpenUrlsInBackground)
def open_urls_in_background_tool(urls: List[str], reason: str):
    """
    Open multiple URLs in background tabs to extract content.
    Expected: A list of 2-4 URLs and a reason.
    Triggers: Sequential background page loading and content extraction.
    """
    return "open_urls_in_background"

@tool("navigate_to", args_schema=NavigateTo)
def navigate_to_tool(url: str, reason: str):
    """
    Navigate the active tab to a specific URL.
    Expected: A valid URL string.
    Triggers: Active tab navigation change.
    """
    return "navigate_to"

@tool("click_element", args_schema=ClickElement)
def click_element_tool(selector: str, reason: str):
    """
    Click an interactive element on the current page.
    Expected: A valid CSS selector or data-ai-id.
    Triggers: Mouse click event on the target element.
    """
    return "click_element"

@tool("type_text", args_schema=TypeText)
def type_text_tool(selector: str, text: str, reason: str):
    """
    Type text into an input field on the page.
    Expected: A selector for the input and the text to type.
    Triggers: Focus and keyboard events on the target element.
    """
    return "type_text"

@tool("scroll", args_schema=Scroll)
def scroll_tool(direction: str, amount: str):
    """
    Scroll the current page to reveal more content.
    Expected: Direction ('up'/'down') and amount.
    Triggers: Scroll event on the active page.
    """
    return "scroll"

@tool("read_page_content", args_schema=ReadPageContent)
def read_page_content_tool(reason: str):
    """
    Read the visible text and structure of the current page.
    Expected: A reason for reading.
    Triggers: DOM analysis and text extraction.
    """
    return "read_page_content"

@tool("done", args_schema=Done)
def done_tool(success: bool, summary: str):
    """
    Signal that the goal has been achieved and provide a final report.
    Expected: Success flag and a markdown-formatted summary.
    Triggers: Termination of the agent loop and delivery of the final response to the user.
    """
    return "done"

# List of tools available to the agent
tools = [
    search_google_tool,
    search_youtube_tool,
    open_urls_in_background_tool,
    navigate_to_tool,
    read_page_content_tool,
    click_element_tool,
    type_text_tool,
    scroll_tool,
    done_tool,
]

# ======================================================
# LLM CONFIGURATION
# ======================================================

# Initialize the OpenAI model with tools bound for agentic behavior
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
llm_with_tools = llm.bind_tools(tools, tool_choice="any")

# ======================================================
# SYSTEM & STEP PROMPTS
# ======================================================

SYSTEM_PROMPT = """You are a browser automation agent. Goal: "{goal}"

CURRENT PAGE:
{dom_state}

## Guidelines:
- You are here to DIRECTLY perform tasks on the browser.
- Use `click_element`, `type_text`, and `scroll` to interact with the current page.
- Preferred way to select elements is via the provided `selector` (e.g., `[data-ai-id="..."]`).
- If the current page doesn't help with the goal, use `search_google` or `navigate_to`.
- You can use tools multiple times as needed to achieve the goal.
- Be precise and efficient. Avoid unnecessary steps.

## Workflow:
1. Examine the `dom_state` for interactive elements that can advance the goal.
2. If an interaction is obvious, call the appropriate tool (`click_element`, `type_text`).
3. If more information is needed from the web, use `search_google` or `open_urls_in_background`.
4. Once the goal is satisfied (content found, action completed), call `done` with a markdown summary.

## Rules:
- Never repeat exactly the same failed action.
- If you seem stuck on a page, try scrolling or navigating elsewhere.
- Always provide a concise reason for each action.
"""

STEP_PROMPT = """Goal: "{goal}"

Current page state:
{dom_state}

What is the single best NEXT action to move closer to the goal?"""

# ======================================================
# AGENT WORKFLOW NODE
# ======================================================

def agent_node(state: AgentState):
    """
    The primary execution node for the agent in the LangGraph.
    
    This function:
    1. Extracts history, DOM state, and the goal from the state.
    2. Constructs a full prompt including the System Message, chat history, and the current step request.
    3. Invokes the LLM with tool-calling capabilities.
    4. Returns the LLM's response to be added to the state messages.
    
    Triggers: LLM generation for tool selection or final output.
    """
    history = state["messages"]
    dom_state = state["dom_state"]
    goal = state["goal"]

    # Always: [rules + context] → [previous interactions] → [current page + decision request]
    # This ensures the LLM sees its operating rules AND its action history on every step.
    system_msg = SystemMessage(content=SYSTEM_PROMPT.format(goal=goal, dom_state=dom_state))
    step_msg = HumanMessage(content=STEP_PROMPT.format(goal=goal, dom_state=dom_state))

    final_messages = [system_msg] + list(history) + [step_msg]

    response = llm_with_tools.invoke(final_messages)
    return {"messages": [response]}

# ======================================================
# GRAPH DEFINITION & COMPILATION
# ======================================================

# Define a simple StateGraph with one 'agent' node that loops back until 'done' is called or END is reached.
graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.set_entry_point("agent")
graph.add_edge("agent", END)

# Compile the graph into a runnable instance
agent_runnable = graph.compile()
