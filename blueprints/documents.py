import re
from flask import Blueprint, render_template, jsonify, request
from models import db, Flight, AccommodationLocation, AccommodationOption, TransportRoute, Location, Day

documents_bp = Blueprint('documents', __name__)

VALID_BOOKING_STATUSES = {'not_booked', 'researching', 'booked', 'confirmed', 'cancelled'}


@documents_bp.route('/documents')
def documents_view():
    flights = Flight.query.order_by(Flight.direction, Flight.leg_number).all()

    accom_locations = AccommodationLocation.query.order_by(
        AccommodationLocation.check_in_date).all()
    accommodations = []
    for loc in accom_locations:
        selected = next((o for o in loc.options if o.is_selected), None)
        if selected:
            accommodations.append({'location': loc, 'selected': selected})

    transport = TransportRoute.query.order_by(TransportRoute.sort_order).all()

    # Activities with tickets/bookings (have URL or cost)
    locations = Location.query.order_by(Location.sort_order).all()
    days = Day.query.order_by(Day.day_number).all()
    ticketed_activities = []
    for loc in locations:
        loc_days = [d for d in days if d.location_id == loc.id]
        for d in loc_days:
            for a in d.activities:
                if a.is_substitute:
                    continue
                if a.url or a.cost_per_person:
                    ticketed_activities.append({
                        'activity': a, 'day': d, 'location': loc
                    })

    return render_template('documents.html',
                           flights=flights,
                           accommodations=accommodations,
                           transport=transport,
                           ticketed_activities=ticketed_activities)


@documents_bp.route('/api/documents/flight/<int:flight_id>/confirmation',
                     methods=['PUT'])
def update_flight_confirmation(flight_id):
    flight = Flight.query.get_or_404(flight_id)
    data = request.get_json()
    new_status = data.get('booking_status')
    if new_status is not None:
        if new_status not in VALID_BOOKING_STATUSES:
            return jsonify({'ok': False, 'error': 'Invalid status'}), 400
        flight.booking_status = new_status
    flight.confirmation_number = data.get('confirmation_number',
                                          flight.confirmation_number)
    db.session.commit()

    from app import socketio
    socketio.emit('document_updated', {'type': 'flight', 'id': flight.id})

    return jsonify({'ok': True})
