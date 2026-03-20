import React, { useEffect, useCallback, useRef } from 'react';
import { useApp } from './store/AppContext';
import { useAuth } from './hooks/useAuth';
import { useStreaming } from './hooks/useStreaming';
import { useContextSync } from './hooks/useContextSync';
import { extractVideoId } from './utils/helpers';

import Header from './components/layout/Header';
import LoginView from './components/views/LoginView';
import ChatView from './components/views/ChatView';
import NotesView from './components/views/NotesView';
import HistoryView from './components/views/HistoryView';
import MediaGallery from './components/views/MediaGallery';
import ChatInput from './components/input/ChatInput';
import FloatingActionBar from './components/ui/FloatingActionBar';
import TabsCarousel from './components/ui/TabsCarousel';
import ScreenshotModal from './components/modals/ScreenshotModal';
import SmartSnapshotModal from './components/modals/SmartSnapshotModal';

const TOOLS_ICON = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 16, height: 16 }}>
    <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
  </svg>
);

export default function App() {
  const { state, dispatch } = useApp();
  const { checkAuthStatus, loginWithGoogle, logoutGoogle } = useAuth();
  const { streamChatResponse, addMessage } = useStreaming();
  const { saveContext } = useContextSync();
  const lastDetectedUrlRef = useRef('');

  const checkForYouTubeVideo = useCallback(async (tab, externalInfo = null) => {
    const url = tab?.url || externalInfo?.url;
    if (!url?.includes('youtube.com/watch')) return;

    // Global deduplication check - find if this video is already the last system message
    // or if we already have it in history to avoid spamming
    const isDuplicate = state.messages.some(m =>
      m.role === 'system' &&
      m.videoPreview &&
      m.videoPreview.url === url
    );
    if (isDuplicate) return;

    // Local ref check as second layer
    if (lastDetectedUrlRef.current === url) return;
    lastDetectedUrlRef.current = url;

    if (externalInfo) {
      addMessage('system', '', { 
        videoPreview: { 
          ...externalInfo, 
          thumbnail: `https://img.youtube.com/vi/${extractVideoId(externalInfo.url)}/mqdefault.jpg` 
        } 
      });
      return;
    }

    try {
      const results = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => {
          const video = document.querySelector('video');
          const title = document.querySelector('h1.ytd-watch-metadata yt-formatted-string')?.textContent ||
            document.querySelector('#container h1 .ytd-watch-metadata')?.textContent || document.title;
          const channel = document.querySelector('#channel-name a')?.textContent || '';
          return { title: title?.trim(), channel, url: location.href, hasVideo: !!video };
        }
      });
      const info = results?.[0]?.result;
      if (info?.hasVideo) {
        addMessage('system', '', { videoPreview: { ...info, thumbnail: `https://img.youtube.com/vi/${extractVideoId(info.url)}/mqdefault.jpg` } });
      }
    } catch { /* silent */ }
  }, [state.messages, addMessage]);

  const listenForChromeMessages = useCallback(() => {
    if (typeof chrome === 'undefined' || !chrome.runtime) return;

    const handler = (msg) => {
      if (msg.type === 'CHAT_MESSAGE') {
        dispatch({ type: 'SET_TAB', tab: 'chats' });
        addMessage('user', msg.text);
        streamChatResponse(msg.text, null, state.activeUrl);
      }
      if (msg.type === 'PREFILL_INPUT') {
        const ev = new CustomEvent('prefillInput', { detail: msg.text });
        window.dispatchEvent(ev);
      }
      if (msg.type === 'SYSTEM_STATUS') {
        addMessage('system-status', msg.text);
      }
      if (msg.type === 'TAB_PREVIEW') {
        addMessage('tab-preview', '', { tabData: msg.tab });
        dispatch({ type: 'INCREMENT_TAB_COUNT' });
        if (state.openedTabCount + 1 >= 4 && !state.groupingSuggestionShown) {
          addMessage('grouping-suggestion', '');
          dispatch({ type: 'SHOW_GROUPING_SUGGESTION' });
        }
      }
      if (msg.type === 'TABS_GROUPED') {
        addMessage('tabs-grouped', '', { groups: msg.groups });
        dispatch({ type: 'RESET_TAB_COUNT' });
      }
      if (msg.type === 'VIDEO_PROGRESS_UPDATE') {
        const { currentTime, duration, title, url } = msg.data;
        const videoId = extractVideoId(url);
        if (videoId) {
          dispatch({
            type: 'UPDATE_VIDEO_STATE',
            videoId,
            data: { currentTime, duration, title, url, lastUpdated: Date.now() }
          });
          
          // 🆕 Automatically show preview card if it doesn't exist
          checkForYouTubeVideo(null, msg.data);
        }
      }
      if (msg.type === 'SCREENSHOT_CAPTURED') {
        addMessage('system', 'Screenshot captured!', { screenshot: msg.image });
        dispatch({ type: 'SET_ATTACHED_IMAGE', url: msg.image });
      }
      if (msg.type === 'RECORDING_DATA') {
        addMessage('system', 'Screen recording complete!', { video: msg.video });
      }
      if (msg.type === 'CIRCLE_SEARCH_RESULT') {
        addMessage('system', 'Circle Search complete!', { 
          screenshot: msg.imageData,
          metadata: { url: msg.pageUrl, title: msg.pageTitle }
        });
        dispatch({ type: 'SET_ATTACHED_IMAGE', url: msg.imageData });
      }

      if (msg.type === 'SMART_SNAPSHOT_START') {
        dispatch({ type: 'SET_TAB', tab: 'chats' });
        dispatch({ 
            type: 'ADD_MESSAGE', 
            message: { 
                id: msg.taskId, 
                role: 'snapshot-progress', 
                format: msg.format, 
                progress: 0, 
                statusText: "Initializing...",
                completed: false,
                isError: false
            } 
        });
      }
      if (msg.type === 'SMART_SNAPSHOT_PROGRESS') {
        dispatch({
            type: 'UPDATE_MESSAGE_BY_ID',
            id: msg.taskId,
            updates: { progress: msg.progress, statusText: msg.messageText }
        });
      }
      if (msg.type === 'SMART_SNAPSHOT_COMPLETE') {
        dispatch({
            type: 'UPDATE_MESSAGE_BY_ID',
            id: msg.taskId,
            updates: { 
                completed: true, 
                progress: 100, 
                statusText: `✅ ${msg.filename} generated!`,
                downloadUrl: msg.downloadUrl,
                filename: msg.filename
            }
        });
      }
      if (msg.type === 'SMART_SNAPSHOT_ERROR') {
        dispatch({
            type: 'UPDATE_MESSAGE_BY_ID',
            id: msg.taskId,
            updates: { 
                isError: true, 
                statusText: `❌ Snapshot failed: ${msg.error}`
            }
        });
      }
    };

    chrome.runtime.onMessage.addListener(handler);

    const onTabUpdated = (tabId, changeInfo, tab) => {
      // Trigger on status change OR URL change (YouTube SPA navigation)
      if ((changeInfo.status === 'complete' || changeInfo.url) && tab.active) {
        checkForYouTubeVideo(tab);
      }
    };
    const onTabActivated = (activeInfo) => {
      chrome.tabs.get(activeInfo.tabId, (tab) => {
        if (tab && tab.active) checkForYouTubeVideo(tab);
      });
    };

    chrome.tabs.onUpdated.addListener(onTabUpdated);
    chrome.tabs.onActivated.addListener(onTabActivated);

    return () => {
      chrome.runtime.onMessage.removeListener(handler);
      chrome.tabs.onUpdated.removeListener(onTabUpdated);
      chrome.tabs.onActivated.removeListener(onTabActivated);
    };
  }, [dispatch, addMessage, streamChatResponse, state.openedTabCount, state.groupingSuggestionShown, checkForYouTubeVideo]);

  const initContext = useCallback(async () => {
    if (typeof chrome === 'undefined' || !chrome.tabs) return;
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tab) {
        checkForYouTubeVideo(tab);
      }
    } catch (e) { console.error('initContext error:', e); }
  }, [checkForYouTubeVideo]);

  const handleSummarize = useCallback((text) => {
    dispatch({ type: 'SET_TAB', tab: 'chats' });
    addMessage('user', text);
    streamChatResponse(text, null, state.activeUrl);
  }, [dispatch, addMessage, streamChatResponse, state.activeUrl]);

  // Effects & Init logic
  useEffect(() => {
    checkAuthStatus();
    initContext();
    const cleanup = listenForChromeMessages();

    // Load notes from storage
    if (typeof chrome !== 'undefined' && chrome.storage) {
      chrome.storage.local.get(['all_notes'], (result) => {
        const allNotes = result.all_notes || [];
        const videoNotes = allNotes.filter(n => n.type === 'video');
        // Group by videoId
        const grouped = {};
        videoNotes.forEach(note => {
          const videoId = extractVideoId(note.videoUrl);
          if (videoId) {
            if (!grouped[videoId]) grouped[videoId] = [];
            grouped[videoId].push(note);
          }
        });
        Object.keys(grouped).forEach(vId => {
          dispatch({ type: 'SET_VIDEO_NOTES', videoId: vId, notes: grouped[vId] });
        });
      });
    }

    return cleanup;
  }, [checkAuthStatus, initContext, listenForChromeMessages, dispatch]);

  const handleMediaGallery = () => dispatch({ type: 'SET_TAB', tab: 'media' });

  const handleAuthAction = () => {
    if (state.isAuthenticated) logoutGoogle();
    else loginWithGoogle();
  };

  const toggleActionBar = () => {
    dispatch({ type: 'TOGGLE_ACTION_BAR' });
  };

  const { isAuthenticated, activeTab, actionBarCollapsed, tabsCarouselOpen, agentMode } = state;

  return (
    <div
      id="chatContainer"
      style={{
        width: '100%',
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--bg-primary)',
        overflow: 'hidden',
        position: 'relative',
      }}
      onClick={() => {
        dispatch({ type: 'CLOSE_PROFILE_DROPDOWN' });
        dispatch({ type: 'CLOSE_PLUS_MENU' });
      }}
    >
      {!isAuthenticated ? (
        <LoginView onLogin={loginWithGoogle} />
      ) : (
        <>
          {/* Header / Nav */}
          <Header onMediaGallery={handleMediaGallery} onAuthAction={handleAuthAction} />

          {/* Agent mode indicator */}
          {agentMode && (
            <div style={{
              textAlign: 'center', fontSize: 10, fontWeight: 500,
              color: 'var(--accent)', padding: '4px 0',
              background: 'rgba(16,163,127,0.08)',
              borderBottom: '1px solid rgba(16,163,127,0.2)',
              flexShrink: 0,
              textTransform: 'none',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 8
            }}>
              <span>⚙️ Agent mode active</span>
              <button 
                onClick={() => {
                  dispatch({ type: 'SET_AGENT_MODE', value: false });
                  if (typeof chrome !== 'undefined' && chrome.runtime) {
                    chrome.runtime.sendMessage({ type: 'STOP_AGENT_LOOP' }).catch(() => {});
                  }
                }}
                style={{
                  background: 'none',
                  border: 'none',
                  color: 'var(--text-secondary)',
                  fontSize: 10,
                  fontWeight: 600,
                  cursor: 'pointer',
                  padding: '2px 6px',
                  borderRadius: 4,
                  textDecoration: 'underline'
                }}
                onMouseEnter={e => (e.currentTarget.style.color = 'var(--text-primary)')}
                onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-secondary)')}
              >
                Deactivate
              </button>
            </div>
          )}

          {/* Main content area */}
          <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column', position: 'relative' }}>
            {activeTab === 'chats' && (
              <ChatView onSendMessage={handleSummarize} />
            )}
            {activeTab === 'notes' && (
              <NotesView onBack={() => dispatch({ type: 'SET_TAB', tab: 'chats' })} />
            )}
            {activeTab === 'history' && (
              <HistoryView onBack={() => dispatch({ type: 'SET_TAB', tab: 'chats' })} />
            )}
            {activeTab === 'media' && <MediaGallery />}
          </div>

          {/* Bottom controls area */}
          <div className="bottom-controls-wrapper">
            {/* Tools toggle (Centered) */}
            <div className="tools-toggle-container">
              <button
                id="toolsToggle"
                onClick={toggleActionBar}
                className={!actionBarCollapsed ? 'active' : ''}
                title="Toggle Tools"
              >
                {TOOLS_ICON}
              </button>
            </div>

            {/* Tabs Carousel Overlay */}
            {tabsCarouselOpen && (
              <TabsCarousel
                onTabSelected={(tabId, url, title) => {
                  saveContext(tabId, url, title);
                  // 🔹 Synchronize the active context so the chat knows which URL to use
                  dispatch({ type: 'SET_CONTEXT', tabId, url, content: null });
                }}
              />
            )}

            {/* Chat input */}
            {(activeTab === 'chats') && (
              <ChatInput currentUrl={state.activeUrl} />
            )}
          </div>

          {/* Floating Action Bar (Tool Box) */}
          <FloatingActionBar
            collapsed={actionBarCollapsed}
            currentUrl={state.activeUrl}
            onSummarize={handleSummarize}
          />
        </>
      )}

      {/* Modals */}
      <ScreenshotModal />
      <SmartSnapshotModal />
    </div>
  );
}
