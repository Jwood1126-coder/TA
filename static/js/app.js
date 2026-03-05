// Japan Travel Assistant - Core JS

// Register service worker for offline support
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw.js')
        .then(reg => console.log('SW registered, scope:', reg.scope))
        .catch(err => console.warn('SW registration failed:', err));
}

// Toast notifications
function showToast(message, type = 'success') {
    let container = document.getElementById('toastContainer');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toastContainer';
        container.className = 'toast-container';
        document.body.appendChild(container);
    }
    const toast = document.createElement('div');
    toast.className = 'toast toast-' + type;
    toast.textContent = message;
    container.appendChild(toast);
    // Trigger animation
    requestAnimationFrame(() => toast.classList.add('show'));
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 2500);
}

// Socket.IO connection
const socket = io({ transports: ['websocket', 'polling'] });

socket.on('connect', () => console.log('Connected to server'));
socket.on('disconnect', () => console.log('Disconnected'));

// More menu
function toggleMore(e) {
    e.preventDefault();
    document.getElementById('moreMenu').classList.toggle('open');
}

function closeMore() {
    document.getElementById('moreMenu').classList.remove('open');
}

// Dark mode
function toggleTheme() {
    const html = document.documentElement;
    const current = html.getAttribute('data-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
    closeMore();
}

// Restore theme on load
(function() {
    const saved = localStorage.getItem('theme');
    if (saved) {
        document.documentElement.setAttribute('data-theme', saved);
    }
})();

// Swipe navigation for day view
let touchStartX = 0;
let touchStartY = 0;

document.addEventListener('touchstart', function(e) {
    touchStartX = e.changedTouches[0].screenX;
    touchStartY = e.changedTouches[0].screenY;
}, { passive: true });

document.addEventListener('touchend', function(e) {
    const dx = e.changedTouches[0].screenX - touchStartX;
    const dy = e.changedTouches[0].screenY - touchStartY;

    // Only horizontal swipe, not vertical scroll
    if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) {
        // Check if we're on a day page
        const prevBtn = document.querySelector('.day-nav-btn:first-child');
        const nextBtn = document.querySelector('.day-nav-btn:last-child');

        if (dx > 0 && prevBtn && !prevBtn.classList.contains('disabled')) {
            prevBtn.click();
        } else if (dx < 0 && nextBtn && !nextBtn.classList.contains('disabled')) {
            nextBtn.click();
        }
    }
}, { passive: true });

// Pull-to-refresh
(function() {
    let pullStartY = 0;
    let pulling = false;
    const THRESHOLD = 80;

    // Create indicator element
    const indicator = document.createElement('div');
    indicator.className = 'pull-refresh-indicator';
    indicator.innerHTML = '&#x21BB;';
    document.body.prepend(indicator);

    document.addEventListener('touchstart', function(e) {
        if (window.scrollY === 0) {
            pullStartY = e.touches[0].clientY;
            pulling = true;
        }
    }, { passive: true });

    document.addEventListener('touchmove', function(e) {
        if (!pulling) return;
        const pullDistance = e.touches[0].clientY - pullStartY;
        if (pullDistance > 0 && window.scrollY === 0) {
            const progress = Math.min(pullDistance / THRESHOLD, 1);
            indicator.style.transform = `translateY(${Math.min(pullDistance * 0.4, 50)}px)`;
            indicator.style.opacity = progress;
            if (progress >= 1) {
                indicator.classList.add('ready');
            } else {
                indicator.classList.remove('ready');
            }
        }
    }, { passive: true });

    document.addEventListener('touchend', function() {
        if (!pulling) return;
        pulling = false;
        if (indicator.classList.contains('ready')) {
            indicator.textContent = 'Refreshing...';
            indicator.style.transform = 'translateY(40px)';
            setTimeout(() => location.reload(), 200);
        } else {
            indicator.style.transform = 'translateY(-40px)';
            indicator.style.opacity = '0';
        }
        indicator.classList.remove('ready');
    }, { passive: true });
})();

// Real-time sync handlers
socket.on('activity_toggled', function(data) {
    const card = document.querySelector(`[data-id="${data.id}"]`);
    if (card) {
        const checkbox = card.querySelector('input[type="checkbox"]');
        if (checkbox) checkbox.checked = data.is_completed;
        card.classList.toggle('completed', data.is_completed);
    }
});

socket.on('checklist_toggled', function(data) {
    const item = document.querySelector(`[data-id="${data.id}"]`);
    if (item) {
        const checkbox = item.querySelector('input[type="checkbox"]');
        if (checkbox) checkbox.checked = data.is_completed;
        item.classList.toggle('completed', data.is_completed);
    }
});

socket.on('checklist_status_changed', function(data) {
    const item = document.querySelector(`[data-id="${data.id}"]`);
    if (item) {
        const badge = item.querySelector('.decision-status-badge');
        if (badge) {
            badge.className = 'decision-status-badge ' + data.status;
            const labels = {
                pending: 'Pending', researching: 'Researching',
                decided: 'Decided', booked: 'Booked', completed: 'Done'
            };
            badge.textContent = labels[data.status] || data.status;
        }
    }
});

socket.on('checklist_option_updated', function(data) {
    // Reload checklists page to reflect changes from other device
    if (window.location.pathname === '/checklists') {
        location.reload();
    }
});

socket.on('accommodation_updated', function(data) {
    // Refresh if on accommodations or checklists page
    if (window.location.pathname === '/accommodations' ||
        window.location.pathname === '/checklists') {
        location.reload();
    }
});
