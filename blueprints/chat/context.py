"""Build dynamic context about the current trip state for the AI."""

from datetime import date, timedelta
from models import (db, Day, Trip, Activity, AccommodationOption,
                    AccommodationLocation, Flight, TransportRoute, BudgetItem)


def build_context():
    """Build dynamic context about the current trip state."""
    parts = []
    today = date.today()

    trip = Trip.query.first()
    if trip:
        days_until = (trip.start_date - today).days
        if days_until > 0:
            parts.append(f"TODAY is {today.strftime('%B %d, %Y')} — "
                         f"{days_until} days until trip starts ({trip.start_date.strftime('%B %d')})")
        elif today <= trip.end_date:
            parts.append(f"TRIP IS ACTIVE — today is {today.strftime('%B %d')}")
        else:
            parts.append(f"Trip ended on {trip.end_date.strftime('%B %d')}")

    current_day = Day.query.filter(Day.date == today).first()
    if current_day:
        parts.append(f"\nTODAY is Day {current_day.day_number} "
                     f"({current_day.date.strftime('%B %d')}): "
                     f"{current_day.title}")
        for a in current_day.activities:
            if a.is_substitute:
                continue
            status = '[DONE]' if a.is_completed else '[RULED OUT]' if a.is_eliminated else '[ ]'
            time_info = f" @ {a.start_time}" if a.start_time else f" ({a.time_slot})" if a.time_slot else ""
            parts.append(f"  {status} {a.title}{time_info}")

    tomorrow = today + timedelta(days=1)
    next_day = Day.query.filter(Day.date == tomorrow).first()
    if next_day:
        parts.append(f"\nTOMORROW is Day {next_day.day_number}: {next_day.title}")
        for a in next_day.activities:
            if a.is_substitute:
                continue
            time_info = f" @ {a.start_time}" if a.start_time else f" ({a.time_slot})" if a.time_slot else ""
            parts.append(f"  {a.title}{time_info}")

    # Full itinerary summary (so chat can reference any day)
    all_days = Day.query.order_by(Day.day_number).all()
    if all_days:
        parts.append("\nFULL ITINERARY:")
        for d in all_days:
            loc = d.location.name if d.location else '?'
            act_count = sum(1 for a in d.activities if not a.is_substitute)
            done_count = sum(1 for a in d.activities if not a.is_substitute and a.is_completed)
            parts.append(f"  Day {d.day_number} ({d.date.strftime('%b %d')}): "
                         f"{d.title} @ {loc} [{done_count}/{act_count} done]")

    # All flights
    flights = Flight.query.order_by(Flight.direction, Flight.leg_number).all()
    if flights:
        parts.append("\nFLIGHTS:")
        for f in flights:
            conf = f" [Conf: {f.confirmation_number}]" if f.confirmation_number else ""
            parts.append(f"  {f.airline} {f.flight_number}: {f.route_from}->{f.route_to} "
                         f"{f.depart_date} {f.depart_time or ''} ({f.booking_status}){conf}")

    # All accommodations with full status
    accom_locs = AccommodationLocation.query.order_by(
        AccommodationLocation.check_in_date).all()
    all_options = AccommodationOption.query.all()
    opts_by_loc = {}
    for opt in all_options:
        opts_by_loc.setdefault(opt.location_id, []).append(opt)

    if accom_locs:
        parts.append("\nACCOMMODATIONS:")
        for loc in accom_locs:
            opts = opts_by_loc.get(loc.id, [])
            selected = next((o for o in opts if o.is_selected), None)
            active = [o for o in opts if not o.is_eliminated]
            parts.append(f"  {loc.location_name} ({loc.check_in_date.strftime('%b %d')}-"
                         f"{loc.check_out_date.strftime('%b %d')}, {loc.nights} nights):")
            if selected:
                conf = f" [Conf: {selected.confirmation_number}]" if selected.confirmation_number else ""
                price = f" ${selected.price_low:.0f}-{selected.price_high:.0f}/nt" if selected.price_low else ""
                total = f" Total: ${selected.total_low:.0f}-${selected.total_high:.0f}" if selected.total_low else ""
                checkin = f" Check-in: {selected.check_in_info}" if getattr(selected, 'check_in_info', None) else ""
                checkout = f" Check-out: {selected.check_out_info}" if getattr(selected, 'check_out_info', None) else ""
                parts.append(f"    SELECTED: {selected.name}{price}{total} ({selected.booking_status}){conf}{checkin}{checkout}")
            for o in active:
                if o == selected:
                    continue
                price = f" ${o.price_low:.0f}-{o.price_high:.0f}/nt" if o.price_low else ""
                parts.append(f"    #{o.rank} {o.name}{price}")
            if not selected and not active:
                parts.append(f"    NO OPTIONS — needs hotel recommendations")

    # Transport routes
    routes = TransportRoute.query.order_by(TransportRoute.sort_order).all()
    if routes:
        parts.append("\nTRANSPORT ROUTES:")
        for r in routes:
            jr = " [JR Pass]" if r.jr_pass_covered else ""
            parts.append(f"  {r.route_from}->{r.route_to}: {r.transport_type} "
                         f"{r.train_name or ''}{jr}")

    # Budget summary
    budget = BudgetItem.query.all()
    if budget:
        total_est_low = sum(b.estimated_low or 0 for b in budget)
        total_est_high = sum(b.estimated_high or 0 for b in budget)
        total_actual = sum(b.actual_amount or 0 for b in budget)
        parts.append(f"\nBUDGET: Estimated ${total_est_low:.0f}-${total_est_high:.0f}, "
                     f"Actual so far: ${total_actual:.0f}")

    return '\n'.join(parts) if parts else 'Trip planning in early stages.'
