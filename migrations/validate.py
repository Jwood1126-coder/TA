"""Boot-time schedule validation. Prints warnings for data inconsistencies."""

from models import (Activity, Day, Trip, AccommodationLocation,
                    AccommodationOption, Flight, TransportRoute, Location, db)


def validate_schedule(app):
    """Post-migration schedule validation. Prints warnings for conflicts.
    Runs on every boot to catch data issues early."""
    trip = Trip.query.first()
    if not trip:
        return

    warnings = []
    days = Day.query.order_by(Day.day_number).all()

    # --- Check 1: Accommodation date chain gaps/overlaps ---
    # Skip locations where ALL options are eliminated (e.g. Kanazawa, merged Budget)
    all_locs = AccommodationLocation.query.order_by(
        AccommodationLocation.check_in_date).all()
    locs = [loc for loc in all_locs
            if loc.options and not all(o.is_eliminated for o in loc.options)]
    for i in range(len(locs) - 1):
        curr = locs[i]
        nxt = locs[i + 1]
        if curr.check_out_date and nxt.check_in_date:
            gap = (nxt.check_in_date - curr.check_out_date).days
            if gap > 0:
                warnings.append(
                    f"ACCOM GAP: {gap} night(s) gap between "
                    f"{curr.location_name} checkout ({curr.check_out_date}) and "
                    f"{nxt.location_name} checkin ({nxt.check_in_date})")
            elif gap < 0:
                warnings.append(
                    f"ACCOM OVERLAP: {abs(gap)} night(s) overlap between "
                    f"{curr.location_name} and {nxt.location_name}")

    # --- Check 2: Accommodation num_nights consistency ---
    for loc in locs:
        if loc.check_in_date and loc.check_out_date:
            expected = (loc.check_out_date - loc.check_in_date).days
            if loc.num_nights and loc.num_nights != expected:
                warnings.append(
                    f"ACCOM NIGHTS: {loc.location_name} says {loc.num_nights} nights "
                    f"but dates span {expected} nights "
                    f"({loc.check_in_date} -> {loc.check_out_date})")

    # --- Check 3: Departure day activities after flight ---
    flights = Flight.query.all()
    for f in flights:
        if f.direction == 'return' and f.depart_date:
            dep_day = next(
                (d for d in days if d.date == f.depart_date), None)
            if dep_day:
                late_activities = Activity.query.filter_by(
                    day_id=dep_day.id, is_eliminated=False
                ).filter(
                    Activity.time_slot.in_(['evening', 'night'])
                ).all()
                for a in late_activities:
                    if not a.is_substitute:
                        warnings.append(
                            f"DEPARTURE CONFLICT: '{a.title}' ({a.time_slot}) on "
                            f"departure day {dep_day.day_number} -- flight {f.flight_number} "
                            f"departs at {f.depart_time}")

    # --- Check 4: Overpacked days (>10 non-eliminated activities) ---
    for d in days:
        active = Activity.query.filter_by(
            day_id=d.id, is_eliminated=False, is_substitute=False
        ).count()
        if active > 10:
            warnings.append(
                f"OVERPACKED: Day {d.day_number} ({d.title}) has {active} active activities")

    # --- Check 5: Multiple selected options per location ---
    for loc in all_locs:
        selected_count = sum(1 for o in loc.options if o.is_selected)
        if selected_count > 1:
            warnings.append(
                f"MULTI-SELECT: {loc.location_name} has {selected_count} selected options (should be 0 or 1)")

    # --- Auto-fix: eliminated options should not claim confirmed/booked ---
    for loc in all_locs:
        for opt in loc.options:
            if opt.is_eliminated and opt.booking_status in ('confirmed', 'booked'):
                old_status = opt.booking_status
                opt.booking_status = 'cancelled'
                db.session.commit()
                warnings.append(
                    f"AUTO-FIX: '{opt.name}' was eliminated but {old_status} "
                    f"— downgraded to cancelled")

    # --- Check 6: Document integrity (confirmed without document) ---
    for loc in all_locs:
        for opt in loc.options:
            if opt.booking_status == 'confirmed' and not opt.document_id:
                warnings.append(
                    f"DOC INTEGRITY: '{opt.name}' is confirmed but has no linked document")
            if opt.confirmation_number and not opt.document_id and opt.is_selected:
                warnings.append(
                    f"DOC SUSPICIOUS: '{opt.name}' has conf# {opt.confirmation_number} but no document linked")

    for f in flights:
        if f.booking_status == 'confirmed' and not f.document_id:
            warnings.append(
                f"DOC INTEGRITY: Flight {f.flight_number} is confirmed but has no linked document")

    if warnings:
        print(f"\n{'='*60}")
        print(f"SCHEDULE VALIDATION: {len(warnings)} warning(s)")
        print(f"{'='*60}")
        for w in warnings:
            print(f"  ! {w}")
        print(f"{'='*60}\n")
    else:
        print("Schedule validation: all checks passed")
