// Itinerary-specific JS

async function toggleActivity(activityId) {
    const card = document.querySelector(`[data-id="${activityId}"]`);
    try {
        const resp = await fetch(`/api/activities/${activityId}/toggle`, {
            method: 'POST'
        });
        const data = await resp.json();
        if (data.ok) {
            card.classList.toggle('completed', data.is_completed);
            showToast(data.is_completed ? 'Marked complete' : 'Unmarked');
        } else {
            showToast('Failed to update', 'error');
        }
    } catch (err) {
        console.error('Toggle failed:', err);
        showToast('Connection error', 'error');
    }
}

function openNoteEditor(activityId, btn) {
    // Check if already open
    const existing = btn.parentElement.querySelector('.note-editor');
    if (existing) {
        existing.remove();
        return;
    }

    const card = btn.closest('.activity-card');
    const currentNote = card.querySelector('.activity-user-note');
    const currentText = currentNote ? currentNote.textContent : '';

    const editor = document.createElement('div');
    editor.className = 'note-editor';
    editor.innerHTML = `
        <textarea class="note-textarea" placeholder="Add a note...">${currentText}</textarea>
        <button class="note-save" onclick="saveActivityNote(${activityId}, this)">Save</button>
    `;
    editor.querySelector('textarea').style.cssText = 'width:100%;min-height:60px;font-size:0.85rem;margin-top:8px;';
    editor.querySelector('button').style.cssText = 'margin-top:4px;font-size:0.8rem;padding:4px 12px;';

    card.appendChild(editor);
    editor.querySelector('textarea').focus();
}

async function saveActivityNote(activityId, btn) {
    const editor = btn.parentElement;
    const text = editor.querySelector('textarea').value;

    try {
        await fetch(`/api/activities/${activityId}/notes`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ notes: text })
        });
        editor.remove();
        showToast('Note saved');
        // Update the display
        const card = document.querySelector(`[data-id="${activityId}"]`);
        let noteDiv = card.querySelector('.activity-user-note');
        if (text) {
            if (!noteDiv) {
                noteDiv = document.createElement('div');
                noteDiv.className = 'activity-user-note';
                card.querySelector('.activity-content').appendChild(noteDiv);
            }
            noteDiv.textContent = text;
        } else if (noteDiv) {
            noteDiv.remove();
        }
    } catch (err) {
        console.error('Save note failed:', err);
        showToast('Failed to save note', 'error');
    }
}

async function saveActivityMapsUrl(activityId, url) {
    try {
        const resp = await fetch(`/api/activities/${activityId}/maps-url`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ maps_url: url })
        });
        if (resp.ok) showToast('Maps link saved');
    } catch (err) {
        console.error('Save maps URL failed:', err);
    }
}

async function saveDayNotes(dayId, text) {
    try {
        const resp = await fetch(`/api/days/${dayId}/notes`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ notes: text })
        });
        if (resp.ok) showToast('Notes saved');
        else showToast('Failed to save notes', 'error');
    } catch (err) {
        console.error('Save day notes failed:', err);
        showToast('Connection error', 'error');
    }
}
