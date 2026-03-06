// Chat JS

const chatMessages = document.getElementById('chatMessages');
const chatInput = document.getElementById('chatInput');
let selectedImages = [];
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

    chatInput.value = '';
    chatInput.style.height = 'auto';
    chatInput.disabled = true;
    document.getElementById('sendBtn').disabled = true;

    // Remove welcome if present
    const welcome = document.querySelector('.chat-welcome');
    if (welcome) welcome.remove();

    // Add user bubble with first image preview if any
    const previewUrl = selectedImages.length > 0 ? URL.createObjectURL(selectedImages[0]) : null;
    const imageCount = selectedImages.length > 1 ? ` [${selectedImages.length} images]` : '';
    addBubble('user', (text || 'Analyzing document...') + imageCount, previewUrl);

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
            if (assistantContent.textContent !== responseBuffer) {
                assistantContent.textContent = responseBuffer;
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

// Handle mobile keyboard resize — keep input bar visible and layout stable
if (window.visualViewport) {
    const chatContainer = document.querySelector('.chat-container');
    const inputBar = document.querySelector('.chat-input-bar');

    function adjustForKeyboard() {
        const vh = window.visualViewport.height;
        const offset = window.visualViewport.offsetTop;
        // Resize container to visible area
        chatContainer.style.height = vh + 'px';
        // Counteract any iOS scroll offset so input stays at bottom
        chatContainer.style.transform = `translateY(${offset}px)`;
        scrollToBottom();
    }

    window.visualViewport.addEventListener('resize', adjustForKeyboard);
    window.visualViewport.addEventListener('scroll', adjustForKeyboard);

    // Reset when keyboard fully dismissed (input blurred)
    chatInput.addEventListener('blur', () => {
        setTimeout(() => {
            chatContainer.style.height = '';
            chatContainer.style.transform = '';
            inputBar.style.transform = '';
            scrollToBottom();
        }, 100);
    });
}
