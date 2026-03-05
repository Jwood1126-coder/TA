from flask import Blueprint, render_template, jsonify, request
from models import db, AccommodationLocation, AccommodationOption, ChecklistItem

accommodations_bp = Blueprint('accommodations', __name__)


@accommodations_bp.route('/accommodations')
def accommodations_view():
    locations = AccommodationLocation.query.order_by(
        AccommodationLocation.sort_order).all()
    return render_template('accommodations.html', locations=locations)


@accommodations_bp.route('/api/accommodations/<int:option_id>/select',
                          methods=['POST'])
def select_option(option_id):
    option = AccommodationOption.query.get_or_404(option_id)
    # Deselect all others in this location
    AccommodationOption.query.filter_by(
        location_id=option.location_id).update({'is_selected': False})
    option.is_selected = True
    db.session.commit()

    from app import socketio
    socketio.emit('accommodation_updated', {
        'location_id': option.location_id,
        'selected_id': option.id,
    })

    return jsonify({'ok': True})


@accommodations_bp.route('/api/accommodations/<int:option_id>/eliminate',
                          methods=['POST'])
def eliminate_option(option_id):
    option = AccommodationOption.query.get_or_404(option_id)
    option.is_eliminated = not option.is_eliminated
    db.session.commit()

    from app import socketio
    socketio.emit('accommodation_updated', {
        'location_id': option.location_id,
        'option_id': option.id,
        'is_eliminated': option.is_eliminated,
    })
    return jsonify({'ok': True, 'is_eliminated': option.is_eliminated})


@accommodations_bp.route('/api/accommodations/<int:option_id>/delete',
                          methods=['DELETE'])
def delete_option(option_id):
    option = AccommodationOption.query.get_or_404(option_id)
    loc_id = option.location_id
    db.session.delete(option)
    # Re-rank remaining options
    remaining = AccommodationOption.query.filter_by(
        location_id=loc_id).order_by(AccommodationOption.rank).all()
    for i, opt in enumerate(remaining, 1):
        opt.rank = i
    db.session.commit()

    from app import socketio
    socketio.emit('accommodation_updated', {'location_id': loc_id})
    return jsonify({'ok': True})


@accommodations_bp.route('/api/accommodations/<int:option_id>/reorder',
                          methods=['PUT'])
def reorder_option(option_id):
    option = AccommodationOption.query.get_or_404(option_id)
    data = request.get_json()
    direction = data.get('direction')  # 'up' or 'down'
    siblings = AccommodationOption.query.filter_by(
        location_id=option.location_id
    ).order_by(AccommodationOption.rank).all()
    idx = next((i for i, o in enumerate(siblings) if o.id == option.id), None)
    if idx is None:
        return jsonify({'ok': False}), 400
    if direction == 'up' and idx > 0:
        siblings[idx].rank, siblings[idx-1].rank = siblings[idx-1].rank, siblings[idx].rank
    elif direction == 'down' and idx < len(siblings) - 1:
        siblings[idx].rank, siblings[idx+1].rank = siblings[idx+1].rank, siblings[idx].rank
    db.session.commit()

    from app import socketio
    socketio.emit('accommodation_updated', {'location_id': option.location_id})
    return jsonify({'ok': True})


VALID_BOOKING_STATUSES = {'not_booked', 'researching', 'booked', 'confirmed', 'cancelled'}


@accommodations_bp.route('/api/accommodations/<int:option_id>/status',
                          methods=['PUT'])
def update_status(option_id):
    option = AccommodationOption.query.get_or_404(option_id)
    data = request.get_json()
    new_status = data.get('booking_status')
    if new_status is not None:
        if new_status not in VALID_BOOKING_STATUSES:
            return jsonify({'ok': False, 'error': f'Invalid status: {new_status}'}), 400
        option.booking_status = new_status
    option.confirmation_number = data.get('confirmation_number',
                                          option.confirmation_number)
    option.user_notes = data.get('user_notes', option.user_notes)
    if 'booking_url' in data:
        option.booking_url = data['booking_url'] or None
    if 'address' in data:
        option.address = data['address'] or None

    # Sync booking status to linked checklist item
    if new_status is not None:
        _sync_checklist_status(option)

    db.session.commit()

    from app import socketio
    socketio.emit('accommodation_updated', {
        'location_id': option.location_id,
        'option_id': option.id,
        'booking_status': option.booking_status,
    })

    return jsonify({'ok': True})


def _sync_checklist_status(option):
    """Keep the linked ChecklistItem status in sync with accommodation booking."""
    cl_item = ChecklistItem.query.filter_by(
        accommodation_location_id=option.location_id).first()
    if not cl_item:
        return
    status_map = {
        'booked': 'booked',
        'confirmed': 'booked',
        'not_booked': 'pending',
        'researching': 'researching',
        'cancelled': 'pending',
    }
    new_cl_status = status_map.get(option.booking_status)
    if new_cl_status and cl_item.status != new_cl_status:
        cl_item.status = new_cl_status
        if new_cl_status == 'booked':
            cl_item.is_completed = True
            from datetime import datetime
            cl_item.completed_at = datetime.utcnow()
        elif cl_item.is_completed:
            cl_item.is_completed = False
            cl_item.completed_at = None
