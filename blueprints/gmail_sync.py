"""Gmail sync blueprint — UI and API for syncing travel bookings from Gmail."""

import threading
from datetime import datetime
from flask import Blueprint, render_template, jsonify, request, current_app
from models import db, GmailSyncLog, PendingGmailChange

gmail_sync_bp = Blueprint('gmail_sync', __name__)

# Background sync lock
_sync_lock = threading.Lock()
_sync_running = False


@gmail_sync_bp.route('/gmail-sync')
def gmail_sync_view():
    """Gmail sync review page."""
    pending = PendingGmailChange.query.filter_by(status='pending').order_by(
        PendingGmailChange.detected_at.desc()).all()
    recent_approved = PendingGmailChange.query.filter_by(status='approved').order_by(
        PendingGmailChange.reviewed_at.desc()).limit(20).all()
    recent_skipped = PendingGmailChange.query.filter_by(status='skipped').order_by(
        PendingGmailChange.detected_at.desc()).limit(10).all()
    last_sync = GmailSyncLog.query.order_by(
        GmailSyncLog.started_at.desc()).first()

    # Check if Gmail is connected
    from services.gmail_sync import get_gmail_credentials
    gmail_connected = get_gmail_credentials() is not None

    return render_template('gmail_sync.html',
                           pending=pending,
                           recent_approved=recent_approved,
                           recent_skipped=recent_skipped,
                           last_sync=last_sync,
                           gmail_connected=gmail_connected)


@gmail_sync_bp.route('/api/gmail/sync', methods=['POST'])
def trigger_sync():
    """Trigger a Gmail sync (manual or from auto-scheduler)."""
    global _sync_running
    if _sync_running:
        return jsonify({'ok': False, 'error': 'Sync already in progress'}), 409

    app = current_app._get_current_object()

    def run():
        global _sync_running
        with _sync_lock:
            _sync_running = True
            try:
                from services.gmail_sync import run_sync
                result = run_sync(app)
                # Notify UI
                from extensions import socketio
                socketio.emit('gmail_sync_complete', result)
            finally:
                _sync_running = False

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    return jsonify({'ok': True, 'message': 'Sync started'})


@gmail_sync_bp.route('/api/gmail/status')
def sync_status():
    """Get current sync status and pending change count."""
    pending_count = PendingGmailChange.query.filter_by(status='pending').count()
    last_sync = GmailSyncLog.query.order_by(
        GmailSyncLog.started_at.desc()).first()

    from services.gmail_sync import get_gmail_credentials
    gmail_connected = get_gmail_credentials() is not None

    return jsonify({
        'gmail_connected': gmail_connected,
        'sync_running': _sync_running,
        'pending_count': pending_count,
        'last_sync': {
            'started_at': last_sync.started_at.isoformat() if last_sync else None,
            'status': last_sync.status if last_sync else None,
            'emails_found': last_sync.emails_found if last_sync else 0,
            'changes_detected': last_sync.changes_detected if last_sync else 0,
        } if last_sync else None,
    })


@gmail_sync_bp.route('/api/gmail/pending')
def pending_changes():
    """Get all pending changes for review."""
    pending = PendingGmailChange.query.filter_by(status='pending').order_by(
        PendingGmailChange.detected_at.desc()).all()
    return jsonify([{
        'id': c.id,
        'email_subject': c.email_subject,
        'email_from': c.email_from,
        'email_date': c.email_date,
        'change_type': c.change_type,
        'entity_type': c.entity_type,
        'entity_id': c.entity_id,
        'description': c.description,
        'proposed_data': c.proposed_data,
        'current_data': c.current_data,
        'detected_at': c.detected_at.isoformat() if c.detected_at else None,
    } for c in pending])


@gmail_sync_bp.route('/api/gmail/approve/<int:change_id>', methods=['POST'])
def approve_change(change_id):
    """Approve and apply a pending change."""
    from services.gmail_sync import apply_change
    result = apply_change(change_id)
    return jsonify(result)


@gmail_sync_bp.route('/api/gmail/reject/<int:change_id>', methods=['POST'])
def reject_change(change_id):
    """Reject a pending change."""
    change = PendingGmailChange.query.get_or_404(change_id)
    if change.status != 'pending':
        return jsonify({'ok': False, 'error': 'Change is not pending'}), 400
    change.status = 'rejected'
    change.reviewed_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True, 'message': f'Rejected: {change.description}'})


@gmail_sync_bp.route('/api/gmail/approve-all', methods=['POST'])
def approve_all():
    """Approve and apply all pending changes."""
    from services.gmail_sync import apply_change
    pending = PendingGmailChange.query.filter_by(status='pending').all()
    results = []
    for change in pending:
        result = apply_change(change.id)
        results.append({'id': change.id, **result})
    return jsonify({'ok': True, 'results': results, 'count': len(results)})


def start_auto_sync(app, interval_hours=2):
    """Start background auto-sync thread.

    Called from app factory. Syncs every interval_hours (default 2h).
    Frequency is high enough to catch boarding passes (sent ~24h before flight).
    """
    import time

    def auto_sync_loop():
        while True:
            time.sleep(interval_hours * 3600)
            try:
                from services.gmail_sync import run_sync, get_gmail_credentials
                if get_gmail_credentials():
                    result = run_sync(app)
                    if result.get('changes_proposed', 0) > 0:
                        with app.app_context():
                            from extensions import socketio
                            socketio.emit('gmail_sync_complete', result)
            except Exception:
                pass

    thread = threading.Thread(target=auto_sync_loop, daemon=True)
    thread.start()
