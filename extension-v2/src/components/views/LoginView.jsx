import React from 'react';
import { LogIn, ShieldCheck, Cloud } from 'lucide-react';

export default function LoginView({ onLogin }) {
    return (
        <div
            id="loginView"
            className="flex flex-col justify-center items-center text-center"
            style={{
                flex: 1,
                padding: '40px 20px',
                background: 'var(--bg-primary)',
                animation: 'fadeIn 0.5s ease'
            }}
        >
            <div style={{ width: '100%', maxWidth: '280px' }}>
                {/* Logo */}
                <div style={{ marginBottom: '32px', position: 'relative', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                    <div style={{ position: 'relative', marginBottom: '16px' }}>
                        <img 
                            src="/beetle-dark-removebg-preview.png" 
                            style={{ 
                                width: '140px', 
                                height: 'auto',
                                filter: 'drop-shadow(0 0 25px rgba(16, 163, 127, 0.3))'
                            }} 
                            alt="Beetle Logo"
                        />
                        <img 
                            src="/1771348297791-dungbeetlelogo.webp" 
                            style={{ 
                                width: '56px', 
                                height: 'auto',
                                position: 'absolute',
                                bottom: '-8px',
                                right: '-8px',
                                borderRadius: '14px',
                                border: '3px solid var(--bg-primary)',
                                boxShadow: '0 6px 16px rgba(0,0,0,0.4)'
                            }} 
                            alt="Dung Beetle Icon"
                        />
                    </div>
                    <h1 style={{ fontSize: 28, fontWeight: 800, color: 'var(--text-primary)', letterSpacing: '-0.8px' }}>
                        Beetle Head
                    </h1>
                </div>

                <p style={{ color: 'var(--text-secondary)', fontSize: 14, marginBottom: 32, lineHeight: 1.5 }}>
                    Your personal AI browser companion.
                </p>

                <button
                    id="btnLoginPrimary"
                    onClick={onLogin}
                    className="flex items-center justify-center gap-2 w-full cursor-pointer transition-all duration-200"
                    style={{
                        padding: '14px',
                        background: 'var(--accent)',
                        color: 'white',
                        border: 'none',
                        borderRadius: '12px',
                        fontSize: '15px',
                        fontWeight: 600,
                        boxShadow: '0 4px 12px rgba(16, 163, 127, 0.2)'
                    }}
                    onMouseEnter={e => {
                        e.currentTarget.style.background = 'var(--accent-hover)';
                        e.currentTarget.style.transform = 'translateY(-2px)';
                    }}
                    onMouseLeave={e => {
                        e.currentTarget.style.background = 'var(--accent)';
                        e.currentTarget.style.transform = 'translateY(0)';
                    }}
                >
                    <LogIn size={18} />
                    <span>Login with Google</span>
                </button>

                {/* Features */}
                <div style={{ marginTop: 40, display: 'flex', flexDirection: 'column', gap: 16, opacity: 0.8 }}>
                    <div className="flex items-center gap-3" style={{ color: 'var(--text-secondary)', fontSize: 13 }}>
                        <ShieldCheck size={16} style={{ color: 'var(--accent)', flexShrink: 0 }} />
                        <span>Secure JWT Auth</span>
                    </div>
                    <div className="flex items-center gap-3" style={{ color: 'var(--text-secondary)', fontSize: 13 }}>
                        <Cloud size={16} style={{ color: 'var(--accent)', flexShrink: 0 }} />
                        <span>Cloud Sync</span>
                    </div>
                </div>
            </div>
        </div>
    );
}
