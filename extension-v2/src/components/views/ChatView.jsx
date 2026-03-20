import React, { useRef, useEffect, useCallback } from 'react';
import { Maximize2 } from 'lucide-react';
import { useApp } from '../../store/AppContext';
import MessageBubble from '../chat/MessageBubble';
import VideoPreviewCard from '../chat/VideoPreviewCard';
import SystemStatusMessage from '../chat/SystemStatusMessage';
import TypingIndicator from '../chat/TypingIndicator';
import NewsSearchInterface from '../chat/NewsSearchInterface';
import NewsCard from '../chat/NewsCard';
import GroupedTabsMessage from '../chat/GroupedTabsMessage';

export default function ChatView({ onSendMessage }) {
    const { state, dispatch } = useApp();
    const { messages, isStreaming, autoScroll } = state;
    const messagesEndRef = useRef(null);
    const messagesRef = useRef(null);

    // Auto-scroll
    useEffect(() => {
        if (autoScroll && messagesEndRef.current) {
            messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
        }
    }, [messages, autoScroll, isStreaming]);

    const handleScroll = useCallback(() => {
        if (!messagesRef.current) return;
        const { scrollTop, scrollHeight, clientHeight } = messagesRef.current;
        const isAtBottom = scrollHeight - scrollTop - clientHeight < 50;
        dispatch({ type: 'SET_AUTO_SCROLL', value: isAtBottom });
    }, [dispatch]);

    return (
        <div
            id="messages"
            ref={messagesRef}
            onScroll={handleScroll}
            style={{
                flex: 1,
                overflowY: 'auto',
                padding: '12px 16px',
                display: 'flex',
                flexDirection: 'column',
                gap: 0,
                background: 'var(--bg-primary)',
            }}
        >
            {/* Initial greeting */}
            {messages.length === 0 && !isStreaming && (
                <div style={{ 
                    flex: 1, 
                    display: 'flex', 
                    flexDirection: 'column', 
                    alignItems: 'center', 
                    justifyContent: 'center',
                    padding: '40px 20px',
                    textAlign: 'center',
                    animation: 'fadeIn 0.8s ease-out'
                }}>
                    <div style={{ position: 'relative', marginBottom: '32px' }}>
                        <img 
                            src="/beetle-dark-removebg-preview.png" 
                            style={{ 
                                width: '120px', 
                                height: 'auto',
                                filter: 'drop-shadow(0 0 20px rgba(16, 163, 127, 0.2))'
                            }} 
                            alt="Beetle Logo"
                        />
                        <img 
                            src="/1771348297791-dungbeetlelogo.webp" 
                            style={{ 
                                width: '48px', 
                                height: 'auto',
                                position: 'absolute',
                                bottom: '-10px',
                                right: '-10px',
                                borderRadius: '12px',
                                border: '2px solid var(--bg-primary)',
                                boxShadow: '0 4px 12px rgba(0,0,0,0.3)'
                            }} 
                            alt="Dung Beetle Icon"
                        />
                    </div>
                    <p style={{ color: 'var(--text-secondary)', fontSize: '14px', maxWidth: '240px', lineHeight: '1.6' }}>
                        Elevate your browsing experience
                    </p> 
                </div>
            )}

            {/* Message list */}
            {messages.map((msg) => {
                if (msg.role === 'system' && msg.videoPreview) {
                    return (
                        <VideoPreviewCard
                            key={msg.id}
                            video={msg.videoPreview}
                            onSummarizeVideo={onSendMessage}
                            onSummarizePage={onSendMessage}
                        />
                    );
                }
                if (msg.role === 'system' && msg.type === 'news-search') {
                    return (
                        <div key={msg.id}>
                            <NewsSearchInterface />
                            {state.newsData.articles.length > 0 && (
                                <div style={{ marginTop: '12px' }}>
                                    <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '8px', padding: '0 4px' }}>
                                        Latest Results:
                                    </div>
                                    {state.newsData.articles.map((article, idx) => (
                                        <NewsCard key={`${msg.id}-art-${idx}`} article={article} onAskAI={onSendMessage} />
                                    ))}
                                </div>
                            )}
                        </div>
                    );
                }
                if (msg.role === 'tabs-grouped' || msg.groups) {
                    return (
                        <GroupedTabsMessage key={msg.id} groups={msg.groups} />
                    );
                }
                if (msg.role === 'system-status') {
                    return (
                        <SystemStatusMessage key={msg.id} content={msg.content} />
                    );
                }
                if (msg.role === 'tab-preview' && msg.tabData) {
                    return (
                        <div key={msg.id} className="tab-preview-msg" style={{
                            margin: '8px 0',
                            padding: '12px',
                            background: 'var(--bg-secondary)',
                            border: '1px solid var(--border)',
                            borderRadius: '12px',
                            display: 'flex',
                            gap: '12px',
                            alignItems: 'center'
                        }}>
                            <img
                                src={msg.tabData.favIconUrl || 'vite.svg'}
                                style={{ width: 32, height: 32, borderRadius: '6px', objectFit: 'cover' }}
                                alt="favicon"
                            />
                            <div style={{ flex: 1, overflow: 'hidden' }}>
                                <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--text-primary)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                    {msg.tabData.title}
                                </div>
                                <div style={{ fontSize: 11, color: 'var(--text-secondary)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                    {msg.tabData.url}
                                </div>
                            </div>
                        </div>
                    );
                }
                if (msg.role === 'snapshot-progress') {
                    return (
                        <div key={msg.id} style={{
                            margin: '8px 0',
                            padding: '16px',
                            background: 'var(--bg-secondary)',
                            border: '1px solid var(--border)',
                            borderRadius: '12px',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: '12px'
                        }}>
                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                                <div style={{ fontWeight: 600, fontSize: 14, color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: 8 }}>
                                    <span>{msg.format === 'png' ? '🖼️' : (msg.format === 'markdown' ? '📝' : '📄')}</span>
                                    <span>Generating {(msg.format || '').toUpperCase()}</span>
                                </div>
                                <div style={{ fontSize: 13, color: 'var(--accent)', fontWeight: 600 }}>
                                    {msg.completed ? '100%' : `${msg.progress || 0}%`}
                                </div>
                            </div>

                            {/* Progress bar container */}
                            {!msg.completed && !msg.isError && (
                                <div style={{ width: '100%', height: 6, background: 'var(--bg-tertiary)', borderRadius: 3, overflow: 'hidden' }}>
                                    <div style={{ 
                                        width: `${msg.progress || 0}%`, 
                                        height: '100%', 
                                        background: 'var(--accent)',
                                        transition: 'width 0.3s ease'
                                    }} />
                                </div>
                            )}

                            <div style={{ fontSize: 12, color: msg.isError ? 'var(--error)' : 'var(--text-secondary)' }}>
                                {msg.statusText}
                            </div>

                            {msg.completed && msg.downloadUrl && (
                                <div style={{ display: 'flex', justifyContent: 'flex-start', marginTop: 4 }}>
                                    <a 
                                        href={msg.downloadUrl} 
                                        download={msg.filename}
                                        style={{
                                            padding: '8px 16px',
                                            background: 'var(--accent)',
                                            color: '#fff',
                                            borderRadius: '6px',
                                            fontSize: 13,
                                            fontWeight: 600,
                                            textDecoration: 'none',
                                            display: 'flex',
                                            alignItems: 'center',
                                            gap: 6,
                                            cursor: 'pointer'
                                        }}
                                    >
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                                        Download {msg.format.toUpperCase()}
                                    </a>
                                </div>
                            )}
                        </div>
                    );
                }
                return (
                    <MessageBubble key={msg.id} message={msg} onSendMessage={onSendMessage} />
                );
            })}

            {/* Streaming indicator on a new bot turn */}
            {isStreaming && messages.length > 0 && messages[messages.length - 1]?.role === 'user' && (
                <div style={{ padding: '4px 0 12px' }}>
                    <TypingIndicator />
                </div>
            )}

            <div ref={messagesEndRef} />
        </div>
    );
}
