// Chat JS

const chatMessages = document.getElementById('chatMessages');
const chatInput = document.getElementById('chatInput');
let selectedImage = null;
let selectedModel = localStorage.getItem('chatModel') || 'balanced';

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

function handleChatKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
}

function scrollToBottom() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function addBubble(role, text, imageUrl) {
    const bubble = document.createElement('div');
    bubble.className = `chat-bubble ${role}`;

    if (imageUrl) {
        const img = document.createElement('img');
        img.src = imageUrl;
        img.className = 'chat-image';
        img.onclick = () => window.open(imageUrl.replace('/thumbnails/thumb_', '/originals/').replace('.jpg', ''));
        bubble.appendChild(img);
    }

    const content = document.createElement('div');
    content.className = 'bubble-content';
    content.textContent = text;
    bubble.appendChild(content);
    chatMessages.appendChild(bubble);
    scrollToBottom();
    return content;
}

function sendQuickPrompt(btn) {
    chatInput.value = btn.textContent;
    sendMessage();
}

function handleImageSelect(input) {
    if (input.files && input.files[0]) {
        selectedImage = input.files[0];
        const reader = new FileReader();
        reader.onload = (e) => {
            document.getElementById('previewImg').src = e.target.result;
            document.getElementById('imagePreview').style.display = 'flex';
        };
        reader.readAsDataURL(selectedImage);
    }
}

function clearImage() {
    selectedImage = null;
    document.getElementById('chatImageInput').value = '';
    document.getElementById('imagePreview').style.display = 'none';
    document.getElementById('previewImg').src = '';
}

async function sendMessage() {
    const text = chatInput.value.trim();
    if (!text && !selectedImage) return;

    chatInput.value = '';
    chatInput.style.height = 'auto';
    chatInput.disabled = true;
    document.getElementById('sendBtn').disabled = true;

    // Remove welcome if present
    const welcome = document.querySelector('.chat-welcome');
    if (welcome) welcome.remove();

    // Add user bubble with optional image preview
    const imagePreviewUrl = selectedImage ? document.getElementById('previewImg').src : null;
    addBubble('user', text || 'Analyzing document...', imagePreviewUrl);

    // Hide image preview
    if (selectedImage) {
        document.getElementById('imagePreview').style.display = 'none';
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
            if (assistantContent.textContent !== responseBuffer) {
                assistantContent.textContent = responseBuffer;
                scrollToBottom();
            }
        }
    };
    document.addEventListener('visibilitychange', visHandler);

    try {
        let response;

        if (selectedImage) {
            const formData = new FormData();
            formData.append('message', text);
            formData.append('image', selectedImage);
            formData.append('model', selectedModel);
            formData.append('session_history', JSON.stringify(sessionHistory));
            selectedImage = null;
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
                            assistantContent.textContent = responseBuffer;
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
    }

    document.removeEventListener('visibilitychange', visHandler);
    chatInput.disabled = false;
    document.getElementById('sendBtn').disabled = false;
    chatInput.focus();
}

function clearChat() {
    sessionHistory = [];
    sessionStorage.removeItem('chatHistory');
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
}

// Scroll to bottom on load
scrollToBottom();
