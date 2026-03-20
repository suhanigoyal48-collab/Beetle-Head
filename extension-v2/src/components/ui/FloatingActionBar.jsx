import React, { useCallback } from 'react';
import { useApp } from '../../store/AppContext';

const ACTIONS = [
    { id: 'record', emoji: '🎥', label: 'Record', msgType: 'TOGGLE_SCREEN_RECORD' },
    { id: 'screenshot', emoji: '📸', label: 'Screenshot', action: 'screenshot' },
    { id: 'circleSearch', emoji: '🔍', label: 'Circle Search', msgType: 'TOGGLE_CIRCLE_SEARCH' },
    { id: 'summarize', emoji: '✍️', label: 'Summarise', action: 'summarize' },
    // { id: 'tabs', emoji: '🗂️', label: 'Tabs', action: 'tabs' },
    { id: 'groupTabs', emoji: '📂', label: 'Group Tabs', msgType: 'GROUP_TABS' },
    { id: 'news', emoji: '📰', label: 'News', msgType: 'FETCH_NEWS' },
    { id: 'darkSite', emoji: '🌗', label: 'Dark Site', msgType: 'TOGGLE_DARK_SITE' },
    { id: 'smartSnap', emoji: '📸✨', label: 'Smart Snap', action: 'smartSnap' },
];

export default function FloatingActionBar({ collapsed, currentUrl, onSummarize }) {
    const { dispatch, state } = useApp();

    const sendToBackground = useCallback((type, extra = {}) => {
        if (typeof chrome !== 'undefined' && chrome.runtime) {
            chrome.runtime.sendMessage({ type, ...extra });
        }
    }, []);

    const handleAction = useCallback((id, msgType, action) => {
        switch (action || id) {
            case 'screenshot':
                dispatch({ type: 'OPEN_SCREENSHOT_MODAL' });
                break;
            case 'summarize':
                // Send summarize message to chat
                if (onSummarize) onSummarize('Summarize this page');
                break;
            // case 'tabs':
            //     dispatch({ type: 'TOGGLE_TABS_CAROUSEL' });
            //     break;
            case 'news':
                dispatch({ type: 'SET_TAB', tab: 'chats' });
                dispatch({ type: 'ADD_MESSAGE', message: { id: Date.now(), role: 'system', type: 'news-search' } });
                break;
            case 'circleSearch':
                sendToBackground('TOGGLE_CIRCLE_SEARCH');
                break;
            case 'smartSnap':
                dispatch({ type: 'OPEN_SMART_SNAPSHOT_MODAL' });
                break;
            default:
                sendToBackground(msgType);
        }
    }, [dispatch, sendToBackground, onSummarize]);

    return (
        <div className={`floating-action-bar ${collapsed ? 'collapsed' : ''}`} id="floatingActionBar">
            {ACTIONS.map(({ id, emoji, label, msgType, action }) => (
                <button
                    key={id}
                    id={`tool${id.charAt(0).toUpperCase() + id.slice(1)}`}
                    className={`action-item ${state.isRecording && id === 'record' ? 'recording' : ''}`}
                    onClick={() => handleAction(id, msgType, action)}
                    title={label}
                >
                    <span className="action-icon">{emoji}</span>
                    <span className="action-label">{label}</span>
                </button>
            ))}
        </div>
    );
}
