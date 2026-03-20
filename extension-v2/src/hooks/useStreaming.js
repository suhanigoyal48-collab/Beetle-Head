import { useCallback } from 'react';
import { useApp } from '../store/AppContext';
import { API } from '../constants/api';
import { marked } from 'marked';
import { isRestrictedPage, downloadJSON } from '../utils/helpers';
import { extractDOMTree } from '../utils/domExtractor';

marked.setOptions({ breaks: true, gfm: true });

export function useStreaming() {
    const { state, dispatch, abortControllerRef } = useApp();

    const log = useCallback((text, type = 'info') => {
        dispatch({ type: 'ADD_DEBUG_LOG', log: { text, logType: type, time: new Date().toLocaleTimeString() } });
        console.log(`[${type.toUpperCase()}] ${text}`);
    }, [dispatch]);

    const addMessage = useCallback((role, content = '', extras = {}) => {
        const msg = { id: Date.now() + Math.random(), role, content, ...extras };
        dispatch({ type: 'ADD_MESSAGE', message: msg });
        return msg.id;
    }, [dispatch]);

    const ensureConversationId = useCallback(async () => {
        if (state.conversationId) return state.conversationId;
        const token = state.accessToken;
        try {
            const res = await fetch(API.CONVERSATIONS, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` }
            });
            const data = await res.json();
            if (data.status === 'success') {
                dispatch({ type: 'SET_CONVERSATION_ID', id: data.conversation_id });
                return data.conversation_id;
            }
        } catch (e) {
            console.error('Failed to create conversation:', e);
        }
        return null;
    }, [state.conversationId, state.accessToken, dispatch]);

    const streamChatResponse = useCallback(async (text, imageUrl = null, currentUrl = null) => {
        const botMsgId = addMessage('bot', '', { isStreaming: true, fullText: '' });
        dispatch({ type: 'SET_STREAMING', value: true });
        dispatch({ type: 'SET_AUTO_SCROLL', value: true });

        const controller = new AbortController();
        abortControllerRef.current = controller;

        const conversationId = await ensureConversationId();

        // ─── Resolve effective URL at call time ─────────────────────────────────
        // state.activeUrl starts as null until the first tab event fires.
        // Always detect the live active tab so we don't miss context on first use.
        let effectiveUrl = currentUrl;  // may be null if called without it
        let activeTab = null;
        let pageContext = null;

        try {
            if (typeof chrome !== 'undefined' && chrome.tabs) {
                const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
                if (tab) {
                    activeTab = tab;
                    // If effectiveUrl was null (e.g. activeUrl not set yet) use the live tab URL
                    if (!effectiveUrl && tab.url && !isRestrictedPage(tab.url)) {
                        effectiveUrl = tab.url;
                        // Keep state in sync so subsequent calls have it
                        dispatch({ type: 'SET_BROWSER_FOCUSED_TAB', tabId: tab.id, url: tab.url });
                    }
                }
            }
        } catch (e) {
            console.warn('[Context] Tab detection failed:', e);
        }

        // ─── Sync context save BEFORE chat request ──────────────────────────────
        // Await the save so vector store has chunks ready before the chat request,
        // and use the now-known conversationId so chunks are properly linked (Bug 2+3).
        try {
            if (activeTab && effectiveUrl && !isRestrictedPage(effectiveUrl) && chrome.scripting) {
                const results = await chrome.scripting.executeScript({
                    target: { tabId: activeTab.id },
                    func: extractDOMTree
                });
                pageContext = results?.[0]?.result;

                if (pageContext) {
                    // Download the extracted JSON
                    // downloadJSON(pageContext, `chat-context-${Date.now()}.json`);
                }

                if (pageContext && conversationId) {
                    const token = state.accessToken;
                    await fetch(API.CONTEXT, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Authorization': `Bearer ${token}`
                        },
                        body: JSON.stringify({
                            url: effectiveUrl,
                            raw_html: pageContext,
                            conversation_id: conversationId
                        })
                    });
                    console.log('[Context] Synced page context before chat request for:', effectiveUrl);
                }
            }
        } catch (e) {
            console.warn('[Context] Pre-chat context sync failed:', e);
        }

        let fullText = '';

        try {
            const token = state.accessToken;
            const res = await fetch(API.CHAT, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({
                    prompt: text,
                    imageUrl,
                    currentUrl: effectiveUrl,           // use live-resolved URL, not null state.activeUrl
                    context: pageContext,               // raw DOM fallback for backend
                    conversationId,
                    model: state.preferredModel,
                    history: state.messages.map(m => ({
                        role: m.role === 'bot' ? 'assistant' : m.role,
                        content: m.content,
                        imageUrl: m.imageUrl || (m.screenshot ? m.screenshot : null)
                    }))
                }),
                signal: controller.signal
            });

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { value, done } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const frames = buffer.split('\n\n');
                buffer = frames.pop();

                for (const frame of frames) {
                    if (!frame.trim()) continue;
                    try {
                        const event = JSON.parse(frame);

                        if (event.type === 'text') {
                            fullText += event.data;
                            const html = marked.parse(fullText);
                            dispatch({
                                type: 'UPDATE_LAST_BOT_MESSAGE',
                                updates: { content: fullText, html, isStreaming: true }
                            });
                        }

                        if (event.type === 'context_analysis') {
                            dispatch({
                                type: 'UPDATE_LAST_BOT_MESSAGE',
                                updates: { contextAnalysis: event.data }
                            });
                        }

                        if (event.type === 'video_analysis') {
                            dispatch({
                                type: 'UPDATE_LAST_BOT_MESSAGE',
                                updates: { videoAnalysis: event.data }
                            });
                        }

                        if (event.type === 'status') {
                            dispatch({
                                type: 'UPDATE_LAST_BOT_MESSAGE',
                                updates: { statusText: event.data }
                            });
                        }

                        if (event.type === 'rich_blocks') {
                            dispatch({
                                type: 'UPDATE_LAST_BOT_MESSAGE',
                                updates: { richBlocks: event.data }
                            });
                        }

                        if (event.type === 'error') {
                            dispatch({
                                type: 'UPDATE_LAST_BOT_MESSAGE',
                                updates: { content: '⚠️ ' + event.data, isStreaming: false, isError: true }
                            });
                        }
                    } catch (e) { /* parse error */ }
                }
            }
        } catch (error) {
            if (error.name !== 'AbortError') {
                dispatch({
                    type: 'UPDATE_LAST_BOT_MESSAGE',
                    updates: { content: '⚠️ Connection error', isStreaming: false, isError: true }
                });
            }
        } finally {
            dispatch({ type: 'UPDATE_LAST_BOT_MESSAGE', updates: { isStreaming: false } });
            dispatch({ type: 'SET_STREAMING', value: false });
            abortControllerRef.current = null;
        }
    }, [state.accessToken, state.messages, addMessage, ensureConversationId, dispatch, abortControllerRef]);

    const streamAgentManifest = useCallback(async (text, tabId) => {
        dispatch({ type: 'CLEAR_AGENT_STEPS' });
        const botMsgId = addMessage('bot', '', { isStreaming: true, isAgent: true });
        dispatch({ type: 'SET_STREAMING', value: true });

        try {
            // Send to background via chrome.runtime
            const response = await chrome.runtime.sendMessage({
                type: 'START_AGENT_LOOP',
                tabId: tabId,
                goal: text,
                history: state.messages.map(m => ({
                    role: m.role === 'bot' ? 'assistant' : m.role,
                    content: m.content
                }))
            });

            if (response && response.success) {
                // summary is already markdown with links — render it directly
                const summaryMarkdown = response.summary || '✅ Research complete.';
                dispatch({
                    type: 'UPDATE_LAST_BOT_MESSAGE',
                    updates: { content: summaryMarkdown, isStreaming: false }
                });
            } else {
                dispatch({
                    type: 'UPDATE_LAST_BOT_MESSAGE',
                    updates: { content: '❌ Task Failed: ' + (response?.error || 'Unknown'), isStreaming: false }
                });
            }
        } catch (err) {
            dispatch({
                type: 'UPDATE_LAST_BOT_MESSAGE',
                updates: { content: 'Error: ' + err.message, isStreaming: false }
            });
        } finally {
            dispatch({ type: 'SET_STREAMING', value: false });
            abortControllerRef.current = null;
        }
    }, [addMessage, state.messages, dispatch, abortControllerRef]);

    const abortStream = useCallback(() => {
        if (abortControllerRef.current) {
            abortControllerRef.current.abort();
        }
        // Also stop the agent if it's running
        if (typeof chrome !== 'undefined' && chrome.runtime) {
            chrome.runtime.sendMessage({ type: 'STOP_AGENT_LOOP' }).catch(() => { });
        }
    }, [abortControllerRef]);

    return { streamChatResponse, streamAgentManifest, abortStream, addMessage, log };
}
