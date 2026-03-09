from flask import Blueprint, render_template
from models import (db, Day, Trip, Location, Activity, Flight,
                    AccommodationLocation, AccommodationOption, TransportRoute)
from datetime import timedelta

calendar_bp = Blueprint('calendar', __name__)


@calendar_bp.route('/calendar')
def calendar_view():
    trip = Trip.query.first()
    days = Day.query.order_by(Day.day_number).all()

    # Pre-load accommodations with selected options
    accom_locs = AccommodationLocation.query.all()
    accom_options = AccommodationOption.query.filter_by(is_selected=True).all()
    options_by_loc = {o.location_id: o for o in accom_options}

    # Build date → accommodation mapping
    accom_by_date = {}
    for loc in accom_locs:
        opt = options_by_loc.get(loc.id)
        d = loc.check_in_date
        while d < loc.check_out_date:
            accom_by_date[d] = {
                'name': opt.name if opt else None,
                'status': opt.booking_status if opt else 'not_booked',
                'check_in': d == loc.check_in_date,
                'check_out': False,
            }
            d += timedelta(days=1)
        # Mark checkout date
        if loc.check_out_date not in accom_by_date:
            accom_by_date[loc.check_out_date] = {
                'name': opt.name if opt else None,
                'status': opt.booking_status if opt else 'not_booked',
                'check_in': False,
                'check_out': True,
            }
        else:
            accom_by_date[loc.check_out_date]['check_out'] = True

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

    # Build calendar data for each day
    calendar_days = []
    for day in days:
        # Key activities (non-substitute, non-eliminated, limit to top ones)
        activities = [a for a in day.activities
                      if not a.is_substitute and not a.is_eliminated]
        main_activities = activities[:4]
        remaining = max(0, len(activities) - 4)

        # Completion stats
        total = len(activities)
        done = sum(1 for a in activities if a.is_completed)

        # Location change?
        transport = routes_by_day.get(day.id, [])

        # Accommodation for this night
        accom = accom_by_date.get(day.date)

        # Flights
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

    from datetime import date as dt_date
    today = dt_date.today()
    today_day_num = None
    if trip and trip.start_date <= today <= trip.end_date:
        today_day = Day.query.filter(Day.date == today).first()
        if today_day:
            today_day_num = today_day.day_number

    return render_template('calendar.html',
                           trip=trip,
                           calendar_days=calendar_days,
                           today_day_num=today_day_num)
