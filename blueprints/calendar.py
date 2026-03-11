from flask import Blueprint, render_template
from models import (Day, Trip, Flight,
                    AccommodationLocation, AccommodationOption, TransportRoute)
from datetime import date, timedelta

calendar_bp = Blueprint('calendar', __name__)

# City accent colors for the month-view accommodation bars
CITY_COLORS = {
    'Tokyo': {'bg': '#F472A8', 'glow': 'rgba(244,114,168,0.3)'},
    'Hakone': {'bg': '#3cb371', 'glow': 'rgba(60,179,113,0.3)'},
    'Takayama': {'bg': '#f59e0b', 'glow': 'rgba(245,158,11,0.3)'},
    'Kyoto': {'bg': '#a78bfa', 'glow': 'rgba(167,139,250,0.3)'},
    'Osaka': {'bg': '#22d3ee', 'glow': 'rgba(34,211,238,0.3)'},
}

# Type icon mapping (mirrors itinerary.py logic)
TYPE_ICONS = {
    'travel': '\u2708',    # ✈
    'rest': '\U0001F3AF',  # 🎯
    'daytrip': '\U0001F684',  # 🚄
    'nature': '\u26F0',    # ⛰
    'temple': '\u26E9',    # ⛩
    'food': '\U0001F35C',  # 🍜
    'explore': '\U0001F4CC',  # 📌
}


def _get_type_icon(title):
    """Determine day type icon from title keywords."""
    t = (title or '').lower()
    if 'travel' in t or 'departure' in t or 'arrive' in t or '\u2192' in t or '->' in t:
        return 'travel'
    elif 'buffer' in t or 'flex' in t:
        return 'rest'
    elif 'day trip' in t or 'hiroshima' in t:
        return 'daytrip'
    elif 'hakone' in t or 'shirakawa' in t:
        return 'nature'
    elif 'alps' in t and 'takayama' not in t:
        return 'nature'
    elif 'temple' in t or 'gion' in t or 'arashiyama' in t:
        return 'temple'
    elif 'osaka' in t or 'neon' in t or 'street food' in t:
        return 'food'
    return 'explore'


def _build_canonical_chain():
    """Build the canonical accommodation chain: one stay per night, no overlaps.

    Returns list of (AccommodationLocation, AccommodationOption|None) tuples.
    Sorted by check-in date. If two locations claim the same night, the earlier
    check-in wins and the overlapping location is excluded entirely.
    """
    locs = AccommodationLocation.query.order_by(
        AccommodationLocation.check_in_date).all()
    covered_nights = set()
    chain = []
    for loc in locs:
        if not loc.check_in_date or not loc.check_out_date:
            continue
        # Skip all-eliminated locations
        if all(o.is_eliminated for o in loc.options):
            continue
        selected = next((o for o in loc.options if o.is_selected), None)
        if not selected:
            continue
        # Check if any of this location's nights are already covered
        nights = set()
        d = loc.check_in_date
        while d < loc.check_out_date:
            nights.add(d)
            d += timedelta(days=1)
        if nights & covered_nights:
            # Overlapping — skip this location (audit will flag it)
            continue
        covered_nights |= nights
        chain.append((loc, selected))
    return chain


@calendar_bp.route('/calendar')
def calendar_view():
    trip = Trip.query.first()
    days = Day.query.order_by(Day.day_number).all()

    # Build canonical accommodation chain: one stay per night, no overlaps.
    # Locations are sorted by check-in date; first-come wins for any night.
    canonical_locs = _build_canonical_chain()

    # Build date → accommodation mapping from canonical chain only
    accom_by_date = {}
    for loc, opt in canonical_locs:
        name = opt.name if opt else None
        status = opt.booking_status if opt else 'not_booked'
        d = loc.check_in_date
        while d < loc.check_out_date:
            is_checkin = d == loc.check_in_date
            if d in accom_by_date:
                if is_checkin:
                    accom_by_date[d]['check_in'] = True
                    accom_by_date[d]['name'] = name
                    accom_by_date[d]['status'] = status
            else:
                accom_by_date[d] = {
                    'name': name,
                    'status': status,
                    'check_in': is_checkin,
                    'check_out': False,
                }
            d += timedelta(days=1)
        # Mark checkout date
        if loc.check_out_date in accom_by_date:
            accom_by_date[loc.check_out_date]['check_out'] = True
        else:
            accom_by_date[loc.check_out_date] = {
                'name': name,
                'status': status,
                'check_in': False,
                'check_out': True,
            }

    # Pre-load flights by date
    flights = Flight.query.order_by(Flight.leg_number).all()
    flights_by_date = {}
    for f in flights:
        if f.depart_date:
            flights_by_date.setdefault(f.depart_date, []).append(f)
        if f.arrive_date and f.arrive_date != f.depart_date:
            flights_by_date.setdefault(f.arrive_date, []).append(f)

    # Pre-load transport routes by day_id
    routes = TransportRoute.query.all()
    routes_by_day = {}
    for r in routes:
        if r.day_id:
            routes_by_day.setdefault(r.day_id, []).append(r)

    # Build calendar data for each day (used by list view)
    calendar_days = []
    for day in days:
        activities = [a for a in day.activities
                      if not a.is_substitute and not a.is_eliminated]
        main_activities = activities[:4]
        remaining = max(0, len(activities) - 4)
        total = len(activities)
        done = sum(1 for a in activities if a.is_completed)
        transport = routes_by_day.get(day.id, [])
        accom = accom_by_date.get(day.date)
        day_flights = flights_by_date.get(day.date, [])

        calendar_days.append({
            'day': day,
            'activities': main_activities,
            'remaining_count': remaining,
            'total': total,
            'done': done,
            'pct': int(done / total * 100) if total else 0,
            'accom': accom,
            'flights': day_flights,
            'transport': transport,
            'location_name': day.location.name if day.location else 'Travel',
        })

    # --- Month view data ---
    month_data = []
    for day in days:
        non_sub = [a for a in day.activities
                   if not a.is_substitute and not a.is_eliminated]
        type_icon = _get_type_icon(day.title)
        month_data.append({
            'day_number': day.day_number,
            'date': day.date.isoformat(),
            'date_day': day.date.day,
            'weekday': day.date.strftime('%a'),
            'title': day.title,
            'activity_count': len(non_sub),
            'confirmed_count': sum(1 for a in non_sub if a.is_confirmed),
            'completed_count': sum(1 for a in non_sub if a.is_completed),
            'type_icon': type_icon,
            'type_emoji': TYPE_ICONS.get(type_icon, '\U0001F4CC'),
            'location_name': day.location.name if day.location else 'Travel',
            'is_buffer': day.is_buffer_day,
        })

    # --- Accommodation spans for month grid (canonical chain only) ---
    accom_spans = []
    for loc, selected in canonical_locs:
        if not selected:
            continue
        city = loc.location_name.split()[0] if loc.location_name else ''
        colors = CITY_COLORS.get(city, {'bg': '#888', 'glow': 'rgba(136,136,136,0.3)'})
        accom_spans.append({
            'name': selected.name,
            'location_name': loc.location_name,
            'city': city,
            'check_in': loc.check_in_date.isoformat(),
            'check_out': loc.check_out_date.isoformat(),
            'check_in_day': loc.check_in_date.day,
            'check_out_day': loc.check_out_date.day,
            'num_nights': (loc.check_out_date - loc.check_in_date).days,
            'status': selected.booking_status,
            'location_id': loc.id,
            'color_bg': colors['bg'],
            'color_glow': colors['glow'],
        })

    # --- Week view data ---
    week_data = {}
    for day in days:
        activities = []
        for a in day.activities:
            if not a.is_substitute and not a.is_eliminated:
                activities.append({
                    'title': a.title,
                    'time_slot': a.time_slot or 'morning',
                    'start_time': a.start_time,
                    'category': a.category or '',
                    'is_optional': a.is_optional,
                    'book_ahead': a.book_ahead,
                    'is_confirmed': a.is_confirmed,
                    'is_completed': a.is_completed,
                })

        transport = routes_by_day.get(day.id, [])
        transit_list = [{
            'route_from': t.route_from,
            'route_to': t.route_to,
            'type': t.transport_type,
            'duration': t.duration,
            'jr_covered': t.jr_pass_covered,
        } for t in transport]

        day_flights_list = []
        for f in flights_by_date.get(day.date, []):
            is_arrival = (f.arrive_date and f.arrive_date == day.date
                          and f.depart_date != day.date)
            day_flights_list.append({
                'flight_number': f.flight_number,
                'route_from': f.route_from,
                'route_to': f.route_to,
                'depart_time': f.depart_time,
                'arrive_time': f.arrive_time,
                'is_arrival': is_arrival,
            })

        accom = accom_by_date.get(day.date)
        type_icon = _get_type_icon(day.title)

        week_data[day.day_number] = {
            'activities': activities,
            'transits': transit_list,
            'flights': day_flights_list,
            'accom_name': accom['name'] if accom else None,
            'accom_check_in': accom['check_in'] if accom else False,
            'accom_check_out': accom.get('check_out', False) if accom else False,
            'location_name': day.location.name if day.location else 'Travel',
            'type_emoji': TYPE_ICONS.get(type_icon, '\U0001F4CC'),
            'title': day.title,
            'date': day.date.isoformat(),
            'is_buffer': day.is_buffer_day,
        }

    today = date.today()
    today_day_num = None
    trip_started = False
    if trip and trip.start_date <= today <= trip.end_date:
        trip_started = True
        today_day = Day.query.filter(Day.date == today).first()
        if today_day:
            today_day_num = today_day.day_number

    # Surface accommodation blockers so the canonical-chain filter
    # doesn't silently mask overlap problems
    from services.trip_audit import audit_trip
    audit = audit_trip()
    accom_blockers = [b for b in audit.blockers
                      if any(kw in b.lower() for kw in
                             ('accommodation', 'overlap', 'stay', 'multiple stays',
                              'selected options', 'night'))]

    return render_template('calendar.html',
                           trip=trip,
                           calendar_days=calendar_days,
                           today_day_num=today_day_num,
                           trip_started=trip_started,
                           month_data=month_data,
                           accom_spans=accom_spans,
                           week_data=week_data,
                           accom_blockers=accom_blockers)
