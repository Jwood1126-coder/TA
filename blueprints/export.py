from flask import Blueprint, render_template
from models import (Trip, Day, Activity, Flight, AccommodationLocation,
                    AccommodationOption, TransportRoute, Location)

export_bp = Blueprint('export', __name__)


@export_bp.route('/export')
def export_view():
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
        accommodations.append({'location': loc, 'selected': selected,
                               'options': loc.options})

    # Build day data with activities
    day_data = []
    for day in days:
        activities = Activity.query.filter_by(
            day_id=day.id, is_substitute=False
        ).order_by(Activity.sort_order).all()
        day_data.append({'day': day, 'activities': activities})

    return render_template('export.html',
                           trip=trip,
                           day_data=day_data,
                           flights=flights,
                           accommodations=accommodations,
                           transport=transport,
                           locations=locations)
