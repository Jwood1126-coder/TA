function updateFlightConfirmation(flightId, value) {
    fetch(`/api/documents/flight/${flightId}/confirmation`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirmation_number: value }),
    });
}

function updateFlightStatus(flightId, status) {
    fetch(`/api/documents/flight/${flightId}/confirmation`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ booking_status: status }),
    }).then(r => r.json()).then(() => {
        // Update badge display
        const card = document.querySelector(`[data-flight-id="${flightId}"]`);
        if (!card) return;
        let badge = card.querySelector('.booking-badge');
        if (status === 'not_booked') {
            if (badge) badge.remove();
        } else {
            if (!badge) {
                badge = document.createElement('span');
                badge.className = 'booking-badge';
                card.querySelector('.flight-card-header').appendChild(badge);
            }
            badge.className = `booking-badge ${status}`;
            badge.textContent = status.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        }
    });
}
