import React from 'react';
import { Image, LogOut, LogIn, Cpu, Globe } from 'lucide-react';
import { useApp } from '../../store/AppContext';

export default function ProfileDropdown({ onMediaGallery, onAuth }) {
    const { state, dispatch } = useApp();
    const { userData, isAuthenticated, preferredModel } = state;

    const setModel = (model) => {
        dispatch({ type: 'SET_PREFERRED_MODEL', value: model });
    };

    return (
        <div className="profile-dropdown" onClick={e => e.stopPropagation()}>
            <div className="dropdown-header">
                <div className="user-email" id="displayEmail">
                    {isAuthenticated && userData ? userData.email : 'Not logged in'}
                </div>
            </div>

            <div className="dropdown-divider" />

            <div className="dropdown-section">
                <div className="section-label">AI Model</div>
                <div className="model-toggle">
                    <button 
                        className={`toggle-btn ${preferredModel === 'openai' ? 'active' : ''}`}
                        onClick={() => setModel('openai')}
                    >
                        <Globe size={14} />
                        <span>OpenAI</span>
                    </button>
                    <button 
                        className={`toggle-btn ${preferredModel === 'ollama' ? 'active' : ''}`}
                        onClick={() => setModel('ollama')}
                    >
                        <Cpu size={14} />
                        <span>Ollama</span>
                    </button>
                </div>
            </div>

            <div className="dropdown-divider" />

            <button className="dropdown-item" id="btnMedia" onClick={onMediaGallery}>
                <Image size={16} />
                <span>Media Gallery</span>
            </button>

            <div className="dropdown-divider" />

            <button className="dropdown-item" id="btnAuth" onClick={onAuth}>
                {isAuthenticated ? <LogOut size={16} /> : <LogIn size={16} />}
                <span id="authText">{isAuthenticated ? 'Logout' : 'Login with Google'}</span>
            </button>
        </div>
    );
}
