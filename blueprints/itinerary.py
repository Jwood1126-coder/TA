from flask import Blueprint, render_template, jsonify, request, redirect, url_for
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
                'accom_doc_backed': False,
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
        # Find ALL matching accommodation locations (e.g. Takayama has Ryokan + Budget)
        matching_accoms = [a for a in all_accom
                          if group['location'] in a.location_name]
        if matching_accoms:
            # Aggregate options across all matching locations
            all_opts = []
            for accom_loc in matching_accoms:
                all_opts.extend(options_by_loc.get(accom_loc.id, []))
            selected = next((o for o in all_opts if o.is_selected), None)
            if selected:
                group['accom_name'] = selected.name
                group['accom_status'] = selected.booking_status
                group['accom_doc_backed'] = bool(selected.document_id)
            else:
                group['accom_pending_count'] = sum(
                    1 for o in all_opts if not o.is_eliminated)
            # Use earliest check-in and latest check-out across all locations
            check_ins = [a.check_in_date for a in matching_accoms
                         if a.check_in_date]
            check_outs = [a.check_out_date for a in matching_accoms
                          if a.check_out_date]
            if check_ins:
                group['start_date'] = min(check_ins)
            if check_outs:
                group['end_date'] = max(check_outs)
                group['show_checkout'] = True

    # Build brief activity summaries + day type icons per day
    for group in location_groups:
        for day in group['days']:
            titles = [a.title for a in day.activities if not a.is_substitute][:3]
            summary = ', '.join(titles)
            if len(summary) > 80:
                summary = summary[:77] + '...'
            day.activity_summary = summary

            # Confirmed activity count for mini progress
            non_sub = [a for a in day.activities if not a.is_substitute]
            day.confirmed_count = sum(1 for a in non_sub if a.is_confirmed)
            day.total_browseable = len(non_sub)

            # Day type icon based on title keywords
            t = (day.title or '').lower()
            if 'travel' in t or 'departure' in t or 'arrive' in t:
                day.type_icon = 'travel'
            elif 'buffer' in t or 'flex' in t:
                day.type_icon = 'rest'
            elif 'day trip' in t or 'hiroshima' in t:
                day.type_icon = 'daytrip'
            elif 'hakone' in t or 'alps' in t or 'shirakawa' in t:
                day.type_icon = 'nature'
            elif 'temple' in t or 'gion' in t or 'arashiyama' in t:
                day.type_icon = 'temple'
            elif 'osaka' in t or 'neon' in t or 'street food' in t:
                day.type_icon = 'food'
            else:
                day.type_icon = 'explore'

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
                                     'pre_departure_miles', 'pre_departure_month']),
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
        # During active trip — find the next incomplete activity for TODAY only
        current = Day.query.filter(Day.date == today).first()
        if current:
            # Time-aware slot filtering
            from datetime import datetime
            hour = datetime.now().hour
            if hour < 12:
                min_slot_idx = 0
            elif hour < 17:
                min_slot_idx = 1
            elif hour < 20:
                min_slot_idx = 2
            else:
                min_slot_idx = 3
            slot_order = {'morning': 0, 'afternoon': 1, 'evening': 2, 'night': 3}

            for a in sorted(current.activities,
                            key=lambda x: (slot_order.get(x.time_slot or 'afternoon', 1),
                                           x.sort_order or 999)):
                if a.is_substitute or a.is_completed or a.is_eliminated:
                    continue
                a_slot_idx = slot_order.get(a.time_slot or 'afternoon', 1)
                if a_slot_idx >= min_slot_idx:
                    return {
                        'type': 'activity',
                        'title': a.title,
                        'subtitle': f'Day {current.day_number} \u2022 {a.time_slot or ""}',
                        'tip': a.start_time or None,
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

    # During active trip: redirect to today's day view (unless ?all=1)
    if trip_started and current_day and not request.args.get('all'):
        return redirect(url_for('itinerary.day_view',
                                day_number=current_day.day_number))

    # Stats
    total_activities = Activity.query.filter_by(is_substitute=False).count()
    completed_activities = Activity.query.filter_by(
        is_substitute=False, is_completed=True).count()
    overall_pct = int(completed_activities / total_activities * 100) \
        if total_activities else 0

    # Booking stats for dashboard breakdown — only count locations that need a booking
    # (i.e., have at least one non-eliminated option or a selected option)
    total_accoms = 0
    booked_accoms = 0
    for loc in AccommodationLocation.query.all():
        opts = AccommodationOption.query.filter_by(location_id=loc.id).all()
        has_active = any(not o.is_eliminated for o in opts)
        has_selected = any(o.is_selected for o in opts)
        if not has_active and not has_selected:
            continue  # Skip locations like Kanazawa (all eliminated, day-trip only)
        total_accoms += 1
        sel = next((o for o in opts if o.is_selected and
                    o.booking_status in ('booked', 'confirmed')), None)
        if sel:
            booked_accoms += 1
    confirmed_activities = Activity.query.filter_by(
        is_substitute=False, is_confirmed=True).count()

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
                           total_days=len(days),
                           total_activities=total_activities,
                           booked_accoms=booked_accoms,
                           total_accoms=total_accoms,
                           confirmed_activities=confirmed_activities,
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

    # Transport routes: find routes linked to this day OR matching location change
    transport_routes = TransportRoute.query.filter_by(day_id=day.id).order_by(
        TransportRoute.sort_order).all()
    if not transport_routes and day.location:
        prev_day_obj = Day.query.filter_by(day_number=day_number - 1).first() if day_number > 1 else None
        if prev_day_obj and prev_day_obj.location and prev_day_obj.location.name != day.location.name:
            prev_loc = prev_day_obj.location.name
            cur_loc = day.location.name
            routes = TransportRoute.query.filter_by(route_from=prev_loc, route_to=cur_loc).all()
            if not routes:
                routes = TransportRoute.query.filter(
                    TransportRoute.route_from.contains(prev_loc.split()[0]),
                    TransportRoute.route_to.contains(cur_loc.split()[0])
                ).all()
            transport_routes = routes

    # Group routes into movements: routes sharing a route_group are alternatives
    # for the same transfer; ungrouped routes are standalone legs.
    transport_movements = []
    _seen_groups = {}
    for route in transport_routes:
        if route.route_group:
            if route.route_group not in _seen_groups:
                movement = {'group': route.route_group, 'routes': [route]}
                _seen_groups[route.route_group] = movement
                transport_movements.append(movement)
            else:
                _seen_groups[route.route_group]['routes'].append(route)
        else:
            transport_movements.append({'group': None, 'routes': [route]})

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

    # Tonight's accommodation: find the selected option whose stay covers this night
    tonight_option = None
    tonight_locs = AccommodationLocation.query.filter(
        AccommodationLocation.check_in_date <= day.date,
        AccommodationLocation.check_out_date > day.date
    ).all()
    for loc in tonight_locs:
        opt = AccommodationOption.query.filter_by(
            location_id=loc.id, is_selected=True).first()
        if opt:
            tonight_option = opt
            break

    # Build set of booked hotel name keywords for filtering
    booked_keywords = set()
    booked_options = AccommodationOption.query.filter(
        AccommodationOption.is_selected == True,
        AccommodationOption.booking_status.in_(['booked', 'confirmed'])
    ).all()
    for opt in booked_options:
        # Extract keywords from hotel name (e.g. "Dormy Inn Asakusa" -> {"dormy", "inn"})
        for word in opt.name.lower().split():
            if len(word) > 2:
                booked_keywords.add(word)

    # Hotel-specific patterns that indicate an amenity activity
    _HOTEL_PATTERNS = [
        'ramen at', 'onsen at', 'onsen bath at', 'breakfast at',
        'dinner at', 'check into', 'check out of', 'check in at',
        'kaiseki dinner at', 'bath at',
    ]
    # Specific hotel/property name keywords
    _HOTEL_NAMES = [
        'dormy', 'ryokan', 'hostel', 'toyoko', "k's house", 'kaname',
        'piece hostel', 'machiya', 'shinagawa',
    ]

    def _is_hotel_activity(activity):
        """Return True if activity references a specific hotel."""
        title = activity.title.lower()
        # Check if title contains hotel-specific patterns
        for pattern in _HOTEL_PATTERNS:
            if pattern in title:
                return True
        for name in _HOTEL_NAMES:
            if name in title:
                return True
        return False

    def _hotel_is_booked(activity):
        """Check if the hotel referenced in the activity title is booked."""
        title = activity.title.lower()
        # Check if any booked hotel keyword appears in the activity title
        for kw in booked_keywords:
            if kw in title:
                return True
        return False

    # Build transport route names + destinations for duplicate detection
    _transport_route_names = set()
    _transport_destinations = set()
    for r in transport_routes:
        _transport_route_names.add(r.transport_type.lower())
        if r.train_name:
            _transport_route_names.add(r.train_name.lower())
        # Collect destinations for "Train to X" / "Bus to X" matching
        if r.route_to:
            _transport_destinations.add(r.route_to.lower())
    _flight_numbers = set()
    for f in day_flights:
        _flight_numbers.add(f.flight_number.lower())
    _checkin_name_words = set()
    if checkin_option:
        for word in checkin_option.name.lower().split():
            if len(word) > 3 and word not in ('hotel', 'the'):
                _checkin_name_words.add(word)
    _checkout_name_words = set()
    if checkout_option:
        for word in checkout_option.name.lower().split():
            if len(word) > 3 and word not in ('hotel', 'the'):
                _checkout_name_words.add(word)

    # Transport keywords for title matching (only distinctive words)
    _TRANSPORT_STOP_WORDS = {'the', 'and', 'for', 'from', 'into', 'with',
                             'line', 'train', 'bus', 'express', 'limited',
                             'station', 'airport'}
    _transport_distinct = set()
    for r in transport_routes:
        for word in r.transport_type.lower().split():
            if len(word) > 3 and word not in _TRANSPORT_STOP_WORDS:
                _transport_distinct.add(word)

    # Transit-verb prefixes that signal a transport duplicate
    _TRANSIT_PREFIXES = ('train to ', 'bus to ', 'ferry to ', 'taxi to ',
                         'subway to ', 'shinkansen to ', 'ride to ', 'transfer to ')

    def _is_logistics_duplicate(activity):
        """Return True if activity duplicates a transport/flight/accom card."""
        title = activity.title.lower()
        # Check-in/out duplicates (must start with check-in/out phrase)
        if title.startswith('check into') or title.startswith('check in at') or title.startswith('check in to'):
            for kw in _checkin_name_words:
                if kw in title:
                    return True
        if title.startswith('check out of') or title.startswith('check out from'):
            for kw in _checkout_name_words:
                if kw in title:
                    return True
        # Flight duplicates (exact flight number in title)
        for fn in _flight_numbers:
            if fn in title:
                return True
        # Transport duplicates — match full route names (any length)
        for name in _transport_route_names:
            if name in title:
                return True
        # "Train to X" / "Bus to X" where X is a transport route destination
        for prefix in _TRANSIT_PREFIXES:
            if title.startswith(prefix):
                for dest in _transport_destinations:
                    if dest in title:
                        return True
        # Match distinctive transport keywords (keikyu, shinkansen, etc.) — short titles only
        if len(title) < 40:
            for kw in _transport_distinct:
                if kw in title:
                    return True
        return False

    # Filter activities: hide hotel-specific ones unless hotel is booked,
    # and hide logistics activities that duplicate transport/flight/accom cards
    hidden_ids = set()
    for a in day.activities:
        if _is_hotel_activity(a) and not _hotel_is_booked(a):
            hidden_ids.add(a.id)
        elif _is_logistics_duplicate(a):
            hidden_ids.add(a.id)

    # Today's day number for "Go to Today" button
    today_day = Day.query.filter(Day.date == date.today()).first()
    today_day_number = today_day.day_number if today_day else None

    return render_template('day.html', day=day, prev_day=prev_day,
                           next_day=next_day, total_days=total_days,
                           transport_routes=transport_routes,
                           transport_movements=transport_movements,
                           day_flights=day_flights,
                           day_checkin=day_checkin, checkin_option=checkin_option,
                           day_checkout=day_checkout, checkout_option=checkout_option,
                           checkin_options_pending=checkin_options_pending,
                           checkout_options_pending=checkout_options_pending,
                           hidden_activity_ids=hidden_ids,
                           tonight_option=tonight_option,
                           today_day_number=today_day_number)


@itinerary_bp.route('/api/activities/<int:activity_id>/toggle', methods=['POST'])
def toggle_activity(activity_id):
    import services.activities as activity_svc
    activity = activity_svc.toggle(activity_id)
    return jsonify({'ok': True, 'is_completed': activity.is_completed})


@itinerary_bp.route('/api/activities/<int:activity_id>/notes', methods=['PUT'])
def update_activity_notes(activity_id):
    import services.activities as activity_svc
    data = request.get_json()
    activity_svc.update_notes(activity_id, data.get('notes', ''))
    return jsonify({'ok': True})


@itinerary_bp.route('/api/activities/<int:activity_id>/maps-url', methods=['PUT'])
def update_activity_maps_url(activity_id):
    import services.activities as activity_svc
    data = request.get_json()
    activity_svc.update_maps_url(activity_id, data.get('maps_url', ''))
    return jsonify({'ok': True})


@itinerary_bp.route('/api/days/<int:day_id>/notes', methods=['PUT'])
def update_day_notes(day_id):
    import services.activities as activity_svc
    data = request.get_json()
    activity_svc.update_day_notes(day_id, data.get('notes', ''))
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


# --- Transport Route CRUD ---

@itinerary_bp.route('/api/transport', methods=['POST'])
def add_transport():
    import services.transport as transport_svc
    data = request.get_json()
    try:
        route = transport_svc.add(data)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, 'id': route.id})


@itinerary_bp.route('/api/transport/<int:route_id>', methods=['PUT'])
def update_transport(route_id):
    import services.transport as transport_svc
    data = request.get_json()
    try:
        transport_svc.update(route_id, data)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True})


@itinerary_bp.route('/api/transport/<int:route_id>', methods=['DELETE'])
def delete_transport(route_id):
    import services.transport as transport_svc
    transport_svc.delete(route_id)
    return jsonify({'ok': True})


