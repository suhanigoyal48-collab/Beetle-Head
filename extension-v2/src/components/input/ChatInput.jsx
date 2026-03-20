import React, { useRef, useState, useEffect, useCallback } from 'react';
import { Send, Square, Plus, MessageSquarePlus, Upload, Camera, X } from 'lucide-react';
import { useApp } from '../../store/AppContext';
import { useStreaming } from '../../hooks/useStreaming';
import { useMedia } from '../../hooks/useMedia';
import { isRestrictedPage } from '../../utils/helpers';

export default function ChatInput({ currentUrl }) {
    const { state, dispatch } = useApp();
    const { isStreaming, attachedImageUrl, plusMenuOpen, agentMode } = state;
    const { streamChatResponse, streamAgentManifest, abortStream, addMessage } = useStreaming();
    const { uploadMedia } = useMedia();
    const inputRef = useRef(null);
    const fileInputRef = useRef(null);
    const [uploadProgress, setUploadProgress] = useState(0);
    const [isUploading, setIsUploading] = useState(false);
    const [previewLabel, setPreviewLabel] = useState('');

    // Auto-grow textarea
    const autoGrow = useCallback(() => {
        if (!inputRef.current) return;
        inputRef.current.style.height = 'auto';
        inputRef.current.style.height = Math.min(inputRef.current.scrollHeight, 160) + 'px';
    }, []);

    const clearPreview = useCallback(() => {
        dispatch({ type: 'CLEAR_ATTACHED_IMAGE' });
        setUploadProgress(0);
        setIsUploading(false);
        setPreviewLabel('');
    }, [dispatch]);

    const handleSend = useCallback(async () => {
        const text = inputRef.current ? inputRef.current.value.trim() : '';
        if (!text && !attachedImageUrl) return;

        dispatch({ type: 'SET_AUTO_SCROLL', value: true });

        // Build user message content
        const userContent = attachedImageUrl ? (text ? `${text}\n\n[Image Attached]` : '[Image Attached]') : text;
        addMessage('user', userContent, { imageUrl: attachedImageUrl });

        if (inputRef.current) {
            inputRef.current.value = '';
            inputRef.current.style.height = 'auto';
        }
        clearPreview();

        // Switch to chats tab if not there
        dispatch({ type: 'SET_TAB', tab: 'chats' });

        if (agentMode) {
            // Get active tab for agent
            let tabId = null;
            if (typeof chrome !== 'undefined' && chrome.tabs) {
                const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
                tabId = tab?.id;
            }
            streamAgentManifest(text, tabId);
        } else {
            streamChatResponse(text, attachedImageUrl, currentUrl);
        }
    }, [attachedImageUrl, addMessage, dispatch, clearPreview, agentMode, streamChatResponse, streamAgentManifest, currentUrl]);

    const handleKeyDown = useCallback((e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            if (isStreaming) { abortStream(); return; }
            handleSend();
        }
    }, [isStreaming, handleSend, abortStream]);

    const handleNewChat = useCallback(() => {
        dispatch({ type: 'CLEAR_MESSAGES' });
        dispatch({ type: 'SET_CONVERSATION_ID', id: null });
        clearPreview();
        dispatch({ type: 'CLOSE_PLUS_MENU' });
    }, [dispatch, clearPreview]);

    const handleFileUpload = useCallback(async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        dispatch({ type: 'CLOSE_PLUS_MENU' });

        const reader = new FileReader();
        reader.onload = async (ev) => {
            setIsUploading(true);
            setPreviewLabel('Uploading to cloud...');

            const base64 = ev.target.result;
            const fileType = file.type.startsWith('image') ? 'image' : file.type.includes('pdf') ? 'pdf' : 'docx';

            // Simulate progress
            let progress = 0;
            const interval = setInterval(() => {
                progress += Math.random() * 15;
                if (progress > 90) progress = 90;
                setUploadProgress(Math.floor(progress));
            }, 200);

            try {
                const result = await uploadMedia(base64, file.name, fileType, 'sidebar');
                clearInterval(interval);
                setUploadProgress(100);

                if (result.status === 'success') {
                    dispatch({ type: 'SET_ATTACHED_IMAGE', url: result.file_url });
                    setPreviewLabel('Image attached — Type your query below');
                    setIsUploading(false);
                    setTimeout(() => setUploadProgress(0), 500);
                    inputRef.current?.focus();
                }
            } catch (err) {
                clearInterval(interval);
                setPreviewLabel('❌ Upload failed');
                setIsUploading(false);
            }
        };
        reader.readAsDataURL(file);
    }, [uploadMedia, dispatch]);

    return (
        <div
            className="input-wrapper"
            style={{
                padding: '8px 12px 10px',
                background: 'var(--bg-primary)',
                borderTop: '1px solid var(--border)',
                flexShrink: 0,
                position: 'relative',
            }}
        >


            <div
                style={{
                    display: 'flex',
                    alignItems: 'flex-end',
                    gap: 8,
                    background: 'var(--bg-secondary)',
                    borderRadius: 16,
                    padding: '8px 8px 8px 12px',
                    border: '1px solid var(--border)',
                    position: 'relative',
                }}
            >
                {/* Plus button */}
                <div style={{ position: 'relative' }}>
                    <button
                        id="plusMenuBtn"
                        onClick={(e) => {
                            e.stopPropagation();
                            dispatch({ type: 'TOGGLE_PLUS_MENU' });
                        }}
                        title="More options"
                        style={{
                            background: 'none',
                            border: 'none',
                            width: 32,
                            height: 32,
                            borderRadius: 8,
                            cursor: 'pointer',
                            color: 'var(--text-secondary)',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            flexShrink: 0,
                            transition: 'color 0.2s',
                        }}
                    >
                        <Plus size={20} />
                    </button>

                    {/* Plus menu */}
                    {plusMenuOpen && (
                        <div
                            id="plusMenu"
                            style={{
                                position: 'absolute',
                                bottom: 40,
                                left: 0,
                                background: 'var(--bg-secondary)',
                                border: '1px solid var(--border)',
                                borderRadius: 12,
                                padding: 6,
                                display: 'flex',
                                flexDirection: 'column',
                                gap: 2,
                                minWidth: 150,
                                boxShadow: '0 8px 20px rgba(0,0,0,0.3)',
                                animation: 'dropdownIn 0.15s ease',
                                zIndex: 1000,
                            }}
                        >
                            <PlusMenuItem icon={<MessageSquarePlus size={15} />} label="New Chat" onClick={handleNewChat} />
                            <PlusMenuItem
                                icon={<Upload size={15} />}
                                label="Upload Image"
                                onClick={() => { fileInputRef.current?.click(); dispatch({ type: 'CLOSE_PLUS_MENU' }); }}
                            />
                            <PlusMenuItem
                                icon={<Camera size={15} />}
                                label="Take Screenshot"
                                onClick={() => { dispatch({ type: 'OPEN_SCREENSHOT_MODAL' }); dispatch({ type: 'CLOSE_PLUS_MENU' }); }}
                            />
                        </div>
                    )}
                </div>

                {/* Hidden File Input */}
                <input ref={fileInputRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={handleFileUpload} />

                {/* Internal Image Thumbnail */}
                {attachedImageUrl && (
                    <div style={{
                        position: 'relative',
                        width: 40,
                        height: 40,
                        borderRadius: 8,
                        overflow: 'hidden',
                        border: '1px solid var(--border)',
                        flexShrink: 0
                    }}>
                        <img 
                            src={attachedImageUrl} 
                            style={{ width: '100%', height: '100%', objectFit: 'cover' }} 
                            alt="Preview" 
                        />
                        <button
                            onClick={clearPreview}
                            style={{
                                position: 'absolute',
                                top: 2,
                                right: 2,
                                background: 'rgba(0,0,0,0.5)',
                                border: 'none',
                                borderRadius: '50%',
                                padding: 2,
                                cursor: 'pointer',
                                color: 'white',
                                display: 'flex'
                            }}
                        >
                            <X size={12} />
                        </button>
                    </div>
                )}

                {/* Textarea */}
                <textarea
                    id="chatInput"
                    ref={inputRef}
                    rows={1}
                    placeholder={agentMode ? "Tell me what to do on this page..." : "Message Quick Open..."}
                    onInput={autoGrow}
                    onKeyDown={handleKeyDown}
                    style={{
                        flex: 1,
                        background: 'none',
                        border: 'none',
                        outline: 'none',
                        color: 'var(--text-primary)',
                        fontSize: 14,
                        lineHeight: 1.5,
                        resize: 'none',
                        maxHeight: 160,
                        overflowY: 'auto',
                        fontFamily: 'inherit',
                        padding: '4px 0',
                    }}
                />

                {/* Send / Stop button */}
                <button
                    id="sendBtn"
                    onClick={isStreaming ? abortStream : handleSend}
                    title={isStreaming ? 'Stop' : 'Send'}
                    style={{
                        background: 'var(--accent)',
                        border: 'none',
                        width: 36,
                        height: 36,
                        borderRadius: 10,
                        cursor: 'pointer',
                        color: 'white',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        flexShrink: 0,
                        transition: 'background 0.2s',
                    }}
                    onMouseEnter={e => (e.currentTarget.style.background = 'var(--accent-hover)')}
                    onMouseLeave={e => (e.currentTarget.style.background = 'var(--accent)')}
                >
                    {isStreaming ? <Square size={16} /> : <Send size={16} />}
                </button>
            </div>
        </div>
    );
}

function PlusMenuItem({ icon, label, onClick }) {
    return (
        <button
            onClick={onClick}
            style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '8px 10px',
                background: 'none',
                border: 'none',
                borderRadius: 8,
                cursor: 'pointer',
                color: 'var(--text-primary)',
                fontSize: 13,
                width: '100%',
                textAlign: 'left',
                transition: 'background 0.15s',
            }}
            onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-primary)')}
            onMouseLeave={e => (e.currentTarget.style.background = 'none')}
        >
            {icon}
            <span>{label}</span>
        </button>
    );
}
