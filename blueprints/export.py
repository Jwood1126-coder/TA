from flask import Blueprint, render_template, jsonify, request
from models import (Trip, Day, Activity, Flight, AccommodationLocation,
                    TransportRoute, Location)
from services.trip_audit import audit_trip

export_bp = Blueprint('export', __name__)


@export_bp.route('/api/trip/audit')
def trip_audit_api():
    """Return trip audit results as JSON."""
    result = audit_trip()
    return jsonify(result.to_dict())


@export_bp.route('/export')
def export_view():
    # Run audit before rendering
    audit = audit_trip()

    # If blockers exist and force not requested, show audit page instead
    force = request.args.get('force') == '1'
    if not audit.exportable and not force:
        return render_template('export_blocked.html', audit=audit)

    trip = Trip.query.first()
    days = Day.query.order_by(Day.day_number).all()
    flights = Flight.query.order_by(Flight.direction, Flight.leg_number).all()
    locations = Location.query.order_by(Location.sort_order).all()
    transport = TransportRoute.query.order_by(TransportRoute.sort_order).all()

    accom_locations = AccommodationLocation.query.order_by(
        AccommodationLocation.check_in_date).all()
    accommodations = []
    for loc in accom_locations:
        selected = next((o for o in loc.options if o.is_selected), None)
        active_options = [o for o in loc.options if not o.is_eliminated]
        # Skip locations where all options are eliminated (e.g. Kanazawa day-trip)
        if not selected and not active_options:
            continue
        accommodations.append({'location': loc, 'selected': selected,
                               'options': active_options})

    # Build day data with activities (exclude substitutes and eliminated)
    day_data = []
    for day in days:
        activities = Activity.query.filter_by(
            day_id=day.id, is_substitute=False, is_eliminated=False
        ).order_by(Activity.sort_order).all()
        day_data.append({'day': day, 'activities': activities})

    return render_template('export.html',
                           trip=trip,
                           day_data=day_data,
                           flights=flights,
                           accommodations=accommodations,
                           transport=transport,
                           locations=locations,
                           audit=audit)
