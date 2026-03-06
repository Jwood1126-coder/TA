// Japan Travel Assistant - Core JS

// Hard refresh: clear SW caches and reload
function hardRefresh() {
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.getRegistrations().then(regs => {
            regs.forEach(r => r.unregister());
        });
        caches.keys().then(keys => {
            Promise.all(keys.map(k => caches.delete(k))).then(() => {
                location.reload();
            });
        });
    } else {
        location.reload();
    }
}

// Register service worker for offline support
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw.js?v=47')
        .then(reg => {
            console.log('SW registered, scope:', reg.scope);
            reg.addEventListener('updatefound', () => {
                const newSW = reg.installing;
                newSW.addEventListener('statechange', () => {
                    if (newSW.state === 'activated') {
                        console.log('New SW activated, reloading...');
                        location.reload();
                    }
                });
            });
        })
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
    let startY = 0;
    let currentY = 0;
    let pulling = false;
    let refreshing = false;
    const THRESHOLD = 70;
    const MAX_PULL = 90;

    // Create indicator bar at top of page
    const ptr = document.createElement('div');
    ptr.className = 'ptr';
    ptr.innerHTML = '<div class="ptr-spinner"></div><span class="ptr-text">Pull to refresh</span>';
    document.body.prepend(ptr);

    const spinner = ptr.querySelector('.ptr-spinner');
    const text = ptr.querySelector('.ptr-text');

    function isAtTop() {
        return window.scrollY <= 0 &&
               document.documentElement.scrollTop <= 0;
    }

    document.addEventListener('touchstart', function(e) {
        if (refreshing) return;
        if (isAtTop()) {
            startY = e.touches[0].clientY;
            pulling = true;
            ptr.classList.remove('ptr-reset');
        }
    }, { passive: true });

    document.addEventListener('touchmove', function(e) {
        if (!pulling || refreshing) return;
        currentY = e.touches[0].clientY;
        const dist = currentY - startY;

        if (dist > 0 && isAtTop()) {
            // Dampen the pull distance
            const pullPx = Math.min(dist * 0.45, MAX_PULL);
            ptr.style.height = pullPx + 'px';
            ptr.classList.add('ptr-visible');

            const progress = Math.min(dist / THRESHOLD, 1);
            spinner.style.transform = `rotate(${progress * 360}deg)`;
            spinner.style.opacity = progress;

            if (progress >= 1) {
                ptr.classList.add('ptr-ready');
                text.textContent = 'Release to refresh';
            } else {
                ptr.classList.remove('ptr-ready');
                text.textContent = 'Pull to refresh';
            }
        } else if (dist <= 0) {
            pulling = false;
            ptr.style.height = '0';
            ptr.classList.remove('ptr-visible', 'ptr-ready');
        }
    }, { passive: true });

    document.addEventListener('touchend', function() {
        if (!pulling || refreshing) return;
        pulling = false;

        if (ptr.classList.contains('ptr-ready')) {
            refreshing = true;
            ptr.style.height = '44px';
            ptr.classList.add('ptr-refreshing');
            ptr.classList.remove('ptr-ready');
            text.textContent = 'Refreshing…';
            spinner.style.opacity = '1';
            setTimeout(() => location.reload(), 300);
        } else {
            ptr.classList.add('ptr-reset');
            ptr.style.height = '0';
            ptr.classList.remove('ptr-visible', 'ptr-ready');
        }
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

// Track when we're making our own accommodation edits
window._accomEditActive = false;

socket.on('accommodation_updated', function(data) {
    // Skip reload if we triggered this update ourselves (editing fields, celebration, etc.)
    if (window._accomEditActive) return;
    if (document.querySelector('.pika-celebrate')) return;
    // Refresh if on accommodations, checklists, or home page (another device made a change)
    if (window.location.pathname === '/accommodations') {
        // Use scroll-preserving reload if available (from accommodations.js)
        if (typeof reloadKeepScroll === 'function') reloadKeepScroll();
        else location.reload();
    } else if (window.location.pathname === '/checklists' ||
               window.location.pathname === '/') {
        location.reload();
    }
});

// Backup / Restore
function showBackupPanel(e) {
    if (e) e.preventDefault();
    closeMore();
    document.getElementById('backupPanel').style.display = '';
    // Load server backups list
    fetch('/api/backup/list').then(r => r.json()).then(data => {
        const el = document.getElementById('serverBackups');
        if (!data.ok || !data.backups.length) {
            el.innerHTML = '<p style="font-size:0.85rem; color:var(--pico-muted-color);">No server backups available.</p>';
            return;
        }
        el.innerHTML = '<p style="font-size:0.8rem; font-weight:600; margin-bottom:6px;">Server Auto-Backups:</p>' +
            data.backups.map(b =>
                `<div style="display:flex; justify-content:space-between; align-items:center; padding:4px 0; font-size:0.8rem;">
                    <span>${b.name.replace('japan_trip_', '').replace('.db', '')} (${b.size_kb}KB)</span>
                    <button onclick="restoreServerBackup('${b.name}')" style="font-size:0.75rem; padding:2px 8px; cursor:pointer;">Restore</button>
                </div>`
            ).join('');
    });
}

function closeBackupPanel() {
    document.getElementById('backupPanel').style.display = 'none';
}

async function restoreFromFile(input) {
    const file = input.files[0];
    if (!file) return;
    if (!confirm(`Restore database from "${file.name}"? Current data will be backed up first.`)) {
        input.value = '';
        return;
    }
    const form = new FormData();
    form.append('backup', file);
    try {
        const resp = await fetch('/api/backup/restore', { method: 'POST', body: form });
        const data = await resp.json();
        if (data.ok) {
            showToast('Database restored! Reloading...');
            setTimeout(() => location.reload(), 1000);
        } else {
            showToast(data.error || 'Restore failed', 'error');
        }
    } catch (err) {
        showToast('Restore failed', 'error');
    }
    input.value = '';
}

async function restoreServerBackup(name) {
    if (!confirm(`Restore from backup "${name}"? Current data will be backed up first.`)) return;
    try {
        const resp = await fetch(`/api/backup/restore-server/${name}`, { method: 'POST' });
        const data = await resp.json();
        if (data.ok) {
            showToast('Restored! Reloading...');
            setTimeout(() => location.reload(), 1000);
        } else {
            showToast(data.error || 'Restore failed', 'error');
        }
    } catch (err) {
        showToast('Restore failed', 'error');
    }
}

// Currency converter
function toggleCurrencyConverter() {
    const el = document.getElementById('currencyConverter');
    if (!el) return;
    el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

function convertCurrency(from) {
    const rateEl = document.querySelector('.info-bar-currency');
    if (!rateEl) return;
    const rate = parseFloat(rateEl.dataset.rate);
    if (!rate) return;

    const usdEl = document.getElementById('usdInput');
    const jpyEl = document.getElementById('jpyInput');
    if (!usdEl || !jpyEl) return;

    if (from === 'usd' && usdEl.value) {
        jpyEl.value = Math.round(parseFloat(usdEl.value) * rate);
    } else if (from === 'jpy' && jpyEl.value) {
        usdEl.value = (parseFloat(jpyEl.value) / rate).toFixed(2);
    } else {
        usdEl.value = '';
        jpyEl.value = '';
    }
}
