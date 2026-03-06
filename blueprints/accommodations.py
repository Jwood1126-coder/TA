import os
import uuid
from flask import Blueprint, render_template, jsonify, request, current_app
from models import db, AccommodationLocation, AccommodationOption, ChecklistItem

accommodations_bp = Blueprint('accommodations', __name__)


@accommodations_bp.route('/accommodations')
def accommodations_view():
    locations = AccommodationLocation.query.order_by(
        AccommodationLocation.check_in_date).all()
    # Build booking status summary per location for tab badges
    loc_status = {}
    for loc in locations:
        booked = [o for o in loc.options
                  if o.booking_status in ('booked', 'confirmed')]
        loc_status[loc.id] = {
            'booked_count': len(booked),
            'booked_name': booked[0].name if len(booked) == 1 else None,
            'double_booked': len(booked) > 1,
        }
    return render_template('accommodations.html',
                           locations=locations, loc_status=loc_status)


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


@accommodations_bp.route('/api/accommodations/reorder-batch', methods=['PUT'])
def reorder_batch():
    data = request.get_json()
    location_id = data.get('location_id')
    order = data.get('order', [])  # list of option IDs as strings
    if not location_id or not order:
        return jsonify({'ok': False}), 400
    for rank, oid in enumerate(order, 1):
        opt = AccommodationOption.query.get(int(oid))
        if opt and opt.location_id == int(location_id):
            opt.rank = rank
    db.session.commit()

    from app import socketio
    socketio.emit('accommodation_updated', {'location_id': int(location_id)})
    return jsonify({'ok': True})


@accommodations_bp.route('/api/accommodations/<int:location_id>/add', methods=['POST'])
def add_option(location_id):
    loc = AccommodationLocation.query.get_or_404(location_id)
    data = request.get_json()
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'Name is required'}), 400

    max_rank = db.session.query(db.func.max(AccommodationOption.rank)).filter_by(
        location_id=location_id).scalar() or 0

    option = AccommodationOption(
        location_id=location_id,
        rank=max_rank + 1,
        name=name,
        property_type=data.get('property_type', ''),
        price_low=float(data['price_low']) if data.get('price_low') else None,
        price_high=float(data['price_high']) if data.get('price_high') else None,
        booking_url=data.get('booking_url') or None,
        maps_url=data.get('maps_url') or None,
    )
    db.session.add(option)
    db.session.commit()

    from app import socketio
    socketio.emit('accommodation_updated', {'location_id': location_id})
    return jsonify({'ok': True, 'id': option.id})


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
    if 'maps_url' in data:
        option.maps_url = data['maps_url'] or None

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


@accommodations_bp.route('/api/accommodations/<int:option_id>/upload-image',
                          methods=['POST'])
def upload_booking_image(option_id):
    option = AccommodationOption.query.get_or_404(option_id)
    file = request.files.get('image')
    if not file or not file.filename:
        return jsonify({'ok': False, 'error': 'No file'}), 400

    allowed = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'heic', 'heif'}
    ext = file.filename.rsplit('.', 1)[-1].lower()
    if ext not in allowed:
        return jsonify({'ok': False, 'error': 'Invalid file type'}), 400

    filename = f"booking_{option_id}_{uuid.uuid4().hex[:8]}.{ext}"
    upload_dir = current_app.config['UPLOAD_FOLDER']
    os.makedirs(os.path.join(upload_dir, 'originals'), exist_ok=True)
    file.save(os.path.join(upload_dir, 'originals', filename))

    # Remove old image if exists
    if option.booking_image:
        old_path = os.path.join(upload_dir, 'originals', option.booking_image)
        if os.path.exists(old_path):
            os.remove(old_path)

    option.booking_image = filename
    db.session.commit()
    return jsonify({'ok': True, 'filename': filename})


@accommodations_bp.route('/api/accommodations/<int:option_id>/delete-image',
                          methods=['DELETE'])
def delete_booking_image(option_id):
    option = AccommodationOption.query.get_or_404(option_id)
    if option.booking_image:
        upload_dir = current_app.config['UPLOAD_FOLDER']
        old_path = os.path.join(upload_dir, 'originals', option.booking_image)
        if os.path.exists(old_path):
            os.remove(old_path)
        option.booking_image = None
        db.session.commit()
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
