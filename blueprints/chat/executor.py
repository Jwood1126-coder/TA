"""Tool executor — handles all AI tool calls against the database.

Chat-specific logic (fuzzy matching by name) lives here.
All mutations delegate to services/ for validation, cascade, and emit.
"""
from datetime import datetime
from models import (db, ChecklistItem, Day, Activity, AccommodationOption,
                    AccommodationLocation, Flight, BudgetItem)
from guardrails import (validate_time_slot, validate_booking_status,
                        validate_non_negative, validate_document_status,
                        check_accom_date_overlap)
import services.accommodations as accom_svc
import services.activities as activity_svc
import services.checklists as checklist_svc


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
            option = AccommodationOption.query.filter(
                AccommodationOption.name.ilike(f"%{name}%")
            ).first()
            if not option:
                return {"success": False, "error": f"Accommodation '{name}' not found"}
            if select:
                accom_svc.select(option.id)
            else:
                option.is_selected = False
                db.session.commit()
            loc = AccommodationLocation.query.get(option.location_id)
            action = "Selected" if select else "Deselected"
            return {"success": True, "message": f"{action} '{option.name}' for {loc.location_name}"}

        elif tool_name == "eliminate_accommodation":
            name = tool_input['name']
            option = AccommodationOption.query.filter(
                AccommodationOption.name.ilike(f"%{name}%")
            ).first()
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
                activity = Activity.query.filter(
                    Activity.day_id == day.id,
                    Activity.title.ilike(f"%{tool_input['title']}%")
                ).first()
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
            activity = Activity.query.filter(
                Activity.day_id == day.id,
                Activity.title.ilike(f"%{tool_input['title']}%")
            ).first()
            if not activity:
                return {"success": False, "error": f"Activity '{tool_input['title']}' not found on Day {day.day_number}"}
            # Set completion directly (chat specifies explicit state, not toggle)
            activity.is_completed = tool_input['completed']
            activity.completed_at = datetime.utcnow() if activity.is_completed else None
            db.session.commit()
            from extensions import socketio
            socketio.emit('activity_toggled', {
                'id': activity.id,
                'is_completed': activity.is_completed,
                'day_id': activity.day_id,
            })
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
                amount = validate_non_negative(tool_input['actual_amount'], 'actual_amount')
                item.actual_amount = (item.actual_amount or 0) + amount
                if tool_input.get('notes'):
                    item.notes = (item.notes or '') + '\n' + tool_input['notes']
                db.session.commit()
                return {"success": True, "message": f"Updated budget: {item.category} — actual: ${item.actual_amount:.0f}"}
            return {"success": False, "error": f"Budget category '{tool_input['category']}' not found"}

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
            # Set completion directly and sync status field
            item.is_completed = tool_input['completed']
            item.completed_at = datetime.utcnow() if item.is_completed else None
            if item.is_completed and item.status != 'completed':
                item.status = 'completed'
            elif not item.is_completed and item.status == 'completed':
                item.status = 'pending'
            db.session.commit()
            from extensions import socketio
            socketio.emit('checklist_toggled', {
                'id': item.id,
                'is_completed': item.is_completed,
            })
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
            option = AccommodationOption.query.filter(
                AccommodationOption.name.ilike(f"%{name}%")
            ).first()
            if not option:
                return {"success": False, "error": f"Accommodation '{name}' not found"}
            loc = AccommodationLocation.query.get(option.location_id)
            opt_name, loc_id = accom_svc.delete(option.id)
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
            activity_svc.eliminate(activity.id)
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
            title, day_id = activity_svc.delete(activity.id)
            return {"success": True, "message": f"Deleted '{title}' from Day {day.day_number}"}

        elif tool_name == "update_day_notes":
            day = Day.query.filter_by(day_number=tool_input['day_number']).first()
            if not day:
                return {"success": False, "error": f"Day {tool_input['day_number']} not found"}
            activity_svc.update_day_notes(day.id, tool_input['notes'])
            return {"success": True, "message": f"Updated notes for Day {day.day_number}"}

    except Exception as e:
        return {"success": False, "error": str(e)}
