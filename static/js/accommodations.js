// Accommodations JS

function toggleOptionDetails(header) {
    const details = header.nextElementSibling;
    details.style.display = details.style.display === 'none' ? '' : 'none';
}

function scrollToLocation(e, id) {
    e.preventDefault();
    document.getElementById(id).scrollIntoView({ behavior: 'smooth' });
}

async function selectOption(optionId) {
    try {
        const resp = await fetch(`/api/accommodations/${optionId}/select`, {
            method: 'POST'
        });
        const data = await resp.json();
        if (data.ok) {
            location.reload();
        }
    } catch (err) {
        console.error('Select failed:', err);
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
        console.error('Update status failed:', err);
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
        console.error('Update confirmation failed:', err);
    }
}

async function updateOptionNotes(optionId, value) {
    try {
        await fetch(`/api/accommodations/${optionId}/status`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_notes: value })
        });
    } catch (err) {
        console.error('Update notes failed:', err);
    }
}

async function reorderOption(optionId, direction) {
    try {
        const resp = await fetch(`/api/accommodations/${optionId}/reorder`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ direction })
        });
        const data = await resp.json();
        if (data.ok) location.reload();
    } catch (err) {
        console.error('Reorder failed:', err);
    }
}

async function updateOptionUrl(optionId) {
    const input = document.getElementById(`url-${optionId}`);
    try {
        await fetch(`/api/accommodations/${optionId}/status`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ booking_url: input.value })
        });
        location.reload();
    } catch (err) {
        console.error('Update URL failed:', err);
    }
}

async function deleteOption(optionId, name) {
    if (!confirm(`Remove "${name}" from options?`)) return;
    try {
        const resp = await fetch(`/api/accommodations/${optionId}/delete`, {
            method: 'DELETE'
        });
        const data = await resp.json();
        if (data.ok) location.reload();
    } catch (err) {
        console.error('Delete failed:', err);
    }
}
