from flask import Blueprint, render_template, jsonify, request
from models import db, Day, Activity, Trip, Location, BudgetItem, Flight, \
    TransportRoute, AccommodationLocation, AccommodationOption
from datetime import datetime, date

itinerary_bp = Blueprint('itinerary', __name__)


@itinerary_bp.route('/')
def index():
    trip = Trip.query.first()
    days = Day.query.order_by(Day.day_number).all()
    flights = Flight.query.order_by(Flight.direction, Flight.leg_number).all()

    # Figure out "today" relative to trip
    today = date.today()
    current_day = None
    trip_started = False
    trip_ended = False
    days_until = None

    if trip:
        if today < trip.start_date:
            days_until = (trip.start_date - today).days
        elif today > trip.end_date:
            trip_ended = True
        else:
            trip_started = True
            current_day = Day.query.filter(Day.date == today).first()

    # Stats
    total_activities = Activity.query.filter_by(is_substitute=False).count()
    completed_activities = Activity.query.filter_by(
        is_substitute=False, is_completed=True).count()

    total_locations = AccommodationLocation.query.count()
    booked_accommodations = AccommodationOption.query.filter_by(
        is_selected=True).count()

    return render_template('index.html',
                           trip=trip,
                           days=days,
                           flights=flights,
                           current_day=current_day,
                           trip_started=trip_started,
                           trip_ended=trip_ended,
                           days_until=days_until,
                           total_activities=total_activities,
                           completed_activities=completed_activities,
                           booked_accommodations=booked_accommodations,
                           total_locations=total_locations)


@itinerary_bp.route('/itinerary')
def itinerary_overview():
    trip = Trip.query.first()
    days = Day.query.order_by(Day.day_number).all()

    # Group consecutive days by location
    location_groups = []
    current_group = None
    for day in days:
        loc_name = day.location.name if day.location else 'Travel'
        if not current_group or current_group['location'] != loc_name:
            current_group = {
                'location': loc_name,
                'location_obj': day.location,
                'days': [],
                'start_date': day.date,
                'end_date': day.date,
                'accom_name': None,
                'accom_status': None,
                'accom_pending_count': 0,
            }
            location_groups.append(current_group)
        current_group['days'].append(day)
        current_group['end_date'] = day.date

    # Attach accommodation status per group
    for group in location_groups:
        accom_loc = AccommodationLocation.query.filter(
            AccommodationLocation.location_name.contains(group['location'])
        ).first()
        if accom_loc:
            selected = AccommodationOption.query.filter_by(
                location_id=accom_loc.id, is_selected=True).first()
            if selected:
                group['accom_name'] = selected.name
                group['accom_status'] = selected.booking_status
            else:
                pending = AccommodationOption.query.filter_by(
                    location_id=accom_loc.id, is_eliminated=False).count()
                group['accom_pending_count'] = pending

    # Build brief activity summaries per day
    for group in location_groups:
        for day in group['days']:
            titles = [a.title for a in day.activities if not a.is_substitute][:3]
            summary = ', '.join(titles)
            if len(summary) > 80:
                summary = summary[:77] + '...'
            day.activity_summary = summary

    # Overall trip progress
    total = Activity.query.filter_by(is_substitute=False).count()
    done = Activity.query.filter_by(is_substitute=False, is_completed=True).count()
    overall_pct = int(done / total * 100) if total else 0

    return render_template('itinerary.html',
                           trip=trip,
                           location_groups=location_groups,
                           overall_pct=overall_pct,
                           completed=done,
                           total=total)


@itinerary_bp.route('/day/<int:day_number>')
def day_view(day_number):
    day = Day.query.filter_by(day_number=day_number).first_or_404()
    total_days = Day.query.count()
    prev_day = day_number - 1 if day_number > 1 else None
    next_day = day_number + 1 if day_number < total_days else None

    # Transport routes: find routes for travel days (location changed from previous day)
    transport_routes = []
    if day.location:
        prev_day_obj = Day.query.filter_by(day_number=day_number - 1).first() if day_number > 1 else None
        if prev_day_obj and prev_day_obj.location and prev_day_obj.location.name != day.location.name:
            prev_loc = prev_day_obj.location.name
            cur_loc = day.location.name
            routes = TransportRoute.query.filter_by(route_from=prev_loc, route_to=cur_loc).all()
            if not routes:
                # Try partial match (first word of location name)
                routes = TransportRoute.query.filter(
                    TransportRoute.route_from.contains(prev_loc.split()[0]),
                    TransportRoute.route_to.contains(cur_loc.split()[0])
                ).all()
            transport_routes = routes

    # Flights on this day
    day_flights = Flight.query.filter(
        (Flight.depart_date == day.date) | (Flight.arrive_date == day.date)
    ).order_by(Flight.leg_number).all()

    # Accommodation check-in/out on this day
    day_checkin = AccommodationLocation.query.filter_by(check_in_date=day.date).first()
    day_checkout = AccommodationLocation.query.filter_by(check_out_date=day.date).first()
    checkin_option = None
    checkout_option = None
    checkin_options_pending = []
    checkout_options_pending = []

    if day_checkin:
        checkin_option = AccommodationOption.query.filter_by(
            location_id=day_checkin.id, is_selected=True).first()
        if not checkin_option:
            checkin_options_pending = AccommodationOption.query.filter_by(
                location_id=day_checkin.id, is_eliminated=False
            ).order_by(AccommodationOption.rank).all()

    if day_checkout:
        checkout_option = AccommodationOption.query.filter_by(
            location_id=day_checkout.id, is_selected=True).first()
        if not checkout_option:
            checkout_options_pending = AccommodationOption.query.filter_by(
                location_id=day_checkout.id, is_eliminated=False
            ).order_by(AccommodationOption.rank).all()

    return render_template('day.html', day=day, prev_day=prev_day,
                           next_day=next_day, total_days=total_days,
                           transport_routes=transport_routes,
                           day_flights=day_flights,
                           day_checkin=day_checkin, checkin_option=checkin_option,
                           day_checkout=day_checkout, checkout_option=checkout_option,
                           checkin_options_pending=checkin_options_pending,
                           checkout_options_pending=checkout_options_pending)


@itinerary_bp.route('/api/activities/<int:activity_id>/toggle', methods=['POST'])
def toggle_activity(activity_id):
    activity = Activity.query.get_or_404(activity_id)
    activity.is_completed = not activity.is_completed
    activity.completed_at = datetime.utcnow() if activity.is_completed else None
    db.session.commit()

    # Broadcast via socketio
    from app import socketio
    socketio.emit('activity_toggled', {
        'id': activity.id,
        'is_completed': activity.is_completed,
        'day_id': activity.day_id,
    })

    return jsonify({'ok': True, 'is_completed': activity.is_completed})


@itinerary_bp.route('/api/activities/<int:activity_id>/notes', methods=['PUT'])
def update_activity_notes(activity_id):
    activity = Activity.query.get_or_404(activity_id)
    data = request.get_json()
    activity.notes = data.get('notes', '')
    db.session.commit()

    from app import socketio
    socketio.emit('notes_updated', {
        'type': 'activity',
        'id': activity.id,
        'notes': activity.notes,
    })

    return jsonify({'ok': True})


@itinerary_bp.route('/api/days/<int:day_id>/notes', methods=['PUT'])
def update_day_notes(day_id):
    day = Day.query.get_or_404(day_id)
    data = request.get_json()
    day.notes = data.get('notes', '')
    db.session.commit()

    from app import socketio
    socketio.emit('notes_updated', {
        'type': 'day',
        'id': day.id,
        'notes': day.notes,
    })

    return jsonify({'ok': True})


@itinerary_bp.route('/api/budget')
def get_budget():
    items = BudgetItem.query.order_by(BudgetItem.sort_order).all()
    return jsonify([{
        'id': i.id,
        'category': i.category,
        'description': i.description,
        'estimated_low': i.estimated_low,
        'estimated_high': i.estimated_high,
        'actual_amount': i.actual_amount,
        'notes': i.notes,
    } for i in items])


CITY_COORDS = {
    'Minneapolis': (44.9778, -93.2650),
    'Tokyo': (35.6762, 139.6503),
    'Hakone': (35.2326, 139.1070),
    'Takayama': (36.1461, 137.2522),
    'Shirakawa-go': (36.2578, 136.9060),
    'Kanazawa': (36.5613, 136.6562),
    'Kyoto': (35.0116, 135.7681),
    'Osaka': (34.6937, 135.5023),
}


@itinerary_bp.route('/map')
def map_view():
    locations = Location.query.order_by(Location.sort_order).all()

    markers = []
    for loc in locations:
        coords = CITY_COORDS.get(loc.name)
        if not coords:
            continue

        accom_loc = AccommodationLocation.query.filter(
            AccommodationLocation.location_name.contains(loc.name)
        ).first()
        accom_name = None
        if accom_loc:
            selected = AccommodationOption.query.filter_by(
                location_id=accom_loc.id, is_selected=True).first()
            if selected:
                accom_name = selected.name

        markers.append({
            'name': loc.name,
            'lat': coords[0],
            'lng': coords[1],
            'vibe': loc.vibe,
            'arrival': loc.arrival_date.strftime('%b %d') if loc.arrival_date else None,
            'departure': loc.departure_date.strftime('%b %d') if loc.departure_date else None,
            'accom': accom_name,
            'guide_url': loc.guide_url,
        })

    return render_template('map.html', markers=markers)
