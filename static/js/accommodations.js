// Accommodations JS

function toggleOptionDetails(header) {
    // Don't toggle if we just did a drag
    if (header.closest('.drag-clone')) return;
    const details = header.parentElement.querySelector('.option-details');
    if (!details) return;
    details.style.display = details.style.display === 'none' ? '' : 'none';
}

// Initialize touch gestures on each location section
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.accom-section').forEach(section => {
        const locId = section.id.replace('loc-', '');

        // Init drag-to-reorder
        if (window.TouchGestures) {
            TouchGestures.initDragReorder(section, {
                itemSelector: '.option-card',
                onReorder: function(ids, movedItem) {
                    // ids is array of option IDs in new visual order
                    saveReorder(locId, ids);
                }
            });
        }
    });

    // Close swipes when tapping elsewhere
    document.addEventListener('touchstart', function(e) {
        if (!e.target.closest('.swipe-actions') && !e.target.closest('.option-card')) {
            document.querySelectorAll('.option-card.swipe-open').forEach(card => {
                const content = card.querySelector('.swipe-content');
                if (content) {
                    content.style.transition = 'transform 0.25s ease';
                    content.style.transform = 'translateX(0)';
                }
                card.classList.remove('swipe-open');
                const actions = card.querySelector('.swipe-actions');
                if (actions) actions.style.opacity = '0';
            });
        }
    }, { passive: true });
});

// Save new order via batch reorder endpoint
async function saveReorder(locationId, optionIds) {
    try {
        const resp = await fetch('/api/accommodations/reorder-batch', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ location_id: locationId, order: optionIds })
        });
        const data = await resp.json();
        if (data.ok) {
            // Update rank numbers visually without reload
            optionIds.forEach((id, idx) => {
                const card = document.querySelector(`[data-option-id="${id}"]`);
                if (card) {
                    const rank = card.querySelector('.option-rank');
                    if (rank) rank.textContent = '#' + (idx + 1);
                }
            });
            showToast('Reordered');
        }
    } catch (err) {
        console.error('Reorder failed:', err);
        showToast('Reorder failed', 'error');
    }
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
        // Pikachu celebration when booked or confirmed
        if (status === 'booked' || status === 'confirmed') {
            const card = document.querySelector(`[data-option-id="${optionId}"]`);
            const name = card ? card.querySelector('.option-name')?.textContent?.trim() : 'Hotel';
            if (window.showBookingCelebration) {
                showBookingCelebration(name);
            }
        }
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

async function updateOptionAddress(optionId) {
    const input = document.getElementById(`addr-${optionId}`);
    try {
        await fetch(`/api/accommodations/${optionId}/status`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ address: input.value })
        });
        showToast('Address saved');
    } catch (err) {
        console.error('Update address failed:', err);
    }
}

async function updateOptionMapsUrl(optionId) {
    const input = document.getElementById(`maps-${optionId}`);
    try {
        await fetch(`/api/accommodations/${optionId}/status`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ maps_url: input.value })
        });
        showToast('Maps link saved');
    } catch (err) {
        console.error('Update maps URL failed:', err);
    }
}

async function uploadBookingImage(optionId, input) {
    const file = input.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('image', file);

    try {
        const resp = await fetch(`/api/accommodations/${optionId}/upload-image`, {
            method: 'POST',
            body: formData
        });
        const data = await resp.json();
        if (data.ok) {
            // Update the preview inline
            const section = document.getElementById(`bookingImg-${optionId}`);
            let preview = section.querySelector('.booking-image-preview');
            if (!preview) {
                preview = document.createElement('div');
                preview.className = 'booking-image-preview';
                section.insertBefore(preview, section.firstChild);
            }
            preview.innerHTML = `
                <img src="/photos/originals/${data.filename}"
                     alt="Booking confirmation"
                     onclick="viewBookingImage(this.src)">
                <button class="booking-image-remove"
                        onclick="removeBookingImage(${optionId})"
                        title="Remove image">&times;</button>
            `;
            showToast('Image attached');
        }
    } catch (err) {
        console.error('Upload failed:', err);
        showToast('Upload failed', 'error');
    }
    input.value = ''; // reset for re-upload
}

async function removeBookingImage(optionId) {
    if (!confirm('Remove booking image?')) return;
    try {
        const resp = await fetch(`/api/accommodations/${optionId}/delete-image`, {
            method: 'DELETE'
        });
        const data = await resp.json();
        if (data.ok) {
            const section = document.getElementById(`bookingImg-${optionId}`);
            const preview = section.querySelector('.booking-image-preview');
            if (preview) preview.remove();
            showToast('Image removed');
        }
    } catch (err) {
        console.error('Delete image failed:', err);
    }
}

function viewBookingImage(src) {
    const viewer = document.createElement('div');
    viewer.className = 'photo-viewer';
    viewer.innerHTML = `
        <img src="${src}" alt="Booking confirmation">
        <button class="photo-close">&times;</button>
    `;
    viewer.addEventListener('click', () => viewer.remove());
    document.body.appendChild(viewer);
}

function toggleAddForm(locationId) {
    const form = document.getElementById(`addForm-${locationId}`);
    form.style.display = form.style.display === 'none' ? '' : 'none';
}

async function saveNewOption(locationId) {
    const name = document.getElementById(`addName-${locationId}`).value.trim();
    if (!name) {
        showToast('Name is required', 'error');
        return;
    }
    const data = {
        name,
        property_type: document.getElementById(`addType-${locationId}`).value.trim(),
        price_low: document.getElementById(`addPriceLow-${locationId}`).value || null,
        price_high: document.getElementById(`addPriceHigh-${locationId}`).value || null,
        booking_url: document.getElementById(`addUrl-${locationId}`).value.trim() || null,
        maps_url: document.getElementById(`addMaps-${locationId}`).value.trim() || null,
    };
    try {
        const resp = await fetch(`/api/accommodations/${locationId}/add`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        const result = await resp.json();
        if (result.ok) {
            location.reload();
        } else {
            showToast(result.error || 'Failed to add', 'error');
        }
    } catch (err) {
        console.error('Add option failed:', err);
        showToast('Failed to add option', 'error');
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
