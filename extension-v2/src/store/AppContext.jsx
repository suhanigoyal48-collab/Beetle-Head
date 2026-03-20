import React, { createContext, useContext, useReducer, useRef } from 'react';

// ================================================
// CONTEXT & HOOKS
// ================================================
export const AppContext = createContext(null);

export function useApp() {
    const ctx = useContext(AppContext);
    if (!ctx) throw new Error('useApp must be used within AppProvider');
    return ctx;
}

// ================================================
// INITIAL STATE
// ================================================
const initialState = {
    // Auth
    isAuthenticated: false,
    userData: null,
    accessToken: null,

    // Navigation
    activeTab: 'chats',       // 'chats' | 'notes' | 'agent' | 'history' | 'media'

    // Chat
    messages: [],
    conversationId: null,
    isStreaming: false,
    autoScroll: true,
    agentMode: false,
    domMode: false,
    preferredModel: 'openai', // 'openai' | 'ollama'

    // Image attachment
    attachedImageUrl: null,

    // UI state
    profileDropdownOpen: false,
    plusMenuOpen: false,
    screenshotModalOpen: false,
    smartSnapshotModalOpen: false,
    actionBarCollapsed: false,
    tabsCarouselOpen: false,
    tabsDrawerOpen: false,
    tabsDrawerData: null,      // { domain, tabs, icon }
    debugPanelOpen: false,
    debugLogs: [],

    // Recording
    isRecording: false,

    // Context
    activeContextTabId: null,
    activeContextContent: null,
    isManualContext: false,
    activeUrl: null,
    browserFocusedTabId: null,

    // Video progress
    videoStates: {},           // videoId -> data
    videoNotes: {},            // videoId -> Array<{ id, timestamp, text }>

    // News
    newsData: {
        articles: [],
        currentCountry: 'in',
        lastQuery: '',
        isLoading: false
    },

    // Tab counts
    openedTabCount: 0,
    groupingSuggestionShown: false,

    // Agent Progress
    agentSteps: [],
};

// ================================================
// REDUCER
// ================================================
function appReducer(state, action) {
    switch (action.type) {
        // Auth
        case 'SET_AUTH':
            return { ...state, isAuthenticated: true, userData: action.userData, accessToken: action.token };
        case 'CLEAR_AUTH':
            return { ...state, isAuthenticated: false, userData: null, accessToken: null };

        // Navigation
        case 'SET_TAB':
            return { ...state, activeTab: action.tab };

        // Messages
        case 'ADD_MESSAGE':
            return { ...state, messages: [...state.messages, action.message] };
        case 'SET_MESSAGES':
            return { ...state, messages: action.messages };
        case 'CLEAR_MESSAGES':
            return { ...state, messages: [] };
        case 'UPDATE_LAST_BOT_MESSAGE':
            const msgs = [...state.messages];
            const lastBotIdx = msgs.map(m => m.role).lastIndexOf('bot');
            if (lastBotIdx !== -1) {
                msgs[lastBotIdx] = { ...msgs[lastBotIdx], ...action.updates };
            }
            return { ...state, messages: msgs };
        case 'UPDATE_MESSAGE_BY_ID':
            return {
                ...state,
                messages: state.messages.map(m => m.id === action.id ? { ...m, ...action.updates } : m)
            };

        // Streaming
        case 'SET_STREAMING':
            return { ...state, isStreaming: action.value };
        case 'SET_CONVERSATION_ID':
            return { ...state, conversationId: action.id };
        case 'SET_PREFERRED_MODEL':
            return { ...state, preferredModel: action.value };

        // Agent/DOM modes
        case 'SET_AGENT_MODE':
            return { ...state, agentMode: action.value };
        case 'SET_DOM_MODE':
            return { ...state, domMode: action.value };

        // Agent Progress
        case 'ADD_SYSTEM_MESSAGE':
            return { ...state, messages: [...state.messages, { ...action.message, type: 'system-status' }] };
        case 'ADD_AGENT_STEP':
            return { ...state, agentSteps: [...state.agentSteps, action.step] };
        case 'CLEAR_AGENT_STEPS':
            return { ...state, agentSteps: [] };

        // Image
        case 'SET_ATTACHED_IMAGE':
            return { ...state, attachedImageUrl: action.url };
        case 'CLEAR_ATTACHED_IMAGE':
            return { ...state, attachedImageUrl: null };

        // Recording
        case 'SET_RECORDING':
            return { ...state, isRecording: action.value };

        // UI toggles
        case 'TOGGLE_PROFILE_DROPDOWN':
            return { ...state, profileDropdownOpen: !state.profileDropdownOpen };
        case 'CLOSE_PROFILE_DROPDOWN':
            return { ...state, profileDropdownOpen: false };
        case 'TOGGLE_PLUS_MENU':
            return { ...state, plusMenuOpen: !state.plusMenuOpen };
        case 'CLOSE_PLUS_MENU':
            return { ...state, plusMenuOpen: false };
        case 'OPEN_SCREENSHOT_MODAL':
            return { ...state, screenshotModalOpen: true };
        case 'CLOSE_SCREENSHOT_MODAL':
            return { ...state, screenshotModalOpen: false };
        case 'OPEN_SMART_SNAPSHOT_MODAL':
            return { ...state, smartSnapshotModalOpen: true };
        case 'CLOSE_SMART_SNAPSHOT_MODAL':
            return { ...state, smartSnapshotModalOpen: false };
        case 'TOGGLE_ACTION_BAR':
            return { ...state, actionBarCollapsed: !state.actionBarCollapsed, tabsCarouselOpen: !state.actionBarCollapsed ? !state.tabsCarouselOpen : false };
        case 'TOGGLE_TABS_CAROUSEL':
            return { ...state, tabsCarouselOpen: !state.tabsCarouselOpen };
        case 'SET_TABS_CAROUSEL':
            return { ...state, tabsCarouselOpen: action.value };
        case 'OPEN_TABS_DRAWER':
            return { ...state, tabsDrawerOpen: true, tabsDrawerData: action.data };
        case 'CLOSE_TABS_DRAWER':
            return { ...state, tabsDrawerOpen: false };
        case 'TOGGLE_DEBUG':
            return { ...state, debugPanelOpen: !state.debugPanelOpen };
        case 'ADD_DEBUG_LOG':
            return { ...state, debugLogs: [...state.debugLogs.slice(-100), action.log] };

        // Context
        case 'SET_CONTEXT':
            return {
                ...state,
                activeContextTabId: action.tabId,
                activeContextContent: action.content,
                activeUrl: action.url,
                isManualContext: true
            };
        case 'CLEAR_CONTEXT':
            return { ...state, activeContextTabId: null, activeContextContent: null, isManualContext: false };
        case 'SET_BROWSER_FOCUSED_TAB':
            return {
                ...state,
                browserFocusedTabId: action.tabId,
                activeUrl: !state.isManualContext ? action.url : state.activeUrl
            };

        // Video
        case 'UPDATE_VIDEO_STATE':
            return { ...state, videoStates: { ...state.videoStates, [action.videoId]: action.data } };
        case 'ADD_VIDEO_NOTE':
            const currentNotes = state.videoNotes[action.videoId] || [];
            return {
                ...state,
                videoNotes: {
                    ...state.videoNotes,
                    [action.videoId]: [...currentNotes, action.note].sort((a, b) => a.timestamp - b.timestamp)
                }
            };
        case 'SET_VIDEO_NOTES':
            return {
                ...state,
                videoNotes: {
                    ...state.videoNotes,
                    [action.videoId]: action.notes.sort((a, b) => a.timestamp - b.timestamp)
                }
            };
        case 'DELETE_VIDEO_NOTE':
            return {
                ...state,
                videoNotes: {
                    ...state.videoNotes,
                    [action.videoId]: (state.videoNotes[action.videoId] || []).filter(n => n.id !== action.noteId)
                }
            };

        // News
        case 'SET_NEWS_LOADING':
            return { ...state, newsData: { ...state.newsData, isLoading: action.value } };
        case 'SET_NEWS_ARTICLES':
            return {
                ...state,
                newsData: {
                    ...state.newsData,
                    articles: action.articles,
                    currentCountry: action.country || state.newsData.currentCountry,
                    lastQuery: action.query !== undefined ? action.query : state.newsData.lastQuery,
                    isLoading: false
                }
            };

        // Tab tracking
        case 'INCREMENT_TAB_COUNT':
            return { ...state, openedTabCount: state.openedTabCount + 1 };
        case 'SHOW_GROUPING_SUGGESTION':
            return { ...state, groupingSuggestionShown: true };
        case 'RESET_TAB_COUNT':
            return { ...state, openedTabCount: 0, groupingSuggestionShown: false };

        // Auto scroll
        case 'SET_AUTO_SCROLL':
            return { ...state, autoScroll: action.value };

        default:
            return state;
    }
}

export function AppProvider({ children }) {
    const [state, dispatch] = useReducer(appReducer, initialState);
    const abortControllerRef = useRef(null);

    return (
        <AppContext.Provider value={{ state, dispatch, abortControllerRef }}>
            {children}
        </AppContext.Provider>
    );
}
