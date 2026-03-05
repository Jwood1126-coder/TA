from flask import Blueprint, render_template, jsonify, request
from models import db, Day, Activity, Trip, Location, BudgetItem, Flight, \
    TransportRoute, AccommodationLocation, AccommodationOption, ChecklistItem
from datetime import datetime, date

itinerary_bp = Blueprint('itinerary', __name__)


def _build_location_groups(days):
    """Group consecutive days by location with accommodation status."""
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

    # Batch-load all accommodations + options in 2 queries (avoids N+1)
    all_accom = AccommodationLocation.query.all()
    all_options = AccommodationOption.query.all()
    options_by_loc = {}
    for opt in all_options:
        options_by_loc.setdefault(opt.location_id, []).append(opt)

    for group in location_groups:
        accom_loc = next(
            (a for a in all_accom if group['location'] in a.location_name),
            None)
        if accom_loc:
            opts = options_by_loc.get(accom_loc.id, [])
            selected = next((o for o in opts if o.is_selected), None)
            if selected:
                group['accom_name'] = selected.name
                group['accom_status'] = selected.booking_status
            else:
                group['accom_pending_count'] = sum(
                    1 for o in opts if not o.is_eliminated)

    # Build brief activity summaries per day
    for group in location_groups:
        for day in group['days']:
            titles = [a.title for a in day.activities if not a.is_substitute][:3]
            summary = ', '.join(titles)
            if len(summary) > 80:
                summary = summary[:77] + '...'
            day.activity_summary = summary

    return location_groups


def _compute_next_up(today, trip):
    """Determine the single most urgent action item for the hero card."""
    # Priority 1: Unbooked accommodations (nearest check-in first)
    # Batch-load all accommodations + options in 2 queries
    accom_locs = AccommodationLocation.query.order_by(
        AccommodationLocation.check_in_date).all()
    all_options = AccommodationOption.query.all()
    options_by_loc = {}
    for opt in all_options:
        options_by_loc.setdefault(opt.location_id, []).append(opt)

    for loc in accom_locs:
        opts = options_by_loc.get(loc.id, [])
        selected = next((o for o in opts if o.is_selected), None)
        if not selected:
            pending = sum(1 for o in opts if not o.is_eliminated)
            return {
                'type': 'accommodation',
                'title': f'Choose hotel: {loc.location_name}',
                'subtitle': f'Check-in {loc.check_in_date.strftime("%b %d")} \u2022 {pending} option{"s" if pending != 1 else ""}',
                'tip': loc.quick_notes or None,
                'url': '/checklists?tab=pre_trip',
                'urgency': 'high' if (loc.check_in_date - today).days < 45 else 'medium',
            }
        elif selected.booking_status in ('not_booked', None):
            return {
                'type': 'accommodation',
                'title': f'Book: {selected.name}',
                'subtitle': f'{loc.location_name} \u2022 Check-in {loc.check_in_date.strftime("%b %d")}',
                'tip': loc.quick_notes or None,
                'url': '/accommodations',
                'urgency': 'high' if (loc.check_in_date - today).days < 45 else 'medium',
            }

    # Priority 2: Flights needing confirmation
    for flight in Flight.query.order_by(Flight.depart_date).all():
        if flight.booking_status in ('not_booked', None):
            return {
                'type': 'flight',
                'title': f'Confirm {flight.airline} {flight.flight_number}',
                'subtitle': f'{flight.route_from} \u2192 {flight.route_to} \u2022 {flight.depart_date.strftime("%b %d")}',
                'tip': flight.notes or None,
                'url': '/documents',
                'urgency': 'high',
            }

    # Priority 3: Pending booking checklist items
    booking_item = ChecklistItem.query.filter(
        ChecklistItem.is_completed == False,
        ChecklistItem.sort_order < 9999,
        ChecklistItem.category.in_(['pre_departure_today', 'pre_departure_week',
                                     'pre_departure_miles']),
    ).order_by(ChecklistItem.sort_order).first()
    if booking_item:
        return {
            'type': 'checklist',
            'title': booking_item.title,
            'subtitle': 'Pre-trip task',
            'tip': None,
            'url': '/checklists?tab=pre_trip',
            'urgency': 'medium',
        }

    # Priority 4: Fallback
    if trip and today < trip.start_date:
        return {
            'type': 'all_set',
            'title': 'All caught up!',
            'subtitle': 'Everything is booked and ready.',
            'tip': None,
            'url': None,
            'urgency': 'low',
        }
    elif trip and trip.start_date <= today <= trip.end_date:
        current = Day.query.filter(Day.date == today).first()
        if current:
            for a in current.activities:
                if not a.is_substitute and not a.is_completed:
                    return {
                        'type': 'activity',
                        'title': a.title,
                        'subtitle': f'Day {current.day_number} \u2022 {a.time_slot or ""}',
                        'tip': None,
                        'url': f'/day/{current.day_number}',
                        'urgency': 'low',
                    }

    return {
        'type': 'all_set',
        'title': 'All caught up!',
        'subtitle': 'Everything is booked and ready.',
        'tip': None,
        'url': None,
        'urgency': 'low',
    }


@itinerary_bp.route('/')
def index():
    trip = Trip.query.first()
    days = Day.query.order_by(Day.day_number).all()

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
    overall_pct = int(completed_activities / total_activities * 100) \
        if total_activities else 0

    # Location-grouped itinerary
    location_groups = _build_location_groups(days)

    # Next Up hero card
    next_up = _compute_next_up(today, trip)

    return render_template('index.html',
                           trip=trip,
                           days=days,
                           current_day=current_day,
                           trip_started=trip_started,
                           trip_ended=trip_ended,
                           days_until=days_until,
                           total_activities=total_activities,
                           completed_activities=completed_activities,
                           overall_pct=overall_pct,
                           location_groups=location_groups,
                           next_up=next_up)


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
    # Try exact date match first, then fallback to location name match
    day_checkin = AccommodationLocation.query.filter_by(check_in_date=day.date).first()
    day_checkout = AccommodationLocation.query.filter_by(check_out_date=day.date).first()

    # Fallback: match by location name if date didn't find anything
    if not day_checkin and day.location:
        loc_name = day.location.name
        candidate = AccommodationLocation.query.filter(
            AccommodationLocation.location_name.contains(loc_name)
        ).first()
        if candidate and candidate.check_in_date == day.date:
            day_checkin = candidate
    if not day_checkout and day.location:
        loc_name = day.location.name
        candidate = AccommodationLocation.query.filter(
            AccommodationLocation.location_name.contains(loc_name)
        ).first()
        if candidate and candidate.check_out_date == day.date:
            day_checkout = candidate

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


