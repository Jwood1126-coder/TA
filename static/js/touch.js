// Touch Gestures Library - drag-to-reorder & swipe-to-delete
// Usage: initTouchList(containerSelector, { onReorder, onDelete, onSwipeAction })

(function() {
    'use strict';

    // --- Drag-to-Reorder ---
    function initDragReorder(container, options) {
        const getItems = () => Array.from(container.querySelectorAll(options.itemSelector));
        let dragItem = null;
        let placeholder = null;
        let dragClone = null;
        let startY = 0;
        let startX = 0;
        let offsetY = 0;
        let isDragging = false;
        let longPressTimer = null;
        const LONG_PRESS_MS = 400;
        const DRAG_THRESHOLD = 8;

        container.addEventListener('touchstart', onTouchStart, { passive: false });
        container.addEventListener('touchmove', onTouchMove, { passive: false });
        container.addEventListener('touchend', onTouchEnd, { passive: true });
        container.addEventListener('touchcancel', onTouchEnd, { passive: true });

        // Also support drag handles - if present, skip long-press
        container.addEventListener('touchstart', function(e) {
            const handle = e.target.closest('.drag-handle');
            if (handle) {
                const item = handle.closest(options.itemSelector);
                if (item) {
                    e.preventDefault();
                    startDrag(item, e.touches[0]);
                }
            }
        }, { passive: false });

        function onTouchStart(e) {
            // Don't intercept touches on interactive elements
            const tag = e.target.tagName;
            if (['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'A'].includes(tag)) return;
            if (e.target.closest('button, a, input, textarea, select, .drag-handle')) return;

            const item = e.target.closest(options.itemSelector);
            if (!item || !container.contains(item)) return;

            const touch = e.touches[0];
            startX = touch.clientX;
            startY = touch.clientY;

            // Long-press to start dragging
            longPressTimer = setTimeout(() => {
                startDrag(item, touch);
            }, LONG_PRESS_MS);
        }

        function startDrag(item, touch) {
            isDragging = true;
            dragItem = item;
            const rect = item.getBoundingClientRect();
            offsetY = touch.clientY - rect.top;

            // Haptic feedback
            if (navigator.vibrate) navigator.vibrate(30);

            // Create a floating clone
            dragClone = item.cloneNode(true);
            dragClone.className = item.className + ' drag-clone';
            dragClone.style.cssText = `
                position: fixed;
                left: ${rect.left}px;
                top: ${rect.top}px;
                width: ${rect.width}px;
                z-index: 9999;
                pointer-events: none;
                opacity: 0.92;
                transform: scale(1.03);
                box-shadow: 0 8px 32px rgba(0,0,0,0.18);
                transition: transform 0.15s, box-shadow 0.15s;
            `;
            document.body.appendChild(dragClone);

            // Create placeholder
            placeholder = document.createElement('div');
            placeholder.className = 'drag-placeholder';
            placeholder.style.height = rect.height + 'px';
            item.parentNode.insertBefore(placeholder, item);

            // Hide original
            item.classList.add('drag-original-hidden');
        }

        function onTouchMove(e) {
            if (longPressTimer) {
                const touch = e.touches[0];
                const dx = Math.abs(touch.clientX - startX);
                const dy = Math.abs(touch.clientY - startY);
                if (dx > DRAG_THRESHOLD || dy > DRAG_THRESHOLD) {
                    clearTimeout(longPressTimer);
                    longPressTimer = null;
                }
            }

            if (!isDragging || !dragClone) return;
            e.preventDefault();

            const touch = e.touches[0];
            const y = touch.clientY - offsetY;
            dragClone.style.top = y + 'px';

            // Find which item we're hovering over
            const items = getItems();
            for (const it of items) {
                if (it === dragItem || it.classList.contains('drag-original-hidden')) continue;
                const r = it.getBoundingClientRect();
                const mid = r.top + r.height / 2;
                if (touch.clientY < mid) {
                    it.parentNode.insertBefore(placeholder, it);
                    break;
                } else if (it === items[items.length - 1] ||
                           (items.indexOf(it) === items.length - 1)) {
                    it.parentNode.insertBefore(placeholder, it.nextSibling);
                }
            }
        }

        function onTouchEnd() {
            if (longPressTimer) {
                clearTimeout(longPressTimer);
                longPressTimer = null;
            }

            if (!isDragging) return;

            // Move original to placeholder position
            if (placeholder && placeholder.parentNode) {
                placeholder.parentNode.insertBefore(dragItem, placeholder);
                placeholder.remove();
            }
            dragItem.classList.remove('drag-original-hidden');

            if (dragClone) {
                dragClone.remove();
                dragClone = null;
            }

            // Compute new order
            const items = getItems();
            const ids = items.map(el => {
                return el.dataset.optionId || el.dataset.id;
            }).filter(Boolean);

            if (options.onReorder) {
                options.onReorder(ids, dragItem);
            }

            isDragging = false;
            dragItem = null;
            placeholder = null;
        }
    }

    // --- Swipe-to-Reveal Actions ---
    function initSwipeActions(container, options) {
        let swipeItem = null;
        let startX = 0;
        let startY = 0;
        let currentX = 0;
        let isSwiping = false;
        let isScrolling = false;
        const SWIPE_THRESHOLD = 70;
        const MAX_SWIPE = 120;

        container.addEventListener('touchstart', onStart, { passive: true });
        container.addEventListener('touchmove', onMove, { passive: false });
        container.addEventListener('touchend', onEnd, { passive: true });
        container.addEventListener('touchcancel', onEnd, { passive: true });

        function onStart(e) {
            // Close any previously opened swipe
            closeAllSwipes(container, options.itemSelector);

            const item = e.target.closest(options.itemSelector);
            if (!item) return;
            // Don't swipe if inside action buttons
            if (e.target.closest('.swipe-actions')) return;

            swipeItem = item;
            const touch = e.touches[0];
            startX = touch.clientX;
            startY = touch.clientY;
            currentX = 0;
            isSwiping = false;
            isScrolling = false;

            // Ensure swipe action buttons exist
            ensureSwipeActions(item, options);
        }

        function onMove(e) {
            if (!swipeItem || isScrolling) return;
            const touch = e.touches[0];
            const dx = touch.clientX - startX;
            const dy = touch.clientY - startY;

            // Decide: horizontal swipe or vertical scroll?
            if (!isSwiping && Math.abs(dy) > Math.abs(dx) && Math.abs(dy) > 10) {
                isScrolling = true;
                return;
            }

            if (Math.abs(dx) > 10) {
                isSwiping = true;
            }

            if (!isSwiping) return;
            e.preventDefault();

            // Only allow left swipe (negative dx) to reveal actions
            currentX = Math.max(-MAX_SWIPE, Math.min(0, dx));
            const content = swipeItem.querySelector('.swipe-content');
            if (content) {
                content.style.transform = `translateX(${currentX}px)`;
                content.style.transition = 'none';
            }

            // Show/hide action buttons based on swipe amount
            const actions = swipeItem.querySelector('.swipe-actions');
            if (actions) {
                actions.style.opacity = Math.min(1, Math.abs(currentX) / 40);
            }
        }

        function onEnd() {
            if (!swipeItem) return;
            const content = swipeItem.querySelector('.swipe-content');
            const actions = swipeItem.querySelector('.swipe-actions');

            if (content) {
                content.style.transition = 'transform 0.25s cubic-bezier(0.25, 0.46, 0.45, 0.94)';
                if (Math.abs(currentX) > SWIPE_THRESHOLD) {
                    // Snap open to show actions
                    content.style.transform = `translateX(-${MAX_SWIPE}px)`;
                    if (actions) {
                        actions.style.opacity = '1';
                        actions.classList.add('visible');
                    }
                    swipeItem.classList.add('swipe-open');
                } else {
                    // Snap back
                    content.style.transform = 'translateX(0)';
                    if (actions) {
                        actions.style.opacity = '0';
                        actions.classList.remove('visible');
                    }
                    swipeItem.classList.remove('swipe-open');
                }
            }

            isSwiping = false;
            swipeItem = null;
        }
    }

    function ensureSwipeActions(item, options) {
        if (item.querySelector('.swipe-actions')) return;

        // Wrap existing content if not wrapped
        if (!item.querySelector('.swipe-content')) {
            const content = document.createElement('div');
            content.className = 'swipe-content';
            while (item.firstChild) {
                content.appendChild(item.firstChild);
            }
            item.appendChild(content);
        }

        // Create action buttons
        const actions = document.createElement('div');
        actions.className = 'swipe-actions';

        if (options.actions) {
            options.actions.forEach(action => {
                const btn = document.createElement('button');
                btn.className = 'swipe-action-btn ' + (action.className || '');
                btn.innerHTML = action.icon || action.label;
                btn.title = action.label;
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const id = item.dataset.optionId || item.dataset.id;
                    action.handler(id, item);
                    closeSwipe(item);
                });
                actions.appendChild(btn);
            });
        }

        item.appendChild(actions);
        item.classList.add('swipe-enabled');
    }

    function closeSwipe(item) {
        const content = item.querySelector('.swipe-content');
        const actions = item.querySelector('.swipe-actions');
        if (content) {
            content.style.transition = 'transform 0.25s cubic-bezier(0.25, 0.46, 0.45, 0.94)';
            content.style.transform = 'translateX(0)';
        }
        if (actions) {
            actions.style.opacity = '0';
            actions.classList.remove('visible');
        }
        item.classList.remove('swipe-open');
    }

    function closeAllSwipes(container, itemSelector) {
        container.querySelectorAll(itemSelector + '.swipe-open').forEach(closeSwipe);
    }

    // --- Delete animation ---
    function animateRemove(item) {
        item.style.transition = 'all 0.3s ease-out';
        item.style.transform = 'translateX(-100%)';
        item.style.opacity = '0';
        item.style.maxHeight = item.offsetHeight + 'px';
        requestAnimationFrame(() => {
            item.style.maxHeight = '0';
            item.style.padding = '0';
            item.style.margin = '0';
            item.style.borderWidth = '0';
        });
        setTimeout(() => item.remove(), 350);
    }

    // --- Public API ---
    window.TouchGestures = {
        initDragReorder,
        initSwipeActions,
        animateRemove,
        closeAllSwipes,

        // Convenience: init both on a container
        initList: function(container, options) {
            if (typeof container === 'string') {
                container = document.querySelector(container);
            }
            if (!container) return;

            if (options.reorder !== false) {
                initDragReorder(container, {
                    itemSelector: options.itemSelector,
                    onReorder: options.onReorder
                });
            }

            if (options.swipe !== false && options.actions) {
                initSwipeActions(container, {
                    itemSelector: options.itemSelector,
                    actions: options.actions
                });
            }
        }
    };
})();
