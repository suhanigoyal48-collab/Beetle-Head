import React from 'react';
import { marked } from 'marked';
import TypingIndicator from './TypingIndicator';
import AgentActions from './AgentActions';
import RichContent from './RichContent';

marked.setOptions({ breaks: true, gfm: true });

export default function MessageBubble({ message, onSendMessage }) {
    const { role, content, html, isStreaming, isError, contextAnalysis,
        videoAnalysis, richBlocks, agentActions, isAgent, screenshot, video } = message;

    if (role === 'user') {
        return (
            <div
                className="user-msg"
                style={{
                    background: 'var(--bg-secondary)',
                    color: 'var(--text-primary)',
                    padding: '10px 14px',
                    borderRadius: 18,
                    maxWidth: '85%',
                    alignSelf: 'flex-end',
                    marginBottom: 12,
                    fontSize: 14,
                    lineHeight: 1.5,
                    wordWrap: 'break-word',
                }}
            >
                {content}
            </div>
        );
    }

    // Bot message
    const renderedHtml = html || (content ? marked.parse(content) : '');

    return (
        <div
            className="bot-msg"
            style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 8,
                maxWidth: '100%',
                marginBottom: 12,
                padding: '4px 0',
            }}
        >
            {/* Context badge */}
            {contextAnalysis && (
                <div className="context-indicator">
                    <div className={`context-badge ${contextAnalysis.needs_context ? 'active' : 'inactive'}`}>
                        {contextAnalysis.needs_context ? '📄 Using Page Context' : 'ℹ️ General Response'}
                    </div>
                    {contextAnalysis.reason && (
                        <div className="context-reason">{contextAnalysis.reason}</div>
                    )}
                </div>
            )}

            {/* Main text */}
            <div
                className="bot-text"
                style={{
                    color: 'var(--text-primary)',
                    lineHeight: 1.6,
                    fontSize: 14,
                    fontWeight: 400,
                    wordWrap: 'break-word',
                    padding: '0 4px',
                }}
                dangerouslySetInnerHTML={renderedHtml ? { __html: renderedHtml } : undefined}
            >
                {!renderedHtml && isStreaming ? null : !renderedHtml && !content ? null : undefined}
            </div>

            {/* Agent Planning Status */}
            {isAgent && (
                <div className="agent-planning-status">
                    <div className="agent-planning-header">🧠 Acting on Page...</div>
                </div>
            )}

            {/* Typing indicator while streaming, no text yet */}
            {isStreaming && !content && <TypingIndicator />}

            {/* Media content */}
            {screenshot && (
                <div style={{
                    marginTop: 8,
                    borderRadius: 12,
                    overflow: 'hidden',
                    border: '1px solid var(--border)',
                    background: 'var(--bg-secondary)',
                    width: '100%',
                }}>
                    <img
                        src={screenshot}
                        alt="Captured Screen"
                        style={{ width: '100%', height: 'auto', display: 'block', cursor: 'pointer' }}
                        onClick={() => window.open(screenshot, '_blank')}
                    />
                </div>
            )}

            {video && (
                <div style={{
                    marginTop: 8,
                    borderRadius: 12,
                    overflow: 'hidden',
                    border: '1px solid var(--border)',
                    background: 'var(--bg-secondary)',
                    width: '100%',
                }}>
                    <video
                        src={video}
                        controls
                        style={{ width: '100%', height: 'auto', display: 'block', maxHeight: 300 }}
                    />
                </div>
            )}

            {/* Agent actions */}
            {agentActions && agentActions.length > 0 && (
                <AgentActions actions={agentActions} onExecute={onSendMessage} />
            )}

            {/* Rich content blocks */}
            {richBlocks && <RichContent blocks={richBlocks} onAction={onSendMessage} />}
        </div>
    );
}
