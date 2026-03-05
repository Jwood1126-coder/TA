// Checklists JS

function scrollToCategory(e, id) {
    e.preventDefault();
    document.getElementById(id).scrollIntoView({ behavior: 'smooth' });
}

// Simple task toggle
async function toggleChecklist(itemId) {
    const item = document.querySelector(`[data-id="${itemId}"]`);
    try {
        const resp = await fetch(`/api/checklists/${itemId}/toggle`, { method: 'POST' });
        const data = await resp.json();
        if (data.ok) {
            item.classList.toggle('completed', data.is_completed);
            showToast(data.is_completed ? 'Done!' : 'Unmarked');
        }
    } catch (err) {
        console.error('Toggle failed:', err);
        showToast('Failed to update', 'error');
    }
}

// Expand/collapse decision item options panel
function toggleItemExpand(itemId) {
    const panel = document.getElementById(`options-${itemId}`);
    const arrow = document.getElementById(`arrow-${itemId}`);
    if (!panel) return;
    const isOpen = panel.style.display !== 'none';
    panel.style.display = isOpen ? 'none' : '';
    if (arrow) arrow.classList.toggle('open', !isOpen);
    if (!isOpen) {
        setTimeout(() => panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 50);
    }
}

// Expand/collapse individual option details
function toggleClOptionDetails(header) {
    const body = header.nextElementSibling;
    if (!body) return;
    body.style.display = body.style.display === 'none' ? '' : 'none';
}

// Update checklist item status
async function updateItemStatus(itemId, status) {
    try {
        await fetch(`/api/checklists/${itemId}/status`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status })
        });
        const item = document.querySelector(`[data-id="${itemId}"]`);
        if (!item) return;
        const badge = item.querySelector('.decision-status-badge');
        if (badge) {
            badge.className = 'decision-status-badge ' + status;
            const labels = {
                pending: 'Pending', researching: 'Researching',
                decided: 'Decided', booked: 'Booked', completed: 'Done'
            };
            badge.textContent = labels[status] || status;
        }
        if (status === 'completed') {
            item.classList.add('completed');
        }
        showToast('Status updated');
    } catch (err) {
        console.error('Status update failed:', err);
        showToast('Failed to update', 'error');
    }
}

// ---------- ChecklistOption actions ----------

async function eliminateClOption(optionId) {
    try {
        const resp = await fetch(`/api/checklist-options/${optionId}/eliminate`, { method: 'POST' });
        const data = await resp.json();
        if (data.ok) {
            const card = document.querySelector(`[data-option-id="cl-${optionId}"]`);
            if (!card) return;
            card.classList.toggle('eliminated', data.is_eliminated);
            const btn = card.querySelector('.eliminate-btn');
            if (btn) {
                btn.classList.toggle('active', data.is_eliminated);
                btn.innerHTML = data.is_eliminated ? 'Restore' : '&#x2717;';
            }
            showToast(data.is_eliminated ? 'Eliminated' : 'Restored');
        }
    } catch (err) {
        console.error('Eliminate failed:', err);
        showToast('Failed to update', 'error');
    }
}

async function selectClOption(optionId) {
    try {
        const resp = await fetch(`/api/checklist-options/${optionId}/select`, { method: 'POST' });
        if ((await resp.json()).ok) location.reload();
    } catch (err) {
        console.error('Select failed:', err);
    }
}

async function updateClOptionNotes(optionId, value) {
    try {
        await fetch(`/api/checklist-options/${optionId}/notes`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_notes: value })
        });
    } catch (err) {
        console.error('Notes update failed:', err);
    }
}

// ---------- AccommodationOption actions (reuse existing APIs) ----------

async function selectAccomOption(optionId) {
    try {
        const resp = await fetch(`/api/accommodations/${optionId}/select`, { method: 'POST' });
        if ((await resp.json()).ok) location.reload();
    } catch (err) {
        console.error('Select failed:', err);
    }
}

async function eliminateAccomOption(optionId) {
    try {
        const resp = await fetch(`/api/accommodations/${optionId}/eliminate`, { method: 'POST' });
        const data = await resp.json();
        if (data.ok) {
            const card = document.querySelector(`[data-option-id="accom-${optionId}"]`);
            if (!card) return;
            card.classList.toggle('eliminated', data.is_eliminated);
            const btn = card.querySelector('.eliminate-btn');
            if (btn) {
                btn.classList.toggle('active', data.is_eliminated);
                btn.innerHTML = data.is_eliminated ? 'Restore' : '&#x2717;';
            }
        }
    } catch (err) {
        console.error('Eliminate failed:', err);
    }
}

async function updateBookingStatus(optionId, status) {
    try {
        await fetch(`/api/accommodations/${optionId}/status`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ booking_status: status })
        });
    } catch (err) {
        console.error('Booking status update failed:', err);
    }
}

async function updateConfirmation(optionId, value) {
    try {
        await fetch(`/api/accommodations/${optionId}/status`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirmation_number: value })
        });
    } catch (err) {
        console.error('Confirmation update failed:', err);
    }
}

async function updateAccomNotes(optionId, value) {
    try {
        await fetch(`/api/accommodations/${optionId}/status`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_notes: value })
        });
    } catch (err) {
        console.error('Notes update failed:', err);
    }
}

// ---------- Delete checklist item ----------

async function deleteItem(itemId) {
    if (!confirm('Delete this item?')) return;
    try {
        const resp = await fetch(`/api/checklists/${itemId}`, { method: 'DELETE' });
        const data = await resp.json();
        if (data.ok) {
            const el = document.querySelector(`[data-id="${itemId}"]`);
            if (el) el.remove();
            showToast('Item deleted');
        } else {
            showToast(data.error || 'Cannot delete', 'error');
        }
    } catch (err) {
        console.error('Delete failed:', err);
        showToast('Failed to delete', 'error');
    }
}

// ---------- Add new checklist item ----------

function showAddItem(sectionKey) {
    const section = document.getElementById(`cat-${sectionKey}`);
    if (!section || section.querySelector('.add-item-form')) return;

    let categoryOptions = '';
    if (sectionKey === 'preparation') {
        categoryOptions = '<option value="pre_departure_month">Preparation</option>';
    } else if (sectionKey === 'packing') {
        categoryOptions = '<option value="packing_essential">Essential</option>' +
                          '<option value="packing_helpful">Helpful</option>';
    }

    const form = document.createElement('div');
    form.className = 'add-item-form';
    form.innerHTML = `
        <input type="text" placeholder="Item name" class="new-item-title"
               onkeydown="if(event.key==='Enter')submitNewItem('${sectionKey}',this)">
        <select class="new-item-category">${categoryOptions}</select>
        <div class="add-option-form-btns">
            <button onclick="submitNewItem('${sectionKey}', this)" class="select-btn-sm">Add</button>
            <button onclick="this.closest('.add-item-form').remove()" class="eliminate-btn">Cancel</button>
        </div>
    `;
    section.appendChild(form);
    form.querySelector('.new-item-title').focus();
}

async function submitNewItem(sectionKey, el) {
    const form = el.closest('.add-item-form');
    const title = form.querySelector('.new-item-title').value.trim();
    const category = form.querySelector('.new-item-category').value;
    if (!title) return;
    try {
        const resp = await fetch('/api/checklists', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, category })
        });
        if ((await resp.json()).ok) location.reload();
    } catch (err) {
        console.error('Add item failed:', err);
        showToast('Failed to add', 'error');
    }
}

// ---------- Add new option ----------

function showAddOption(itemId) {
    const panel = document.getElementById(`options-${itemId}`);
    if (!panel || panel.querySelector('.add-option-form')) return;
    const form = document.createElement('div');
    form.className = 'add-option-form';
    form.innerHTML = `
        <input type="text" placeholder="Option name" class="new-opt-name">
        <input type="text" placeholder="URL (optional)" class="new-opt-url">
        <input type="text" placeholder="Price note (optional)" class="new-opt-price">
        <div class="add-option-form-btns">
            <button onclick="submitNewOption(${itemId}, this)" class="select-btn-sm">Save</button>
            <button onclick="this.closest('.add-option-form').remove()" class="eliminate-btn">Cancel</button>
        </div>
    `;
    const addBtn = panel.querySelector('.add-option-btn');
    if (addBtn) addBtn.before(form);
    else panel.appendChild(form);
    form.querySelector('.new-opt-name').focus();
}

async function submitNewOption(itemId, btn) {
    const form = btn.closest('.add-option-form');
    const name = form.querySelector('.new-opt-name').value.trim();
    if (!name) return;
    try {
        const resp = await fetch(`/api/checklists/${itemId}/options`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name,
                url: form.querySelector('.new-opt-url').value.trim() || null,
                price_note: form.querySelector('.new-opt-price').value.trim() || null,
            })
        });
        if ((await resp.json()).ok) location.reload();
    } catch (err) {
        console.error('Add option failed:', err);
    }
}
