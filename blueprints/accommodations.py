from flask import Blueprint, render_template, jsonify, request
from models import db, AccommodationLocation, AccommodationOption

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


@accommodations_bp.route('/api/accommodations/<int:option_id>/status',
                          methods=['PUT'])
def update_status(option_id):
    option = AccommodationOption.query.get_or_404(option_id)
    data = request.get_json()
    option.booking_status = data.get('booking_status', option.booking_status)
    option.confirmation_number = data.get('confirmation_number',
                                          option.confirmation_number)
    option.user_notes = data.get('user_notes', option.user_notes)
    db.session.commit()

    from app import socketio
    socketio.emit('accommodation_updated', {
        'location_id': option.location_id,
        'option_id': option.id,
        'booking_status': option.booking_status,
    })

    return jsonify({'ok': True})
