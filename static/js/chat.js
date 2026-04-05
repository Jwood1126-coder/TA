// Chat JS

const chatMessages = document.getElementById('chatMessages');
const chatInput = document.getElementById('chatInput');
let selectedImages = [];
let selectedModel = localStorage.getItem('chatModel') || 'balanced';
let lastUserMessage = null; // Track last message for retry

// Session history — persists across page navigations, clears on New Chat or tab close
let sessionHistory = JSON.parse(sessionStorage.getItem('chatHistory') || '[]');

function saveSessionHistory() {
    sessionStorage.setItem('chatHistory', JSON.stringify(sessionHistory));
}

// Model selector
document.querySelectorAll('.model-pill').forEach(btn => {
    if (btn.dataset.model === selectedModel) btn.classList.add('active');
    btn.addEventListener('click', () => {
        document.querySelectorAll('.model-pill').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        selectedModel = btn.dataset.model;
        localStorage.setItem('chatModel', selectedModel);
    });
});

// Auto-growing textarea
function autoGrow(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

// Persist draft message across tab switches
chatInput.addEventListener('input', () => {
    sessionStorage.setItem('chatDraft', chatInput.value);
});
(function() {
    const draft = sessionStorage.getItem('chatDraft');
    if (draft) { chatInput.value = draft; autoGrow(chatInput); }
})();

function handleChatKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
}

function scrollToBottom() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function renderChatMarkdown(text) {
    // Lightweight markdown: links, bold, line breaks — safe for chat bubbles
    // Escape HTML first to prevent XSS
    let html = text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
    // Markdown links: [text](url) -> clickable <a> (only safe protocols)
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function(match, linkText, url) {
        if (/^(\/|https?:\/\/)/.test(url)) {
            return '<a href="' + url + '" class="chat-link">' + linkText + '</a>';
        }
        return linkText;
    });
    // Bold: **text** -> <strong>
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    // Line breaks
    html = html.replace(/\n/g, '<br>');
    return html;
}

function addBubble(role, text, imageUrls) {
    const bubble = document.createElement('div');
    bubble.className = `chat-bubble ${role}`;

    // Support single URL string or array of URLs
    const urls = imageUrls ? (Array.isArray(imageUrls) ? imageUrls : [imageUrls]) : [];
    for (const imageUrl of urls) {
        const img = document.createElement('img');
        img.src = imageUrl;
        img.className = 'chat-image';
        img.onclick = () => window.open(imageUrl.replace('/thumbnails/thumb_', '/originals/').replace('.jpg', ''));
        bubble.appendChild(img);
    }

    const content = document.createElement('div');
    content.className = 'bubble-content';
    if (role === 'assistant') {
        content.innerHTML = renderChatMarkdown(text);
    } else {
        content.textContent = text;
    }
    bubble.appendChild(content);
    chatMessages.appendChild(bubble);
    scrollToBottom();
    return content;
}

function addRetryButton() {
    const retryDiv = document.createElement('div');
    retryDiv.className = 'chat-retry';
    retryDiv.innerHTML = '<button class="retry-btn" onclick="retryLastMessage()">Retry</button>';
    chatMessages.appendChild(retryDiv);
    scrollToBottom();
}

function retryLastMessage() {
    if (!lastUserMessage) return;
    // Remove the retry button
    const retryBtns = document.querySelectorAll('.chat-retry');
    retryBtns.forEach(el => el.remove());
    // Remove the last error bubble
    const bubbles = document.querySelectorAll('.chat-bubble.assistant');
    if (bubbles.length > 0) {
        const last = bubbles[bubbles.length - 1];
        const content = last.querySelector('.bubble-content');
        if (content && content.textContent.startsWith('Error:')) {
            last.remove();
        }
    }
    // Remove the last user message from session history (it will be re-added)
    if (sessionHistory.length > 0 && sessionHistory[sessionHistory.length - 1].role === 'user') {
        sessionHistory.pop();
        saveSessionHistory();
    }
    chatInput.value = lastUserMessage;
    sendMessage();
}

function sendQuickPrompt(btn) {
    chatInput.value = btn.textContent;
    sendMessage();
}

function handleImageSelect(input) {
    if (!input.files) return;
    for (const file of input.files) {
        selectedImages.push(file);
    }
    updateImagePreview();
}

function updateImagePreview() {
    const container = document.getElementById('imagePreview');
    if (selectedImages.length === 0) {
        container.style.display = 'none';
        container.innerHTML = '';
        return;
    }
    container.style.display = 'flex';
    container.innerHTML = '';
    selectedImages.forEach((file, idx) => {
        const wrapper = document.createElement('div');
        wrapper.className = 'preview-thumb';
        const img = document.createElement('img');
        img.src = URL.createObjectURL(file);
        const removeBtn = document.createElement('button');
        removeBtn.className = 'preview-remove';
        removeBtn.textContent = '\u00d7';
        removeBtn.onclick = () => { selectedImages.splice(idx, 1); updateImagePreview(); };
        wrapper.appendChild(img);
        wrapper.appendChild(removeBtn);
        container.appendChild(wrapper);
    });
}

function clearImage() {
    selectedImages = [];
    document.getElementById('chatImageInput').value = '';
    document.getElementById('imagePreview').style.display = 'none';
    document.getElementById('imagePreview').innerHTML = '';
}

async function sendMessage() {
    const text = chatInput.value.trim();
    if (!text && selectedImages.length === 0) return;

    lastUserMessage = text; // Save for retry
    chatInput.value = '';
    sessionStorage.removeItem('chatDraft');
    chatInput.style.height = 'auto';
    chatInput.disabled = true;
    document.getElementById('sendBtn').disabled = true;

    // Remove welcome if present
    const welcome = document.querySelector('.chat-welcome');
    if (welcome) welcome.remove();

    // Add user bubble with all image previews
    const previewUrls = selectedImages.map(f => URL.createObjectURL(f));
    addBubble('user', text || 'Analyzing document...', previewUrls.length > 0 ? previewUrls : null);

    // Hide image preview
    if (selectedImages.length > 0) {
        document.getElementById('imagePreview').style.display = 'none';
        document.getElementById('imagePreview').innerHTML = '';
    }

    // Show thinking indicator
    const thinking = document.createElement('div');
    thinking.className = 'thinking-indicator';
    thinking.innerHTML = '<div class="thinking-dot"></div><div class="thinking-dot"></div><div class="thinking-dot"></div>';
    chatMessages.appendChild(thinking);
    scrollToBottom();

    let assistantContent = null;
    let responseBuffer = '';

    // Sync DOM with buffer on tab return
    const visHandler = () => {
        if (document.visibilityState === 'visible' && assistantContent && responseBuffer) {
            const rendered = renderChatMarkdown(responseBuffer);
            if (assistantContent.innerHTML !== rendered) {
                assistantContent.innerHTML = rendered;
                scrollToBottom();
            }
        }
    };
    document.addEventListener('visibilitychange', visHandler);

    try {
        let response;

        if (selectedImages.length > 0) {
            const formData = new FormData();
            formData.append('message', text);
            for (const img of selectedImages) {
                formData.append('images', img);
            }
            formData.append('model', selectedModel);
            formData.append('session_history', JSON.stringify(sessionHistory));
            selectedImages = [];
            document.getElementById('chatImageInput').value = '';

            response = await fetch('/api/chat/send', {
                method: 'POST',
                body: formData,
            });
        } else {
            response = await fetch('/api/chat/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: text,
                    model: selectedModel,
                    session_history: sessionHistory,
                }),
            });
        }

        // Check response status before reading stream
        if (!response.ok) {
            thinking.remove();
            let errorMsg = `Server error (${response.status})`;
            try {
                const errData = await response.json();
                if (errData.error) errorMsg = errData.error;
            } catch (e) { /* ignore parse failure */ }
            assistantContent = addBubble('assistant', '');
            assistantContent.textContent = 'Error: ' + errorMsg;
            addRetryButton();
            document.removeEventListener('visibilitychange', visHandler);
            chatInput.disabled = false;
            document.getElementById('sendBtn').disabled = false;
            chatInput.focus();
            return;
        }

        // Track user message in session
        sessionHistory.push({ role: 'user', content: text });
        saveSessionHistory();

        const reader = response.body.getReader();
        const decoder = new TextDecoder();

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;

            const chunk = decoder.decode(value);
            const lines = chunk.split('\n');

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        if (data.text) {
                            responseBuffer += data.text;
                            if (!assistantContent) {
                                thinking.remove();
                                assistantContent = addBubble('assistant', '');
                            }
                            assistantContent.innerHTML = renderChatMarkdown(responseBuffer);
                            scrollToBottom();
                        }
                        if (data.processing) {
                            if (!assistantContent) {
                                thinking.remove();
                                assistantContent = addBubble('assistant', '');
                            }
                            const proc = document.createElement('div');
                            proc.className = 'processing-indicator';
                            proc.textContent = data.processing;
                            assistantContent.parentNode.insertBefore(proc, assistantContent);
                            scrollToBottom();
                        }
                        if (data.error) {
                            if (!assistantContent) {
                                thinking.remove();
                                assistantContent = addBubble('assistant', '');
                            }
                            assistantContent.textContent = 'Error: ' + data.error;
                            addRetryButton();
                        }
                        if (data.done) {
                            document.querySelectorAll('.processing-indicator').forEach(el => el.remove());
                            // Track assistant response in session history
                            if (responseBuffer) {
                                sessionHistory.push({ role: 'assistant', content: responseBuffer });
                                saveSessionHistory();
                            }
                        }
                    } catch (e) {
                        // Ignore parse errors on partial chunks
                    }
                }
            }
        }
    } catch (err) {
        thinking.remove();
        if (responseBuffer && assistantContent) {
            assistantContent.textContent = responseBuffer + '\n\n[Connection lost — partial response shown]';
        } else {
            if (!assistantContent) {
                assistantContent = addBubble('assistant', '');
            }
            assistantContent.textContent = 'Connection error. Please try again.';
        }
        addRetryButton();
    }

    document.removeEventListener('visibilitychange', visHandler);
    chatInput.disabled = false;
    document.getElementById('sendBtn').disabled = false;
    chatInput.focus();
}

function clearChat() {
    sessionHistory = [];
    sessionStorage.removeItem('chatHistory');
    // Also clear server-side chat history so old messages don't come back
    fetch('/api/chat/history', { method: 'DELETE' }).catch(() => {});
    chatMessages.innerHTML = `
        <div class="chat-welcome">
            <p>Ask me anything about your Japan trip!</p>
            <p class="chat-welcome-sub">Upload screenshots of confirmations, bookings, or tickets and I'll update your itinerary automatically.</p>
            <div class="quick-prompts">
                <button class="prompt-btn" onclick="sendQuickPrompt(this)">What should we eat tonight?</button>
                <button class="prompt-btn" onclick="sendQuickPrompt(this)">Translate something for me</button>
                <button class="prompt-btn" onclick="sendQuickPrompt(this)">Suggest a modification to tomorrow</button>
                <button class="prompt-btn" onclick="sendQuickPrompt(this)">What's our budget status?</button>
            </div>
        </div>`;
}

// Restore chat bubbles from session history on page load
if (sessionHistory.length > 0) {
    const welcome = document.querySelector('.chat-welcome');
    if (welcome) welcome.remove();
    for (const m of sessionHistory) {
        addBubble(m.role, m.content);
    }
} else {
    // Fallback: fetch from server if session is empty (e.g. tab was killed on mobile)
    fetch('/api/chat/history').then(r => r.json()).then(messages => {
        if (messages.length > 0) {
            const welcome = document.querySelector('.chat-welcome');
            if (welcome) welcome.remove();
            for (const m of messages) {
                addBubble(m.role, m.content);
            }
            // Don't add to sessionHistory — this is historical, not active context
        }
    }).catch(() => { /* silently ignore fetch errors */ });
}

// Scroll to bottom on load
scrollToBottom();

// Handle mobile keyboard resize — pin body to visible viewport
if (window.visualViewport) {
    function adjustForKeyboard() {
        const vv = window.visualViewport;
        // Size body to visible area so flex layout fills above the keyboard
        document.body.style.height = vv.height + 'px';
        // Prevent browser from scrolling the page up behind the keyboard
        // (which is what causes the input to appear at the "top of screen")
        document.body.style.position = 'fixed';
        document.body.style.top = vv.offsetTop + 'px';
        document.body.style.left = '0';
        document.body.style.right = '0';
        scrollToBottom();
    }

    window.visualViewport.addEventListener('resize', adjustForKeyboard);
    window.visualViewport.addEventListener('scroll', adjustForKeyboard);

    // Reset when keyboard dismissed
    chatInput.addEventListener('blur', () => {
        setTimeout(() => {
            document.body.style.height = '';
            document.body.style.position = '';
            document.body.style.top = '';
            document.body.style.left = '';
            document.body.style.right = '';
            scrollToBottom();
        }, 100);
    });
}
