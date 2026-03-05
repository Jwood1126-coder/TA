from flask import Blueprint, render_template, jsonify, request, Response, \
    current_app, copy_current_request_context
from models import db, ChatMessage, ChecklistItem, Day, Activity, \
    AccommodationOption, AccommodationLocation, Flight, TransportRoute, \
    BudgetItem
from datetime import date, datetime, timedelta
import json
import base64
import os

chat_bp = Blueprint('chat', __name__)

SYSTEM_PROMPT = """You are a Japan travel assistant and digital travel agent for Jake \
and his wife (both 33, from Cleveland, OH) on a 15-day cherry blossom trip, \
April 4-18, 2026.

When users share images of travel documents (flight confirmations, hotel bookings, \
train tickets, receipts, etc.), you should:
1. Read and extract ALL relevant data (dates, times, confirmation numbers, costs, addresses)
2. Use the available tools to update the trip database automatically
3. Check for any scheduling conflicts with existing plans
4. Note budget impact if costs are included
5. Confirm what you updated in your response

You have deep knowledge of Japan: restaurants, etiquette, transit, language, \
hidden gems. Be concise — they're reading this on a phone. \
Give specific, actionable answers. When suggesting schedule changes, \
explain clearly what to add, remove, or move.

You can add items to the trip checklist (pre_trip, packing, on_trip categories). \
If someone asks you to add a task, booking reminder, or checklist item, use the \
add_checklist_item tool."""

TOOLS = [
    {
        "name": "update_flight",
        "description": "Update a flight record with booking confirmation, status, or details. Match by flight number.",
        "input_schema": {
            "type": "object",
            "properties": {
                "flight_number": {"type": "string", "description": "Flight number e.g. DL123, AA456"},
                "booking_status": {"type": "string", "enum": ["not_booked", "booked", "confirmed"]},
                "confirmation_number": {"type": "string"},
                "depart_time": {"type": "string"},
                "arrive_time": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["flight_number"]
        }
    },
    {
        "name": "update_accommodation",
        "description": "Update accommodation booking status, confirmation, or address. Fuzzy matches by name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Hotel/accommodation name (partial match ok)"},
                "booking_status": {"type": "string", "enum": ["not_booked", "booked", "confirmed"]},
                "confirmation_number": {"type": "string"},
                "address": {"type": "string"},
                "user_notes": {"type": "string"},
            },
            "required": ["name"]
        }
    },
    {
        "name": "update_activity",
        "description": "Update an existing activity's address, notes, or cost. Or add a new activity to a day.",
        "input_schema": {
            "type": "object",
            "properties": {
                "day_number": {"type": "integer", "description": "Day number (1-15)"},
                "title": {"type": "string", "description": "Activity title (for matching existing or creating new)"},
                "time_slot": {"type": "string", "enum": ["morning", "afternoon", "evening", "night"]},
                "start_time": {"type": "string"},
                "cost_per_person": {"type": "number"},
                "address": {"type": "string"},
                "notes": {"type": "string"},
                "create_new": {"type": "boolean", "description": "True to add a new activity, false to update existing"},
            },
            "required": ["day_number", "title"]
        }
    },
    {
        "name": "flag_conflict",
        "description": "Alert about a scheduling conflict or issue found in the travel plans.",
        "input_schema": {
            "type": "object",
            "properties": {
                "conflict_type": {"type": "string", "description": "e.g. 'time_overlap', 'booking_mismatch', 'budget_warning'"},
                "description": {"type": "string"},
                "suggestion": {"type": "string"},
            },
            "required": ["conflict_type", "description"]
        }
    },
    {
        "name": "update_budget",
        "description": "Record an actual cost from a booking confirmation into the budget tracker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "e.g. 'flights', 'accommodation', 'transport', 'activities'"},
                "description": {"type": "string"},
                "actual_amount": {"type": "number"},
                "currency": {"type": "string", "default": "USD"},
                "notes": {"type": "string"},
            },
            "required": ["category", "actual_amount"]
        }
    },
    {
        "name": "add_checklist_item",
        "description": "Add a new item to the trip checklist (pre-trip tasks, bookings to make, packing items, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "The checklist item title"},
                "category": {"type": "string", "enum": ["pre_trip", "packing", "on_trip"], "description": "Which checklist tab"},
                "description": {"type": "string", "description": "Optional details"},
                "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                "url": {"type": "string", "description": "Optional booking or reference URL"},
            },
            "required": ["title", "category"]
        }
    }
]


def _execute_tool(tool_name, tool_input):
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
                flight.booking_status = tool_input['booking_status']
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
            for field in ['booking_status', 'confirmation_number', 'address', 'user_notes']:
                if tool_input.get(field):
                    setattr(option, field, tool_input[field])
            db.session.commit()
            loc = AccommodationLocation.query.get(option.location_id)
            return {"success": True, "message": f"Updated {option.name} at {loc.location_name}"}

        elif tool_name == "update_activity":
            day = Day.query.filter_by(day_number=tool_input['day_number']).first()
            if not day:
                return {"success": False, "error": f"Day {tool_input['day_number']} not found"}

            if tool_input.get('create_new', False):
                max_order = max([a.sort_order for a in day.activities] or [0])
                activity = Activity(
                    day_id=day.id,
                    title=tool_input['title'],
                    time_slot=tool_input.get('time_slot'),
                    start_time=tool_input.get('start_time'),
                    cost_per_person=tool_input.get('cost_per_person'),
                    address=tool_input.get('address'),
                    notes=tool_input.get('notes'),
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
                for field in ['address', 'notes', 'start_time', 'cost_per_person']:
                    if tool_input.get(field) is not None:
                        setattr(activity, field, tool_input[field])
                db.session.commit()
                return {"success": True, "message": f"Updated '{activity.title}' on Day {day.day_number}"}

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

    except Exception as e:
        return {"success": False, "error": str(e)}


@chat_bp.route('/chat')
def chat_view():
    messages = ChatMessage.query.order_by(ChatMessage.created_at).all()
    return render_template('chat.html', messages=messages)


@chat_bp.route('/api/chat/send', methods=['POST'])
def send_message():
    api_key = current_app.config.get('ANTHROPIC_API_KEY', '')
    if not api_key or api_key == 'sk-ant-your-key-here':
        return jsonify({'error': 'API key not configured. '
                        'Set ANTHROPIC_API_KEY in .env'}), 400

    # Handle both JSON and multipart form data
    image_data = None
    media_type = None
    image_filename = None

    if request.content_type and 'multipart/form-data' in request.content_type:
        user_message = request.form.get('message', '').strip()
        image_file = request.files.get('image')
        if image_file and image_file.filename:
            from blueprints.uploads import save_chat_image
            image_filename, original_path = save_chat_image(image_file)
            if image_filename and original_path:
                with open(original_path, 'rb') as f:
                    raw = f.read()
                # Resize if over 4MB for API limits
                if len(raw) > 4 * 1024 * 1024:
                    try:
                        from PIL import Image
                        import io
                        img = Image.open(io.BytesIO(raw))
                        img.thumbnail((2048, 2048))
                        buf = io.BytesIO()
                        img.save(buf, format='JPEG', quality=85)
                        raw = buf.getvalue()
                        media_type = 'image/jpeg'
                    except Exception:
                        pass
                image_data = base64.b64encode(raw).decode('utf-8')
                ext = image_filename.rsplit('.', 1)[-1].lower()
                if not media_type:
                    media_type = {
                        'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                        'png': 'image/png', 'gif': 'image/gif',
                        'webp': 'image/webp',
                    }.get(ext, 'image/jpeg')
    else:
        data = request.get_json()
        user_message = data.get('message', '').strip()

    if not user_message and not image_data:
        return jsonify({'error': 'Empty message'}), 400

    if not user_message and image_data:
        user_message = "Please analyze this travel document and extract any useful information."

    # Save user message
    user_msg = ChatMessage(
        role='user',
        content=user_message,
        image_filename=image_filename,
    )
    db.session.add(user_msg)
    db.session.commit()

    # Build context
    context = _build_context()

    # Get recent chat history
    history = ChatMessage.query.order_by(
        ChatMessage.created_at.desc()).limit(20).all()
    history.reverse()

    # Build messages for API
    messages = []
    for m in history:
        if m.id == user_msg.id:
            continue  # Will add current message with image below
        messages.append({'role': m.role, 'content': m.content})

    # Build current user message with optional image
    user_content = []
    if image_data:
        user_content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": image_data,
            }
        })
    user_content.append({"type": "text", "text": user_message})
    messages.append({"role": "user", "content": user_content})

    has_image = image_data is not None
    app = current_app._get_current_object()

    def generate():
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            system = SYSTEM_PROMPT + '\n\n' + context
            full_response = ''

            if has_image:
                # Image flow: non-streaming first call with tools
                response = client.messages.create(
                    model='claude-sonnet-4-5-20250929',
                    max_tokens=2048,
                    system=system,
                    messages=messages,
                    tools=TOOLS,
                )

                tool_results = []
                text_parts = []
                for block in response.content:
                    if block.type == 'tool_use':
                        yield f"data: {json.dumps({'processing': f'Updating: {block.name}...'})}\n\n"
                        with app.app_context():
                            result = _execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result),
                        })
                    elif block.type == 'text':
                        text_parts.append(block.text)

                if tool_results:
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append({"role": "user", "content": tool_results})
                    with client.messages.stream(
                        model='claude-sonnet-4-5-20250929',
                        max_tokens=1024,
                        system=system,
                        messages=messages,
                    ) as stream:
                        for text in stream.text_stream:
                            full_response += text
                            yield f"data: {json.dumps({'text': text})}\n\n"
                else:
                    full_response = ''.join(text_parts)
                    yield f"data: {json.dumps({'text': full_response})}\n\n"
            else:
                # Text-only: first call with tools (non-streaming to catch tool use)
                response = client.messages.create(
                    model='claude-sonnet-4-5-20250929',
                    max_tokens=1024,
                    system=system,
                    messages=messages,
                    tools=TOOLS,
                )

                tool_results = []
                text_parts = []
                for block in response.content:
                    if block.type == 'tool_use':
                        yield f"data: {json.dumps({'processing': f'Updating: {block.name}...'})}\n\n"
                        with app.app_context():
                            result = _execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result),
                        })
                    elif block.type == 'text':
                        text_parts.append(block.text)

                if tool_results:
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append({"role": "user", "content": tool_results})
                    with client.messages.stream(
                        model='claude-sonnet-4-5-20250929',
                        max_tokens=1024,
                        system=system,
                        messages=messages,
                    ) as stream:
                        for text in stream.text_stream:
                            full_response += text
                            yield f"data: {json.dumps({'text': text})}\n\n"
                else:
                    full_response = ''.join(text_parts)
                    yield f"data: {json.dumps({'text': full_response})}\n\n"

            # Save assistant response
            with app.app_context():
                assistant_msg = ChatMessage(role='assistant', content=full_response)
                db.session.add(assistant_msg)
                db.session.commit()
            yield f"data: {json.dumps({'done': True})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(generate(), mimetype='text/event-stream')


@chat_bp.route('/api/chat/history')
def chat_history():
    messages = ChatMessage.query.order_by(
        ChatMessage.created_at.desc()).limit(50).all()
    messages.reverse()
    return jsonify([{
        'role': m.role,
        'content': m.content,
        'image_filename': m.image_filename,
        'created_at': m.created_at.isoformat() if m.created_at else None,
    } for m in messages])


def _build_context():
    """Build dynamic context about the current trip state."""
    parts = []
    today = date.today()

    current_day = Day.query.filter(Day.date == today).first()
    if current_day:
        parts.append(f"TODAY is Day {current_day.day_number} "
                     f"({current_day.date.strftime('%B %d')}): "
                     f"{current_day.title}")
        activities = Activity.query.filter_by(day_id=current_day.id).order_by(
            Activity.sort_order).all()
        if activities:
            parts.append("Today's activities:")
            for a in activities:
                status = '[DONE]' if a.is_completed else '[ ]'
                parts.append(f"  {status} {a.title} ({a.time_slot or ''})")

    tomorrow = today + timedelta(days=1)
    next_day = Day.query.filter(Day.date == tomorrow).first()
    if next_day:
        parts.append(f"\nTOMORROW is Day {next_day.day_number}: "
                     f"{next_day.title}")

    # All flights
    flights = Flight.query.order_by(Flight.direction, Flight.leg_number).all()
    if flights:
        parts.append("\nFLIGHTS:")
        for f in flights:
            conf = f" [Conf: {f.confirmation_number}]" if f.confirmation_number else ""
            parts.append(f"  {f.airline} {f.flight_number}: {f.route_from}->{f.route_to} "
                         f"{f.depart_date} {f.depart_time or ''} ({f.booking_status}){conf}")

    # Booked/selected accommodations
    accom = AccommodationOption.query.filter(
        (AccommodationOption.is_selected == True) |
        (AccommodationOption.booking_status != 'not_booked')
    ).all()
    if accom:
        parts.append("\nACCOMMODATIONS:")
        for a in accom:
            loc = AccommodationLocation.query.get(a.location_id)
            conf = f" [Conf: {a.confirmation_number}]" if a.confirmation_number else ""
            parts.append(f"  {loc.location_name}: {a.name} ({a.booking_status}){conf}")

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

    return '\n'.join(parts) if parts else 'Trip has not started yet.'
