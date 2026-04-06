"""Tool executor — handles all AI tool calls against the database.

Chat-specific logic (fuzzy matching by name) lives here.
All mutations delegate to services/ for validation, cascade, and emit.
"""
from datetime import date, datetime, timedelta
from models import (ChecklistItem, Day, Activity, AccommodationOption,
                    AccommodationLocation, Flight, BudgetItem, TransportRoute)
import services.accommodations as accom_svc
import services.activities as activity_svc
import services.checklists as checklist_svc
import services.transport as transport_svc
import services.flights as flight_svc
import services.budget as budget_svc


# --- Query tool helpers ---

TIME_SLOT_ORDER = {'morning': 0, 'afternoon': 1, 'evening': 2, 'night': 3}


def _get_current_accommodation():
    """Find the accommodation for today's date."""
    today = date.today()
    loc = AccommodationLocation.query.filter(
        AccommodationLocation.check_in_date <= today,
        AccommodationLocation.check_out_date > today,
    ).first()
    if loc:
        opt = AccommodationOption.query.filter_by(
            location_id=loc.id, is_selected=True
        ).first()
        return opt, loc
    return None, None


def _format_activity_detail(a):
    """Format an activity with all useful details for on-the-go use."""
    parts = [f"{'[DONE] ' if a.is_completed else ''}{a.title}"]
    if a.start_time:
        parts[0] += f" @ {a.start_time}"
    elif a.time_slot:
        parts[0] += f" ({a.time_slot})"
    if a.description:
        parts.append(f"  Info: {a.description}")
    if a.address:
        parts.append(f"  Address: {a.address}")
    if a.maps_url:
        parts.append(f"  Directions: {a.maps_url}")
    if a.getting_there:
        parts.append(f"  Getting there: {a.getting_there}")
    if a.url:
        parts.append(f"  Link: {a.url}")
    if a.cost_note:
        parts.append(f"  Cost: {a.cost_note}")
    if getattr(a, 'book_ahead', False) and getattr(a, 'book_ahead_note', None):
        parts.append(f"  Booking: {a.book_ahead_note}")
    if a.notes:
        parts.append(f"  Notes: {a.notes}")
    return '\n'.join(parts)


def _execute_get_day_schedule(tool_input):
    """Get full detailed schedule for a specific day."""
    day = Day.query.filter_by(day_number=tool_input['day_number']).first()
    if not day:
        return {"success": False, "error": f"Day {tool_input['day_number']} not found"}

    parts = [f"Day {day.day_number} ({day.date.strftime('%A, %B %d')}): {day.title}"]

    # Check-out info
    checkout_loc = AccommodationLocation.query.filter(
        AccommodationLocation.check_out_date == day.date
    ).first()
    if checkout_loc:
        opt = AccommodationOption.query.filter_by(
            location_id=checkout_loc.id, is_selected=True
        ).first()
        if opt:
            parts.append(f"\nCHECK-OUT: {opt.name}")
            if opt.check_out_info:
                parts.append(f"  {opt.check_out_info}")
            if opt.address:
                parts.append(f"  Address: {opt.address}")

    # Activities grouped by time slot
    activities = [a for a in day.activities if not a.is_substitute and not a.is_eliminated]
    activities.sort(key=lambda a: (TIME_SLOT_ORDER.get(a.time_slot or 'afternoon', 1), a.sort_order or 999))

    current_slot = None
    for a in activities:
        slot = a.time_slot or 'afternoon'
        if slot != current_slot:
            current_slot = slot
            parts.append(f"\n{slot.upper()}:")
        parts.append(_format_activity_detail(a))

    # Transport routes for this day
    routes = TransportRoute.query.filter_by(day_id=day.id).order_by(TransportRoute.sort_order).all()
    if routes:
        parts.append("\nTRANSPORT:")
        for r in routes:
            jr = " [JR Pass]" if r.jr_pass_covered else ""
            line = f"  {r.route_from} -> {r.route_to}: {r.transport_type} {r.train_name or ''}{jr}"
            if r.duration:
                line += f" ({r.duration})"
            if r.maps_url:
                line += f"\n    Directions: {r.maps_url}"
            if r.notes:
                line += f"\n    Notes: {r.notes}"
            parts.append(line)

    # Check-in info
    checkin_loc = AccommodationLocation.query.filter(
        AccommodationLocation.check_in_date == day.date
    ).first()
    if checkin_loc:
        opt = AccommodationOption.query.filter_by(
            location_id=checkin_loc.id, is_selected=True
        ).first()
        if opt:
            parts.append(f"\nCHECK-IN: {opt.name}")
            if opt.check_in_info:
                parts.append(f"  {opt.check_in_info}")
            if opt.address:
                parts.append(f"  Address: {opt.address}")
            if opt.maps_url:
                parts.append(f"  Directions: {opt.maps_url}")
            if getattr(opt, 'phone', None):
                parts.append(f"  Host phone: {opt.phone}")

    if day.notes:
        parts.append(f"\nDAY NOTES: {day.notes}")

    return {"success": True, "schedule": '\n'.join(parts)}


def _execute_get_accommodation_info(tool_input):
    """Get full accommodation details."""
    name = tool_input.get('name', '').strip()

    if not name:
        # Get current stay based on date
        opt, loc = _get_current_accommodation()
        if not opt:
            # Try next upcoming stay
            today = date.today()
            loc = AccommodationLocation.query.filter(
                AccommodationLocation.check_in_date >= today
            ).order_by(AccommodationLocation.check_in_date).first()
            if loc:
                opt = AccommodationOption.query.filter_by(
                    location_id=loc.id, is_selected=True
                ).first()
        if not opt:
            return {"success": False, "error": "No current or upcoming accommodation found"}
    else:
        opt, err = _fuzzy_find(AccommodationOption, 'name', name)
        if err:
            return {"success": False, "error": err}
        if not opt:
            return {"success": False, "error": f"Accommodation '{name}' not found"}
        loc = AccommodationLocation.query.get(opt.location_id)

    parts = [f"{opt.name}"]
    parts.append(f"Location: {loc.location_name}")
    parts.append(f"Dates: {loc.check_in_date.strftime('%b %d')} - {loc.check_out_date.strftime('%b %d')} ({loc.nights} nights)")
    parts.append(f"Status: {opt.booking_status}")
    if opt.confirmation_number:
        parts.append(f"Confirmation: {opt.confirmation_number}")
    if opt.address:
        parts.append(f"Address: {opt.address}")
    if opt.maps_url:
        parts.append(f"Maps: {opt.maps_url}")
    if opt.check_in_info:
        parts.append(f"Check-in: {opt.check_in_info}")
    if opt.check_out_info:
        parts.append(f"Check-out: {opt.check_out_info}")
    if getattr(opt, 'phone', None):
        parts.append(f"Host phone: {opt.phone}")
    if opt.user_notes:
        parts.append(f"Notes: {opt.user_notes}")
    if opt.price_low:
        parts.append(f"Price: ${opt.price_low:.0f}-${opt.price_high:.0f}/night")

    return {"success": True, "info": '\n'.join(parts)}


def _execute_get_next_activity(tool_input):
    """Get the next upcoming activity based on current date/time."""
    today = date.today()
    now = datetime.now()
    current_hour = now.hour

    # Determine current time slot
    if current_hour < 12:
        current_slot_idx = 0  # morning
    elif current_hour < 17:
        current_slot_idx = 1  # afternoon
    elif current_hour < 20:
        current_slot_idx = 2  # evening
    else:
        current_slot_idx = 3  # night

    day = Day.query.filter(Day.date == today).first()
    if not day:
        # Trip hasn't started or already ended — find nearest upcoming day
        day = Day.query.filter(Day.date >= today).order_by(Day.date).first()
        if not day:
            return {"success": True, "info": "No upcoming activities — the trip has ended or hasn't started yet."}
        current_slot_idx = -1  # show all activities for future days

    activities = [a for a in day.activities
                  if not a.is_substitute and not a.is_eliminated and not a.is_completed]
    activities.sort(key=lambda a: (TIME_SLOT_ORDER.get(a.time_slot or 'afternoon', 1), a.sort_order or 999))

    # Filter to upcoming activities (same or later time slot)
    upcoming = [a for a in activities
                if TIME_SLOT_ORDER.get(a.time_slot or 'afternoon', 1) >= current_slot_idx]

    if not upcoming:
        # Nothing left today — check tomorrow
        tomorrow = today + timedelta(days=1)
        next_day = Day.query.filter(Day.date == tomorrow).first()
        if next_day:
            activities = [a for a in next_day.activities
                          if not a.is_substitute and not a.is_eliminated and not a.is_completed]
            activities.sort(key=lambda a: (TIME_SLOT_ORDER.get(a.time_slot or 'afternoon', 1), a.sort_order or 999))
            if activities:
                parts = [f"Nothing left today! Tomorrow (Day {next_day.day_number}, {next_day.date.strftime('%b %d')}):",
                         f"First up: {_format_activity_detail(activities[0])}"]
                if len(activities) > 1:
                    parts.append(f"\nThen: {activities[1].title}" + (f" @ {activities[1].start_time}" if activities[1].start_time else ""))
                return {"success": True, "info": '\n'.join(parts)}
        return {"success": True, "info": "No more activities scheduled for today or tomorrow!"}

    next_act = upcoming[0]
    parts = [f"NEXT UP (Day {day.day_number}):", _format_activity_detail(next_act)]

    if len(upcoming) > 1:
        parts.append(f"\nAFTER THAT: {upcoming[1].title}" + (f" @ {upcoming[1].start_time}" if upcoming[1].start_time else ""))
    if len(upcoming) > 2:
        parts.append(f"THEN: {upcoming[2].title}" + (f" @ {upcoming[2].start_time}" if upcoming[2].start_time else ""))

    remaining = len(upcoming) - 1
    if remaining > 2:
        parts.append(f"\n({remaining} more activities remaining today)")

    return {"success": True, "info": '\n'.join(parts)}


def _execute_search_itinerary(tool_input):
    """Search activities, transport, and accommodations by keyword."""
    query = tool_input['query'].strip()
    if not query:
        return {"success": False, "error": "Search query is empty"}

    results = []

    # Search activities
    matches = Activity.query.filter(
        Activity.is_eliminated == False,
        (Activity.title.ilike(f"%{query}%") |
         Activity.description.ilike(f"%{query}%") |
         Activity.address.ilike(f"%{query}%") |
         Activity.notes.ilike(f"%{query}%"))
    ).all()

    for a in matches:
        day = Day.query.get(a.day_id)
        if day:
            time_str = f" @ {a.start_time}" if a.start_time else f" ({a.time_slot})" if a.time_slot else ""
            entry = f"Day {day.day_number} ({day.date.strftime('%b %d')}): {a.title}{time_str}"
            if a.address:
                entry += f"\n  Address: {a.address}"
            if a.maps_url:
                entry += f"\n  Directions: {a.maps_url}"
            results.append(entry)

    # Search transport routes
    route_matches = TransportRoute.query.filter(
        TransportRoute.route_from.ilike(f"%{query}%") |
        TransportRoute.route_to.ilike(f"%{query}%") |
        TransportRoute.notes.ilike(f"%{query}%")
    ).all()

    for r in route_matches:
        day = Day.query.get(r.day_id) if r.day_id else None
        day_str = f"Day {day.day_number} ({day.date.strftime('%b %d')})" if day else "Unassigned"
        entry = f"{day_str}: Transport {r.route_from} -> {r.route_to} ({r.transport_type})"
        if r.maps_url:
            entry += f"\n  Directions: {r.maps_url}"
        results.append(entry)

    # Search accommodations
    accom_matches = AccommodationOption.query.filter(
        AccommodationOption.is_eliminated == False,
        (AccommodationOption.name.ilike(f"%{query}%") |
         AccommodationOption.address.ilike(f"%{query}%") |
         AccommodationOption.user_notes.ilike(f"%{query}%"))
    ).all()

    for o in accom_matches:
        loc = AccommodationLocation.query.get(o.location_id)
        entry = f"Stay: {o.name} ({loc.location_name}, {loc.check_in_date.strftime('%b %d')}-{loc.check_out_date.strftime('%b %d')})"
        if o.address:
            entry += f"\n  Address: {o.address}"
        results.append(entry)

    if not results:
        return {"success": True, "results": f"No matches found for '{query}'."}
    return {"success": True, "results": f"Found {len(results)} match(es) for '{query}':\n\n" + '\n\n'.join(results)}


def _execute_get_directions(tool_input):
    """Get directions to a destination."""
    dest = tool_input['to'].strip()
    origin = tool_input.get('from', '').strip()
    day_num = tool_input.get('day_number')

    # Try to find the destination as an activity
    activity = Activity.query.filter(
        Activity.is_eliminated == False,
        Activity.title.ilike(f"%{dest}%")
    ).first()

    if activity:
        parts = [f"Directions to: {activity.title}"]
        if activity.address:
            parts.append(f"Address: {activity.address}")
        if activity.maps_url:
            parts.append(f"Google Maps Directions: {activity.maps_url}")
        if activity.getting_there:
            parts.append(f"Getting there: {activity.getting_there}")
        return {"success": True, "directions": '\n'.join(parts)}

    # Try accommodation
    opt, err = _fuzzy_find(AccommodationOption, 'name', dest)
    if opt:
        parts = [f"Directions to: {opt.name}"]
        if opt.address:
            parts.append(f"Address: {opt.address}")
        if opt.maps_url:
            parts.append(f"Google Maps Directions: {opt.maps_url}")
        return {"success": True, "directions": '\n'.join(parts)}

    # Try transport route
    if origin:
        route = TransportRoute.query.filter(
            TransportRoute.route_to.ilike(f"%{dest}%")
        ).first()
    else:
        route = TransportRoute.query.filter(
            TransportRoute.route_to.ilike(f"%{dest}%")
        ).first()

    if route:
        parts = [f"Route: {route.route_from} -> {route.route_to}"]
        parts.append(f"Type: {route.transport_type} {route.train_name or ''}")
        if route.duration:
            parts.append(f"Duration: {route.duration}")
        if route.maps_url:
            parts.append(f"Google Maps Directions: {route.maps_url}")
        if route.notes:
            parts.append(f"Notes: {route.notes}")
        jr = "Yes" if route.jr_pass_covered else "No"
        parts.append(f"JR Pass covered: {jr}")
        return {"success": True, "directions": '\n'.join(parts)}

    # Build a generic Google Maps directions URL
    if not origin:
        opt, loc = _get_current_accommodation()
        origin = opt.name if opt else "current location"

    maps_url = (f"https://www.google.com/maps/dir/?api=1"
                f"&origin={origin.replace(' ', '+')}"
                f"&destination={dest.replace(' ', '+')}"
                f"&travelmode=transit")
    return {"success": True, "directions": f"No saved directions found.\n\nGenerated link:\n{maps_url}"}


def _fuzzy_find(model_class, name_field, search_term, filters=None):
    """Find a record by name: exact match first, then ilike contains.

    Returns (match, error_message). error_message is None on success.
    If multiple matches found, returns (None, error listing matches).
    """
    col = getattr(model_class, name_field)
    base_query = model_class.query
    if filters:
        for f in filters:
            base_query = base_query.filter(f)

    # 1. Try exact match (case-insensitive)
    exact = base_query.filter(col.ilike(search_term)).first()
    if exact:
        return exact, None

    # 2. Try contains match
    matches = base_query.filter(col.ilike(f"%{search_term}%")).all()
    if len(matches) == 1:
        return matches[0], None
    if len(matches) == 0:
        return None, None  # caller handles "not found" message
    # Multiple matches — return error with list
    names = [getattr(m, name_field) for m in matches]
    return None, f"Multiple matches for '{search_term}': {', '.join(names)}. Please be more specific."


def execute_tool(tool_name, tool_input):
    """Execute a tool call from Claude and return the result."""
    try:
        # --- Query tools (read-only, on-the-go helpers) ---
        if tool_name == "get_day_schedule":
            return _execute_get_day_schedule(tool_input)
        elif tool_name == "get_accommodation_info":
            return _execute_get_accommodation_info(tool_input)
        elif tool_name == "get_next_activity":
            return _execute_get_next_activity(tool_input)
        elif tool_name == "search_itinerary":
            return _execute_search_itinerary(tool_input)
        elif tool_name == "get_directions":
            return _execute_get_directions(tool_input)

        # --- Mutation tools ---
        elif tool_name == "update_flight":
            flight_num = tool_input['flight_number'].strip().upper()
            flight = Flight.query.filter(
                Flight.flight_number.ilike(f"%{flight_num}%")
            ).first()
            if not flight:
                return {"success": False, "error": f"Flight {flight_num} not found in itinerary"}
            fields = {}
            for field in ('booking_status', 'confirmation_number',
                          'depart_time', 'arrive_time', 'notes'):
                if tool_input.get(field) is not None:
                    fields[field] = tool_input[field]
            try:
                flight = flight_svc.update(flight.id, fields)
            except ValueError as e:
                return {"success": False, "error": str(e)}
            return {"success": True, "message": f"Updated flight {flight.flight_number} — status: {flight.booking_status}"}

        elif tool_name == "update_accommodation":
            name = tool_input['name']
            option, err = _fuzzy_find(AccommodationOption, 'name', name)
            if err:
                return {"success": False, "error": err}
            if not option:
                return {"success": False, "error": f"Accommodation '{name}' not found"}
            # Build fields dict for the service
            fields = {}
            for field in ['booking_status', 'confirmation_number', 'address', 'user_notes',
                          'check_in_info', 'check_out_info', 'price_low', 'price_high']:
                if tool_input.get(field) is not None:
                    fields[field] = tool_input[field]
            try:
                option = accom_svc.update_status(option.id, fields)
            except ValueError as e:
                return {"success": False, "error": str(e)}
            loc = AccommodationLocation.query.get(option.location_id)
            return {"success": True, "message": f"Updated {option.name} at {loc.location_name}"}

        elif tool_name == "add_accommodation_option":
            loc_name = tool_input['location_name']
            accom_loc = AccommodationLocation.query.filter(
                AccommodationLocation.location_name.ilike(f"%{loc_name}%")
            ).first()
            if not accom_loc:
                return {"success": False, "error": f"No accommodation location matching '{loc_name}'. "
                        f"Available: {', '.join(l.location_name for l in AccommodationLocation.query.all())}"}
            try:
                option, loc, overlap_warning = accom_svc.add_option(accom_loc.id, tool_input)
            except ValueError as e:
                return {"success": False, "error": str(e)}
            msg = (f"Added '{option.name}' as option #{option.rank} for {loc.location_name} "
                   f"({loc.check_in_date.strftime('%b %d')}-{loc.check_out_date.strftime('%b %d')})")
            if overlap_warning:
                msg += f" ⚠️ {overlap_warning}"
            return {"success": True, "message": msg}

        elif tool_name == "select_accommodation":
            name = tool_input['name']
            select = tool_input.get('select', True)
            option, err = _fuzzy_find(AccommodationOption, 'name', name)
            if err:
                return {"success": False, "error": err}
            if not option:
                return {"success": False, "error": f"Accommodation '{name}' not found"}
            if select:
                accom_svc.select(option.id)
            else:
                accom_svc.deselect(option.id)
            loc = AccommodationLocation.query.get(option.location_id)
            action = "Selected" if select else "Deselected"
            return {"success": True, "message": f"{action} '{option.name}' for {loc.location_name}"}

        elif tool_name == "eliminate_accommodation":
            name = tool_input['name']
            option, err = _fuzzy_find(AccommodationOption, 'name', name)
            if err:
                return {"success": False, "error": err}
            if not option:
                return {"success": False, "error": f"Accommodation '{name}' not found"}
            try:
                option = accom_svc.eliminate(option.id, eliminate=tool_input.get('eliminate', True))
            except ValueError as e:
                return {"success": False, "error": str(e)}
            loc = AccommodationLocation.query.get(option.location_id)
            action = "Eliminated" if option.is_eliminated else "Restored"
            return {"success": True, "message": f"{action} '{option.name}' for {loc.location_name}"}

        elif tool_name == "update_activity":
            day = Day.query.filter_by(day_number=tool_input['day_number']).first()
            if not day:
                return {"success": False, "error": f"Day {tool_input['day_number']} not found"}

            if tool_input.get('create_new', False):
                fields = {k: v for k, v in tool_input.items()
                          if k not in ('day_number', 'create_new')}
                try:
                    activity = activity_svc.add(day.id, fields)
                except ValueError as e:
                    return {"success": False, "error": str(e)}
                return {"success": True, "message": f"Added '{activity.title}' to Day {day.day_number}"}
            else:
                activity, err = _fuzzy_find(
                    Activity, 'title', tool_input['title'],
                    filters=[Activity.day_id == day.id]
                )
                if err:
                    return {"success": False, "error": err}
                if not activity:
                    return {"success": False, "error": f"Activity '{tool_input['title']}' not found on Day {day.day_number}"}
                fields = {k: v for k, v in tool_input.items()
                          if k not in ('day_number', 'create_new') and v is not None}
                try:
                    activity_svc.update(activity.id, fields)
                except ValueError as e:
                    return {"success": False, "error": str(e)}
                return {"success": True, "message": f"Updated '{activity.title}' on Day {day.day_number}"}

        elif tool_name == "toggle_activity":
            day = Day.query.filter_by(day_number=tool_input['day_number']).first()
            if not day:
                return {"success": False, "error": f"Day {tool_input['day_number']} not found"}
            activity, err = _fuzzy_find(
                Activity, 'title', tool_input['title'],
                filters=[Activity.day_id == day.id]
            )
            if err:
                return {"success": False, "error": err}
            if not activity:
                return {"success": False, "error": f"Activity '{tool_input['title']}' not found on Day {day.day_number}"}
            activity = activity_svc.set_completed(activity.id, tool_input['completed'])
            status = "completed" if activity.is_completed else "not completed"
            return {"success": True, "message": f"Marked '{activity.title}' as {status}"}

        elif tool_name == "flag_conflict":
            return {
                "success": True,
                "message": f"Conflict flagged: {tool_input['conflict_type']} — {tool_input['description']}",
                "suggestion": tool_input.get('suggestion', '')
            }

        elif tool_name == "update_budget":
            item = BudgetItem.query.filter(
                BudgetItem.category.ilike(f"%{tool_input['category']}%")
            ).first()
            if not item:
                return {"success": False, "error": f"Budget category '{tool_input['category']}' not found"}
            try:
                item = budget_svc.record_expense(
                    item.id, tool_input['actual_amount'],
                    notes=tool_input.get('notes'))
            except ValueError as e:
                return {"success": False, "error": str(e)}
            return {"success": True, "message": f"Updated budget: {item.category} — actual: ${item.actual_amount:.0f}"}

        elif tool_name == "add_checklist_item":
            try:
                item = checklist_svc.create(tool_input)
            except ValueError as e:
                return {"success": False, "error": str(e)}
            return {"success": True, "message": f"Added '{item.title}' to {item.category} checklist"}

        elif tool_name == "toggle_checklist_item":
            item = ChecklistItem.query.filter(
                ChecklistItem.title.ilike(f"%{tool_input['title']}%")
            ).first()
            if not item:
                return {"success": False, "error": f"Checklist item '{tool_input['title']}' not found"}
            item = checklist_svc.set_completed(item.id, tool_input['completed'])
            status = "completed" if item.is_completed else "not completed"
            return {"success": True, "message": f"Marked '{item.title}' as {status}"}

        elif tool_name == "delete_checklist_item":
            item = ChecklistItem.query.filter(
                ChecklistItem.title.ilike(f"%{tool_input['title']}%")
            ).first()
            if not item:
                return {"success": False, "error": f"Checklist item '{tool_input['title']}' not found"}
            title = item.title
            try:
                checklist_svc.delete(item.id, enforce_category=False)
            except ValueError as e:
                return {"success": False, "error": str(e)}
            return {"success": True, "message": f"Deleted checklist item '{title}'"}

        elif tool_name == "delete_accommodation":
            name = tool_input['name']
            option, err = _fuzzy_find(AccommodationOption, 'name', name)
            if err:
                return {"success": False, "error": err}
            if not option:
                return {"success": False, "error": f"Accommodation '{name}' not found"}
            loc = AccommodationLocation.query.get(option.location_id)
            opt_name, loc_id = accom_svc.delete(option.id)
            return {"success": True, "message": f"Deleted '{opt_name}' from {loc.location_name}"}

        elif tool_name == "eliminate_activity":
            day = Day.query.filter_by(day_number=tool_input['day_number']).first()
            if not day:
                return {"success": False, "error": f"Day {tool_input['day_number']} not found"}
            activity, err = _fuzzy_find(
                Activity, 'title', tool_input['title'],
                filters=[Activity.day_id == day.id]
            )
            if err:
                return {"success": False, "error": err}
            if not activity:
                return {"success": False, "error": f"Activity '{tool_input['title']}' not found on Day {day.day_number}"}
            activity_svc.eliminate(activity.id)
            status = "ruled out" if activity.is_eliminated else "restored"
            return {"success": True, "message": f"Activity '{activity.title}' {status} on Day {day.day_number}"}

        elif tool_name == "delete_activity":
            day = Day.query.filter_by(day_number=tool_input['day_number']).first()
            if not day:
                return {"success": False, "error": f"Day {tool_input['day_number']} not found"}
            activity, err = _fuzzy_find(
                Activity, 'title', tool_input['title'],
                filters=[Activity.day_id == day.id]
            )
            if err:
                return {"success": False, "error": err}
            if not activity:
                return {"success": False, "error": f"Activity '{tool_input['title']}' not found on Day {day.day_number}"}
            title, day_id = activity_svc.delete(activity.id)
            return {"success": True, "message": f"Deleted '{title}' from Day {day.day_number}"}

        elif tool_name == "update_day_notes":
            day = Day.query.filter_by(day_number=tool_input['day_number']).first()
            if not day:
                return {"success": False, "error": f"Day {tool_input['day_number']} not found"}
            activity_svc.update_day_notes(day.id, tool_input['notes'])
            return {"success": True, "message": f"Updated notes for Day {day.day_number}"}

        elif tool_name == "add_transport_route":
            # Resolve day_number to day_id
            fields = dict(tool_input)
            day = None
            if 'day_number' in fields:
                day = Day.query.filter_by(day_number=fields.pop('day_number')).first()
                if day:
                    fields['day_id'] = day.id
            try:
                route = transport_svc.add(fields)
            except ValueError as e:
                return {"success": False, "error": str(e)}
            jr = " (JR Pass ✓)" if route.jr_pass_covered else ""
            msg = f"Added route: {route.route_from} → {route.route_to} ({route.transport_type}){jr}"
            # Soft geographic warning if route origin doesn't match day's location
            if day and day.location:
                loc_name = day.location.name.lower()
                origin = route.route_from.lower()
                if loc_name not in origin and origin not in loc_name:
                    msg += (f" ⚠️ Note: Day {day.day_number} is in {day.location.name} "
                            f"but route origin is {route.route_from} — verify this is the correct day.")
            return {"success": True, "message": msg}

        elif tool_name == "update_transport_route":
            # Fuzzy match by from/to
            route_from = tool_input['route_from']
            route_to = tool_input['route_to']
            route = TransportRoute.query.filter(
                TransportRoute.route_from.ilike(f"%{route_from}%"),
                TransportRoute.route_to.ilike(f"%{route_to}%")
            ).first()
            if not route:
                return {"success": False, "error": f"Route '{route_from} → {route_to}' not found"}
            fields = {}
            if tool_input.get('new_route_from'):
                fields['route_from'] = tool_input['new_route_from']
            if tool_input.get('new_route_to'):
                fields['route_to'] = tool_input['new_route_to']
            for f in ('transport_type', 'train_name', 'duration',
                      'jr_pass_covered', 'cost_if_not_covered', 'notes', 'url'):
                if tool_input.get(f) is not None:
                    fields[f] = tool_input[f]
            if 'day_number' in tool_input:
                day = Day.query.filter_by(day_number=tool_input['day_number']).first()
                if day:
                    fields['day_id'] = day.id
            try:
                transport_svc.update(route.id, fields)
            except ValueError as e:
                return {"success": False, "error": str(e)}
            return {"success": True, "message": f"Updated route: {route.route_from} → {route.route_to}"}

        elif tool_name == "delete_transport_route":
            route_from = tool_input['route_from']
            route_to = tool_input['route_to']
            route = TransportRoute.query.filter(
                TransportRoute.route_from.ilike(f"%{route_from}%"),
                TransportRoute.route_to.ilike(f"%{route_to}%")
            ).first()
            if not route:
                return {"success": False, "error": f"Route '{route_from} → {route_to}' not found"}
            desc, day_id = transport_svc.delete(route.id)
            return {"success": True, "message": f"Deleted route: {desc}"}

    except Exception as e:
        return {"success": False, "error": str(e)}

    return {"success": False, "error": f"Unknown tool: {tool_name}"}
