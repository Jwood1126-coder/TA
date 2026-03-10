"""Tool executor — handles all AI tool calls against the database."""

from datetime import datetime
from models import (db, ChecklistItem, Day, Activity, AccommodationOption,
                    AccommodationLocation, Flight, BudgetItem)
from guardrails import (validate_time_slot, validate_booking_status,
                        validate_non_negative, validate_document_status,
                        check_accom_date_overlap)


def execute_tool(tool_name, tool_input):
    """Execute a tool call from Claude and return the result."""
    try:
        if tool_name == "update_flight":
            flight_num = tool_input['flight_number'].strip().upper()
            flight = Flight.query.filter(
                Flight.flight_number.ilike(f"%{flight_num}%")
            ).first()
            if not flight:
                return {"success": False, "error": f"Flight {flight_num} not found in itinerary"}
            if tool_input.get('booking_status'):
                try:
                    new_status = validate_booking_status(tool_input['booking_status'])
                    validate_document_status(new_status, flight.document_id,
                                             f'flight {flight.flight_number}')
                    flight.booking_status = new_status
                except ValueError as e:
                    return {"success": False, "error": str(e)}
            if tool_input.get('confirmation_number'):
                flight.confirmation_number = tool_input['confirmation_number']
            if tool_input.get('depart_time'):
                flight.depart_time = tool_input['depart_time']
            if tool_input.get('arrive_time'):
                flight.arrive_time = tool_input['arrive_time']
            if tool_input.get('notes'):
                flight.notes = tool_input['notes']
            db.session.commit()
            return {"success": True, "message": f"Updated flight {flight.flight_number} — status: {flight.booking_status}"}

        elif tool_name == "update_accommodation":
            name = tool_input['name']
            option = AccommodationOption.query.filter(
                AccommodationOption.name.ilike(f"%{name}%")
            ).first()
            if not option:
                return {"success": False, "error": f"Accommodation '{name}' not found"}
            if tool_input.get('booking_status'):
                try:
                    new_status = validate_booking_status(tool_input['booking_status'])
                    validate_document_status(new_status, option.document_id,
                                             f"accommodation '{option.name}'")
                    tool_input['booking_status'] = new_status
                except ValueError as e:
                    return {"success": False, "error": str(e)}
            for field in ['booking_status', 'confirmation_number', 'address', 'user_notes',
                          'check_in_info', 'check_out_info']:
                if tool_input.get(field):
                    setattr(option, field, tool_input[field])
            # Handle price updates
            if tool_input.get('price_low') is not None:
                try:
                    option.price_low = validate_non_negative(tool_input['price_low'], 'price_low')
                except ValueError as e:
                    return {"success": False, "error": str(e)}
            if tool_input.get('price_high') is not None:
                try:
                    option.price_high = validate_non_negative(tool_input['price_high'], 'price_high')
                except ValueError as e:
                    return {"success": False, "error": str(e)}
            # Recalculate totals if prices changed
            if option.price_low and option.location_id:
                loc = AccommodationLocation.query.get(option.location_id)
                if loc:
                    option.total_low = option.price_low * loc.num_nights
                    if option.price_high:
                        option.total_high = option.price_high * loc.num_nights
            db.session.commit()
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
            # Check for date overlaps with other locations
            overlap_warning = check_accom_date_overlap(accom_loc)
            # Determine next rank
            existing = AccommodationOption.query.filter_by(location_id=accom_loc.id).all()
            next_rank = max([o.rank for o in existing] or [0]) + 1
            # Calculate totals from per-night prices
            price_low = tool_input.get('price_low')
            price_high = tool_input.get('price_high')
            total_low = price_low * accom_loc.num_nights if price_low else None
            total_high = price_high * accom_loc.num_nights if price_high else None
            option = AccommodationOption(
                location_id=accom_loc.id,
                rank=next_rank,
                name=tool_input['name'],
                property_type=tool_input.get('property_type'),
                price_low=price_low,
                price_high=price_high,
                total_low=total_low,
                total_high=total_high,
                address=tool_input.get('address'),
                booking_url=tool_input.get('booking_url'),
                alt_booking_url=tool_input.get('alt_booking_url'),
                standout=tool_input.get('standout'),
                breakfast_included=tool_input.get('breakfast_included', False),
                has_onsen=tool_input.get('has_onsen', False),
                user_notes=tool_input.get('user_notes'),
            )
            db.session.add(option)
            db.session.commit()
            msg = (f"Added '{option.name}' as option #{next_rank} for {accom_loc.location_name} "
                   f"({accom_loc.check_in_date.strftime('%b %d')}-{accom_loc.check_out_date.strftime('%b %d')})")
            if overlap_warning:
                msg += f" ⚠️ {overlap_warning}"
            return {"success": True, "message": msg}

        elif tool_name == "select_accommodation":
            name = tool_input['name']
            select = tool_input.get('select', True)
            option = AccommodationOption.query.filter(
                AccommodationOption.name.ilike(f"%{name}%")
            ).first()
            if not option:
                return {"success": False, "error": f"Accommodation '{name}' not found"}
            if select:
                # Deselect all other options for this location first
                AccommodationOption.query.filter_by(
                    location_id=option.location_id
                ).update({'is_selected': False})
                option.is_selected = True
            else:
                option.is_selected = False
            db.session.commit()
            loc = AccommodationLocation.query.get(option.location_id)
            action = "Selected" if select else "Deselected"
            return {"success": True, "message": f"{action} '{option.name}' for {loc.location_name}"}

        elif tool_name == "eliminate_accommodation":
            name = tool_input['name']
            eliminate = tool_input.get('eliminate', True)
            option = AccommodationOption.query.filter(
                AccommodationOption.name.ilike(f"%{name}%")
            ).first()
            if not option:
                return {"success": False, "error": f"Accommodation '{name}' not found"}
            if eliminate and option.booking_status in ('booked', 'confirmed'):
                return {"success": False, "error": f"Cannot eliminate '{option.name}' — it is {option.booking_status}. "
                        f"Change booking status first."}
            option.is_eliminated = eliminate
            if eliminate and option.is_selected:
                option.is_selected = False
            db.session.commit()
            loc = AccommodationLocation.query.get(option.location_id)
            action = "Eliminated" if eliminate else "Restored"
            return {"success": True, "message": f"{action} '{option.name}' for {loc.location_name}"}

        elif tool_name == "update_activity":
            day = Day.query.filter_by(day_number=tool_input['day_number']).first()
            if not day:
                return {"success": False, "error": f"Day {tool_input['day_number']} not found"}
            # Validate inputs
            try:
                validated_ts = validate_time_slot(tool_input.get('time_slot'))
                validated_cost = validate_non_negative(tool_input.get('cost_per_person'), 'cost_per_person')
            except ValueError as e:
                return {"success": False, "error": str(e)}

            if tool_input.get('create_new', False):
                max_order = max([a.sort_order for a in day.activities] or [0])
                activity = Activity(
                    day_id=day.id,
                    title=tool_input['title'],
                    time_slot=validated_ts,
                    start_time=tool_input.get('start_time'),
                    cost_per_person=validated_cost,
                    cost_note=tool_input.get('cost_note'),
                    address=tool_input.get('address'),
                    description=tool_input.get('description'),
                    url=tool_input.get('url'),
                    notes=tool_input.get('notes'),
                    is_optional=tool_input.get('is_optional', False),
                    sort_order=max_order + 1,
                )
                db.session.add(activity)
                db.session.commit()
                return {"success": True, "message": f"Added '{activity.title}' to Day {day.day_number}"}
            else:
                activity = Activity.query.filter(
                    Activity.day_id == day.id,
                    Activity.title.ilike(f"%{tool_input['title']}%")
                ).first()
                if not activity:
                    return {"success": False, "error": f"Activity '{tool_input['title']}' not found on Day {day.day_number}"}
                # Apply validated values for time_slot and cost
                if validated_ts is not None:
                    activity.time_slot = validated_ts
                if validated_cost is not None:
                    activity.cost_per_person = validated_cost
                for field in ['address', 'notes', 'start_time',
                              'cost_note', 'description', 'url', 'is_optional']:
                    if tool_input.get(field) is not None:
                        setattr(activity, field, tool_input[field])
                db.session.commit()
                return {"success": True, "message": f"Updated '{activity.title}' on Day {day.day_number}"}

        elif tool_name == "toggle_activity":
            day = Day.query.filter_by(day_number=tool_input['day_number']).first()
            if not day:
                return {"success": False, "error": f"Day {tool_input['day_number']} not found"}
            activity = Activity.query.filter(
                Activity.day_id == day.id,
                Activity.title.ilike(f"%{tool_input['title']}%")
            ).first()
            if not activity:
                return {"success": False, "error": f"Activity '{tool_input['title']}' not found on Day {day.day_number}"}
            activity.is_completed = tool_input['completed']
            activity.completed_at = datetime.utcnow() if activity.is_completed else None
            db.session.commit()
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
            if item:
                item.actual_amount = (item.actual_amount or 0) + tool_input['actual_amount']
                if tool_input.get('notes'):
                    item.notes = (item.notes or '') + '\n' + tool_input['notes']
                db.session.commit()
                return {"success": True, "message": f"Updated budget: {item.category} — actual: ${item.actual_amount:.0f}"}
            return {"success": False, "error": f"Budget category '{tool_input['category']}' not found"}

        elif tool_name == "add_checklist_item":
            max_order = db.session.query(
                db.func.max(ChecklistItem.sort_order)
            ).filter_by(category=tool_input['category']).scalar() or 0
            item = ChecklistItem(
                title=tool_input['title'],
                category=tool_input['category'],
                description=tool_input.get('description'),
                priority=tool_input.get('priority', 'medium'),
                url=tool_input.get('url'),
                sort_order=max_order + 1,
            )
            db.session.add(item)
            db.session.commit()
            return {"success": True, "message": f"Added '{item.title}' to {item.category} checklist"}

        elif tool_name == "toggle_checklist_item":
            item = ChecklistItem.query.filter(
                ChecklistItem.title.ilike(f"%{tool_input['title']}%")
            ).first()
            if not item:
                return {"success": False, "error": f"Checklist item '{tool_input['title']}' not found"}
            item.is_completed = tool_input['completed']
            item.completed_at = datetime.utcnow() if item.is_completed else None
            db.session.commit()
            status = "completed" if item.is_completed else "not completed"
            return {"success": True, "message": f"Marked '{item.title}' as {status}"}

        elif tool_name == "delete_checklist_item":
            item = ChecklistItem.query.filter(
                ChecklistItem.title.ilike(f"%{tool_input['title']}%")
            ).first()
            if not item:
                return {"success": False, "error": f"Checklist item '{tool_input['title']}' not found"}
            title = item.title
            db.session.delete(item)
            db.session.commit()
            return {"success": True, "message": f"Deleted checklist item '{title}'"}

        elif tool_name == "delete_accommodation":
            name = tool_input['name']
            option = AccommodationOption.query.filter(
                AccommodationOption.name.ilike(f"%{name}%")
            ).first()
            if not option:
                return {"success": False, "error": f"Accommodation '{name}' not found"}
            loc = AccommodationLocation.query.get(option.location_id)
            opt_name = option.name
            loc_id = option.location_id
            db.session.delete(option)
            # Re-rank remaining options
            remaining = AccommodationOption.query.filter_by(
                location_id=loc_id).order_by(AccommodationOption.rank).all()
            for i, opt in enumerate(remaining, 1):
                opt.rank = i
            db.session.commit()
            return {"success": True, "message": f"Deleted '{opt_name}' from {loc.location_name}"}

        elif tool_name == "eliminate_activity":
            day = Day.query.filter_by(day_number=tool_input['day_number']).first()
            if not day:
                return {"success": False, "error": f"Day {tool_input['day_number']} not found"}
            activity = Activity.query.filter(
                Activity.day_id == day.id,
                Activity.title.ilike(f"%{tool_input['title']}%")
            ).first()
            if not activity:
                return {"success": False, "error": f"Activity '{tool_input['title']}' not found on Day {day.day_number}"}
            activity.is_eliminated = not activity.is_eliminated
            db.session.commit()
            status = "ruled out" if activity.is_eliminated else "restored"
            return {"success": True, "message": f"Activity '{activity.title}' {status} on Day {day.day_number}"}

        elif tool_name == "delete_activity":
            day = Day.query.filter_by(day_number=tool_input['day_number']).first()
            if not day:
                return {"success": False, "error": f"Day {tool_input['day_number']} not found"}
            activity = Activity.query.filter(
                Activity.day_id == day.id,
                Activity.title.ilike(f"%{tool_input['title']}%")
            ).first()
            if not activity:
                return {"success": False, "error": f"Activity '{tool_input['title']}' not found on Day {day.day_number}"}
            title = activity.title
            db.session.delete(activity)
            db.session.commit()
            return {"success": True, "message": f"Deleted '{title}' from Day {day.day_number}"}

        elif tool_name == "update_day_notes":
            day = Day.query.filter_by(day_number=tool_input['day_number']).first()
            if not day:
                return {"success": False, "error": f"Day {tool_input['day_number']} not found"}
            day.notes = tool_input['notes']
            db.session.commit()
            return {"success": True, "message": f"Updated notes for Day {day.day_number}"}

    except Exception as e:
        return {"success": False, "error": str(e)}
