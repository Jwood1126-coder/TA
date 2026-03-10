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

SYSTEM_PROMPT = """You are the personal travel agent and Japan expert for Jake and his wife \
(both 33, from Cleveland, OH). They're taking a 14-day cherry blossom trip, April 5-18, 2026. \
You manage their entire trip through this app — you ARE the app's brain.

PERSONALITY & STYLE:
- You're a knowledgeable, opinionated travel agent — not a generic chatbot
- Be concise (they're on a phone) but warm and confident
- Give specific recommendations, not wishy-washy lists
- When they ask "where should we eat?", give ONE great pick with why, not five options
- Use your deep Japan knowledge: restaurants, etiquette, transit tricks, hidden gems, seasonal tips
- Cherry blossom season context: peak bloom is usually early-mid April in Kyoto/Tokyo, \
  later in Takayama. Weather is mild but rainy days happen. Pack layers.

TRIP OVERVIEW (burned into your brain):
- Day 1: Travel from Cleveland via Detroit to Tokyo Haneda (Delta DL5392 CLE->DTW, DL275 DTW->HND)
- Day 2-4: Tokyo base (Sotetsu Fresa Inn Higashi-Shinjuku, 3 nights, confirmation #976558450) — city exploration + Hakone day trip
- Day 5-7: Takayama (3 nights) — ryokan, morning markets, Hida beef, Japanese Alps
- Day 8: Takayama -> Shirakawa-go -> Kyoto (transit day with sightseeing)
- Day 9-10: Kyoto (4 nights total) — temples, Gion, Arashiyama
- Day 11: Day trip to Hiroshima & Miyajima Island from Kyoto
- Day 12-13: Osaka (2 nights) — street food, Dotonbori, neon chaos
- Day 14: Departure — shinkansen Osaka to Haneda, fly home (United UA876 HND->SFO, UA1470 SFO->CLE)
- They have a 14-day JR Pass covering all shinkansen and JR trains

IMAGE PROCESSING — THIS IS CRITICAL:
When users share images, you are an expert document reader. Think step by step:
1. IDENTIFY what the image is: hotel confirmation, flight booking, restaurant receipt, \
   train ticket, screenshot of a website, map, menu, etc.
2. EXTRACT every useful detail: names, dates, times, confirmation numbers, prices, \
   addresses, check-in/out times, flight numbers, seat assignments, anything useful
3. MATCH to the trip: Use dates, city names, and hotel names to figure out which part \
   of the trip this belongs to. The ACCOMMODATIONS section in your context lists every \
   hotel option by city and date range — find the match.
   - If a hotel confirmation matches an existing option: UPDATE it (booking_status, \
     confirmation_number, check_in_info, check_out_info, price, address, notes)
   - If it's a new hotel not in the list: ADD it to the right location
   - If it's a flight: UPDATE the matching flight record
   - If it's an activity/restaurant/ticket: ADD it to the right day
4. ALWAYS use tools to save the data — never just describe what you see
5. After updating, give a clear summary: "Updated [hotel name] for [city] — \
   set to Booked, confirmation #ABC123, check-in 3pm"

COMMON SENSE RULES:
- DAYS vs NIGHTS — get this right: a stay from April 6 check-in to April 9 check-out is \
  3 NIGHTS (sleeping 3 times: 6th, 7th, 8th) but spans 4 CALENDAR DAYS. Nights = checkout \
  date minus check-in date. Two calendar days (e.g. Apr 6-7) = 1 night, not 2. Always count \
  nights as the number of sleeps, which equals check-out date minus check-in date.
- If they share a booking.com confirmation for a Kyoto hotel checking in April 12, \
  that's obviously the "Kyoto (4 nights)" location. Match it.
- If they share a restaurant reservation for April 13, add it to Day 9 (Kyoto Day 1)
- If prices are in JPY, convert to USD at ~150 JPY/USD for the price fields
- If a screenshot shows a hotel they already have as an option, update it — don't add a duplicate
- When updating accommodation prices from a booking, set BOTH price_low and price_high \
  to the actual booked price (it's no longer a range, it's confirmed)
- Always set booking_status to "booked" when processing a confirmation
- Extract check-in and check-out times whenever visible

TRAVEL AGENT MINDSET — ALWAYS ACTIVE:
You are not a passive database. You are a proactive travel agent who THINKS about the trip holistically. \
Every time you make a change, think through the ripple effects:
- CONFLICT DETECTION: Before adding/moving an activity, check what else is scheduled that day and \
  time slot. Flag overlaps, impossible timelines (e.g., activity in Kyoto at 2 PM + activity in \
  Osaka at 2:30 PM), and days that are overpacked. Use the flag_conflict tool when you spot issues.
- TRANSPORTATION GAPS: Every time activities span different areas, think about how the user gets \
  between them. If you add an activity in Arashiyama and the next one is in Fushimi, note the \
  ~45 min transit. If getting_there is empty on a non-obvious activity, fill it in.
- SCHEDULE REALISM: A day with 10 activities is not realistic. Flag when days are overloaded. \
  Account for travel fatigue (especially Day 1-2 after a 14-hour flight), meal times, rest, \
  and the fact that temples/shrines close by 5 PM. Buffer days exist for a reason.
- CONSISTENCY: When you update one thing, check if related items need updating too. If you change \
  a hotel check-in date, does the previous hotel's checkout still line up? If you move an activity \
  to a different day, does the getting_there still make sense? If you book a restaurant, should it \
  replace the generic "dinner out" activity?
- GAPS & DEAD TIME: If a day has morning activities and evening activities but nothing in the \
  afternoon, mention it. Either suggest something or confirm that's intentional rest time.
- TRANSPORT AWARENESS: Know which transit is JR Pass covered vs. not. Remind them about the \
  Hakone Free Pass (not JR), private railways in Kyoto (not JR), and that local buses/subways \
  need cash or IC card (Suica). When suggesting activities, factor in whether they need to \
  backtrack or can flow naturally through an area.

SCHEDULE INTELLIGENCE:
- When they ask to add/move/change activities, think about logistics:
  - What's nearby? Group activities by area to minimize transit
  - What time does it open/close? Don't suggest shrines at midnight
  - How long does it take? Temple visits ~1hr, museums ~2hr, markets ~1.5hr
  - Transit time between areas: factor in 20-30 min for most Kyoto moves, \
    45-60 min across Tokyo
- If they share a "things to do" screenshot or article, extract the activities \
  and suggest which days they'd fit best based on location and existing schedule
- When they ask to modify the schedule, use update_activity and update_day_notes tools. \
  Don't just describe changes — make them.
- After ANY schedule change, briefly scan for conflicts with adjacent activities and mention \
  anything that looks off. Don't wait to be asked.

ACCOMMODATION INTELLIGENCE:
- When comparing options, consider: location (walkability to attractions), \
  price per night, amenities (onsen, breakfast, etc.), reviews/ratings
- For ryokans: mention if dinner is included (important for Takayama)
- Flag if a check-in date doesn't match the location's dates
- CRITICAL DATE MAPPING (memorize this):
  Day 1 = Apr 5 (Travel), Day 2 = Apr 6 (Tokyo), Day 3 = Apr 7 (Tokyo), Day 4 = Apr 8 (Tokyo/Hakone), \
  Day 5 = Apr 9 (Takayama), Day 6 = Apr 10 (Takayama), Day 7 = Apr 11 (Takayama), \
  Day 8 = Apr 12 (Shirakawa-go → Kyoto), Day 9 = Apr 13 (Kyoto), Day 10 = Apr 14 (Kyoto), \
  Day 11 = Apr 15 (Hiroshima day trip), Day 12 = Apr 16 (Osaka), Day 13 = Apr 17 (Osaka), \
  Day 14 = Apr 18 (Departure)
- ACCOMMODATION STAYS (check-in → check-out, nights):
  Tokyo: Apr 6-9 (3 nights), Takayama: Apr 9-12 (3 nights), \
  Kyoto: Apr 12-16 (4 nights, split across 2 properties), Osaka: Apr 16-18 (2 nights)
- Some cities have MULTIPLE AccommodationLocation records (e.g. Takayama has "Ryokan" + "Budget"). \
  When searching for a city's accommodation, check ALL locations whose name contains that city.
- Transition days: checkout from one city and check-in to the next often happen on the SAME date \
  (e.g. Apr 9 = Tokyo checkout + Takayama check-in). This is normal, not a conflict.

TOOLS AVAILABLE:
- web_search: Search the internet for current info (prices, hours, reviews, directions, etc.)
- update_flight: Update flight booking status, confirmation, times
- update_accommodation: Update hotel booking status, confirmation, address, prices, check-in/out
- add_accommodation_option: Add new hotel option to a location
- select_accommodation: Pick the chosen hotel for a location
- eliminate_accommodation: Rule out a bad option
- delete_accommodation: Remove an option entirely
- update_activity: Modify existing activity OR add new one (create_new=true)
- eliminate_activity: Rule out an activity (toggle)
- toggle_activity: Mark activity done/not done
- delete_activity: Remove activity from a day
- update_day_notes: Set notes for a day
- add_checklist_item: Add to-do item (pre_trip, packing, on_trip)
- toggle_checklist_item: Check/uncheck a to-do
- delete_checklist_item: Remove a to-do
- update_budget: Record actual costs
- flag_conflict: Alert about scheduling issues

WEB SEARCH: You can search the web to find current information. Use this for:
- Looking up restaurant recommendations, opening hours, menus
- Checking transit routes and schedules
- Finding current prices, availability, reviews
- Researching attractions, events, or cultural info
- Any question where current/real-time info would help

WHEN UNCERTAIN: Ask a short, specific clarifying question rather than guessing wrong. \
"Which Kyoto hotel — the 3-night stay or the machiya?" is better than updating the wrong one."""

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
                "check_in_info": {"type": "string", "description": "Check-in time/info (e.g. '3:00 PM')"},
                "check_out_info": {"type": "string", "description": "Check-out time/info (e.g. '11:00 AM')"},
                "price_low": {"type": "number", "description": "Per-night price low end in USD"},
                "price_high": {"type": "number", "description": "Per-night price high end in USD"},
            },
            "required": ["name"]
        }
    },
    {
        "name": "add_accommodation_option",
        "description": "Add a new hotel/accommodation option to a location. Match location by city name or check-in date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "location_name": {"type": "string", "description": "City/area name to match (e.g. 'Tokyo', 'Kyoto', 'Osaka')"},
                "name": {"type": "string", "description": "Hotel/accommodation name"},
                "property_type": {"type": "string", "description": "e.g. 'Hotel', 'Ryokan', 'Hostel', 'Capsule hotel', 'Airbnb'"},
                "price_low": {"type": "number", "description": "Low end per-night price in USD"},
                "price_high": {"type": "number", "description": "High end per-night price in USD"},
                "address": {"type": "string"},
                "booking_url": {"type": "string", "description": "URL to the booking page"},
                "alt_booking_url": {"type": "string", "description": "Alternative booking URL"},
                "standout": {"type": "string", "description": "What makes this place special"},
                "breakfast_included": {"type": "boolean"},
                "has_onsen": {"type": "boolean"},
                "user_notes": {"type": "string"},
            },
            "required": ["location_name", "name"]
        }
    },
    {
        "name": "select_accommodation",
        "description": "Select an accommodation option as the chosen one for a location, or deselect it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Hotel name (partial match ok)"},
                "select": {"type": "boolean", "description": "True to select, false to deselect", "default": True},
            },
            "required": ["name"]
        }
    },
    {
        "name": "eliminate_accommodation",
        "description": "Mark an accommodation option as eliminated (removed from consideration) or restore it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Hotel name (partial match ok)"},
                "eliminate": {"type": "boolean", "description": "True to eliminate, false to restore", "default": True},
            },
            "required": ["name"]
        }
    },
    {
        "name": "update_activity",
        "description": "Update an existing activity or add a new one. Supports full activity details.",
        "input_schema": {
            "type": "object",
            "properties": {
                "day_number": {"type": "integer", "description": "Day number (1-15)"},
                "title": {"type": "string", "description": "Activity title (for matching existing or creating new)"},
                "time_slot": {"type": "string", "enum": ["morning", "afternoon", "evening", "night"]},
                "start_time": {"type": "string"},
                "cost_per_person": {"type": "number"},
                "cost_note": {"type": "string", "description": "Cost description e.g. '¥500 entry'"},
                "address": {"type": "string"},
                "description": {"type": "string", "description": "Activity description"},
                "url": {"type": "string", "description": "Website or booking URL"},
                "notes": {"type": "string"},
                "is_optional": {"type": "boolean"},
                "create_new": {"type": "boolean", "description": "True to add a new activity, false to update existing"},
            },
            "required": ["day_number", "title"]
        }
    },
    {
        "name": "toggle_activity",
        "description": "Mark an activity as completed or not completed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "day_number": {"type": "integer", "description": "Day number (1-15)"},
                "title": {"type": "string", "description": "Activity title (partial match ok)"},
                "completed": {"type": "boolean", "description": "True to mark done, false to unmark"},
            },
            "required": ["day_number", "title", "completed"]
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
    },
    {
        "name": "toggle_checklist_item",
        "description": "Mark a checklist item as completed or not completed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Checklist item title (partial match ok)"},
                "completed": {"type": "boolean", "description": "True to mark done, false to unmark"},
            },
            "required": ["title", "completed"]
        }
    },
    {
        "name": "delete_checklist_item",
        "description": "Delete a checklist item permanently.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Checklist item title (partial match ok)"},
            },
            "required": ["title"]
        }
    },
    {
        "name": "delete_accommodation",
        "description": "Permanently delete an accommodation option.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Hotel name (partial match ok)"},
            },
            "required": ["name"]
        }
    },
    {
        "name": "eliminate_activity",
        "description": "Rule out (or restore) an activity. Toggles eliminated status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "day_number": {"type": "integer", "description": "Day number (1-15)"},
                "title": {"type": "string", "description": "Activity title (partial match ok)"},
            },
            "required": ["day_number", "title"]
        }
    },
    {
        "name": "delete_activity",
        "description": "Permanently delete an activity from a day.",
        "input_schema": {
            "type": "object",
            "properties": {
                "day_number": {"type": "integer", "description": "Day number (1-15)"},
                "title": {"type": "string", "description": "Activity title (partial match ok)"},
            },
            "required": ["day_number", "title"]
        }
    },
    {
        "name": "update_day_notes",
        "description": "Update or set the notes for a specific day.",
        "input_schema": {
            "type": "object",
            "properties": {
                "day_number": {"type": "integer", "description": "Day number (1-15)"},
                "notes": {"type": "string", "description": "The notes text (replaces existing)"},
            },
            "required": ["day_number", "notes"]
        }
    }
]

# Server-side tools — Anthropic executes these automatically, no client-side handler needed
SERVER_TOOLS = [
    {"type": "web_search_20250305", "name": "web_search"},
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
            for field in ['booking_status', 'confirmation_number', 'address', 'user_notes',
                          'check_in_info', 'check_out_info']:
                if tool_input.get(field):
                    setattr(option, field, tool_input[field])
            # Handle price updates
            if tool_input.get('price_low') is not None:
                option.price_low = tool_input['price_low']
            if tool_input.get('price_high') is not None:
                option.price_high = tool_input['price_high']
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
            return {"success": True, "message": f"Added '{option.name}' as option #{next_rank} for {accom_loc.location_name} "
                    f"({accom_loc.check_in_date.strftime('%b %d')}-{accom_loc.check_out_date.strftime('%b %d')})"}

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

            if tool_input.get('create_new', False):
                max_order = max([a.sort_order for a in day.activities] or [0])
                activity = Activity(
                    day_id=day.id,
                    title=tool_input['title'],
                    time_slot=tool_input.get('time_slot'),
                    start_time=tool_input.get('start_time'),
                    cost_per_person=tool_input.get('cost_per_person'),
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
                for field in ['address', 'notes', 'start_time', 'cost_per_person',
                              'cost_note', 'description', 'url', 'time_slot', 'is_optional']:
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


@chat_bp.route('/chat')
def chat_view():
    return render_template('chat.html')


@chat_bp.route('/api/chat/send', methods=['POST'])
def send_message():
    api_key = current_app.config.get('ANTHROPIC_API_KEY', '')
    if not api_key or api_key == 'sk-ant-your-key-here':
        return jsonify({'error': 'API key not configured. '
                        'Set ANTHROPIC_API_KEY in .env'}), 400

    # Handle both JSON and multipart form data
    images = []  # list of {data, media_type, filename}
    image_filename = None  # first image filename for DB record
    session_history_raw = []

    MODEL_MAP = {
        'fast': 'claude-haiku-4-5-20251001',
        'balanced': 'claude-sonnet-4-6',
        'deep': 'claude-opus-4-6',
    }

    if request.content_type and 'multipart/form-data' in request.content_type:
        user_message = request.form.get('message', '').strip()
        model_choice = request.form.get('model', 'balanced')
        session_history_raw = request.form.get('session_history', '[]')

        # Support multiple images
        image_files = request.files.getlist('images') or []
        # Also check legacy single 'image' field
        single = request.files.get('image')
        if single and single.filename:
            image_files.append(single)

        from blueprints.uploads import save_chat_image
        for image_file in image_files:
            if not image_file or not image_file.filename:
                continue
            fname, original_path = save_chat_image(image_file)
            if not fname or not original_path:
                continue
            if not image_filename:
                image_filename = fname  # store first for DB
            with open(original_path, 'rb') as f:
                raw = f.read()
            img_media_type = None
            if len(raw) > 4 * 1024 * 1024:
                try:
                    from PIL import Image
                    import io
                    img = Image.open(io.BytesIO(raw))
                    img.thumbnail((2048, 2048))
                    buf = io.BytesIO()
                    img.save(buf, format='JPEG', quality=85)
                    raw = buf.getvalue()
                    img_media_type = 'image/jpeg'
                except Exception:
                    pass
            encoded = base64.b64encode(raw).decode('utf-8')
            ext = fname.rsplit('.', 1)[-1].lower()
            if not img_media_type:
                img_media_type = {
                    'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                    'png': 'image/png', 'gif': 'image/gif',
                    'webp': 'image/webp',
                }.get(ext, 'image/jpeg')
            images.append({'data': encoded, 'media_type': img_media_type, 'filename': fname})
    else:
        data = request.get_json() or {}
        user_message = data.get('message', '').strip()
        model_choice = data.get('model', 'balanced')
        session_history_raw = data.get('session_history', [])

    if not user_message and not images:
        return jsonify({'error': 'Empty message'}), 400

    if not user_message and images:
        user_message = "Please analyze this travel document and extract any useful information. Match it to existing accommodations, flights, or activities if possible."

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

    # Build messages from client-sent session history
    messages = []
    if isinstance(session_history_raw, str):
        try:
            session_history_raw = json.loads(session_history_raw)
        except (json.JSONDecodeError, TypeError):
            session_history_raw = []

    for m in (session_history_raw or []):
        if isinstance(m, dict) and m.get('role') and m.get('content'):
            messages.append({'role': m['role'], 'content': m['content']})

    # Build current user message with optional images
    user_content = []
    for img in images:
        user_content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": img['media_type'],
                "data": img['data'],
            }
        })
    user_content.append({"type": "text", "text": user_message})
    messages.append({"role": "user", "content": user_content})

    model_id = MODEL_MAP.get(model_choice, 'claude-sonnet-4-6')
    # Token budgets per tier
    if model_choice == 'deep':
        max_tokens = 16384
    elif model_choice == 'balanced':
        max_tokens = 8192
    else:
        max_tokens = 2048
    app = current_app._get_current_object()

    def generate():
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            system = SYSTEM_PROMPT + '\n\n' + context
            full_response = ''

            # Configure thinking for balanced/deep modes (adaptive for 4.6 models)
            use_thinking = model_choice in ('balanced', 'deep')

            # Combine user-defined and server-side tools
            all_tools = TOOLS + SERVER_TOOLS

            # First call: non-streaming to detect tool use
            api_kwargs = dict(
                model=model_id,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                tools=all_tools,
            )
            if use_thinking:
                api_kwargs['thinking'] = {"type": "adaptive"}

            response = client.messages.create(**api_kwargs)

            # Process response blocks — extract text and execute client-side tools
            # Server-side tools (web_search) are executed by Anthropic automatically;
            # their results appear inline and don't need client-side handling.
            tool_results = []
            text_parts = []
            has_server_tool = False
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
                elif block.type == 'server_tool_use':
                    has_server_tool = True
                    yield f"data: {json.dumps({'processing': f'Searching the web...'})}\n\n"
                elif block.type == 'text':
                    text_parts.append(block.text)
                elif block.type == 'thinking':
                    yield f"data: {json.dumps({'processing': 'Thinking...'})}\n\n"

            if tool_results:
                # Follow-up call: stream the final response after client tool execution
                assistant_content = []
                for block in response.content:
                    if block.type == 'text':
                        assistant_content.append({"type": "text", "text": block.text})
                    elif block.type == 'tool_use':
                        assistant_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })
                    elif block.type == 'thinking':
                        assistant_content.append({
                            "type": "thinking",
                            "thinking": block.thinking,
                            "signature": block.signature,
                        })
                messages.append({"role": "assistant", "content": assistant_content})
                messages.append({"role": "user", "content": tool_results})
                followup_kwargs = dict(
                    model=model_id,
                    max_tokens=max_tokens,
                    system=system,
                    messages=messages,
                    tools=all_tools,
                )
                if use_thinking:
                    followup_kwargs['thinking'] = {"type": "adaptive"}
                with client.messages.stream(**followup_kwargs) as stream:
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
    from models import Trip
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
                         f"{loc.check_out_date.strftime('%b %d')}, {loc.num_nights} nights):")
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
