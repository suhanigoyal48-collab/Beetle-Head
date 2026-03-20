// ================================================
// HELPER UTILITIES (mirrors vanilla sidebar.js utils)
// ================================================

export function isRestrictedPage(url) {
    if (!url) return true;
    return url.startsWith('chrome://') ||
        url.startsWith('chrome-extension://') ||
        url.startsWith('about:') ||
        url.startsWith('edge://') ||
        url.startsWith('file://');
}

export function escapeHTML(str) {
    if (!str) return '';
    const p = document.createElement('p');
    p.textContent = str;
    return p.innerHTML;
}

export function formatTime(seconds) {
    if (!seconds && seconds !== 0) return '0:00';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
    return `${m}:${s.toString().padStart(2, '0')}`;
}

export function extractVideoId(url) {
    if (!url) return '';
    try {
        const urlObj = new URL(url);
        if (urlObj.hostname.includes('youtu.be')) return urlObj.pathname.slice(1);
        if (urlObj.pathname.includes('/shorts/')) return urlObj.pathname.split('/shorts/')[1].split(/[?#]/)[0];
        return urlObj.searchParams.get('v') || '';
    } catch {
        const match = url.match(/(?:youtu\.be\/|youtube\.com\/(?:v\/|u\/\w\/|embed\/|watch\?v=))([^#&?]*)/);
        return (match && match[1].length === 11) ? match[1] : '';
    }
}

export function getYouTubeThumbnail(url) {
    const id = extractVideoId(url);
    return id ? `https://img.youtube.com/vi/${id}/mqdefault.jpg` : '';
}

export function truncateUrl(url) {
    try {
        const urlObj = new URL(url);
        let path = urlObj.pathname + urlObj.search;
        if (path.length > 50) path = path.substring(0, 47) + '...';
        return path === '/' ? urlObj.hostname : path;
    } catch {
        return url;
    }
}

export function extractUrls(text) {
    const urlRegex = /(https?:\/\/[^\s]+)/g;
    return text.match(urlRegex) || [];
}

export function getFileIcon(fileType) {
    const icons = { pdf: '📄', docx: '📝', image: '🖼️', video: '🎥' };
    return icons[fileType] || '📎';
}

export function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

export function getFaviconForDomain(domain) {
    const emojis = {
        'youtube.com': '▶️', 'google.com': '🔍', 'github.com': '🐙',
        'twitter.com': '🐦', 'x.com': '✖️', 'facebook.com': '👥',
        'linkedin.com': '💼', 'instagram.com': '📷', 'reddit.com': '🤖',
        'amazon.com': '📦', 'amazon.in': '📦', 'netflix.com': '🎬',
        'spotify.com': '🎵', 'stackoverflow.com': '📚', 'wikipedia.org': '📖',
    };
    return emojis[domain] || '🌐';
}

export function getFaviconUrl(url) {
    try {
        const urlObj = new URL(url);
        return `https://www.google.com/s2/favicons?domain=${urlObj.hostname}&sz=32`;
    } catch {
        return '';
    }
}

export function groupTabsByDomain(tabs) {
    const groups = {};
    tabs.forEach(tab => {
        try {
            if (!tab.url) return;
            const url = new URL(tab.url);
            const domain = url.hostname.replace(/^www\./, '');
            if (!groups[domain]) groups[domain] = [];
            groups[domain].push(tab);
        } catch { }
    });
    return groups;
}

export function getGroupEmoji(topic) {
    const topicLower = topic.toLowerCase();
    if (topicLower.includes('video') || topicLower.includes('youtube')) return '🎥';
    if (topicLower.includes('shop') || topicLower.includes('product')) return '🛍️';
    if (topicLower.includes('news')) return '📰';
    if (topicLower.includes('code') || topicLower.includes('dev')) return '💻';
    if (topicLower.includes('social')) return '👥';
    if (topicLower.includes('work') || topicLower.includes('doc')) return '📄';
    return '🌐';
}

export function hasContextKeywords(query) {
    const keywords = [
        'this page', 'current page', 'this site', 'this website',
        'summarize', 'summary', 'summarise', 'tldr',
        'what is on', 'what does this', 'tell me about this',
        'page content', 'contents', 'content', 'product', 'item',
        'article', 'this video', 'what is this', 'explain this',
        'about this', 'about it', 'tell me about'
    ];
    return keywords.some(k => lower.includes(k));
}

export function downloadJSON(data, filename) {
    try {
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename || `dom-extraction-${Date.now()}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        console.log(`[Download] Triggered download for: ${a.download}`);
    } catch (e) {
        console.error('[Download] Failed to trigger JSON download:', e);
    }
}
