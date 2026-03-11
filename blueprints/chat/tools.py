"""Tool definitions for the AI travel agent."""

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
        "description": "Update an existing activity or add a new one. When creating, ALWAYS set category. Include address and url when known.",
        "input_schema": {
            "type": "object",
            "properties": {
                "day_number": {"type": "integer", "description": "Day number (1-14)"},
                "title": {"type": "string", "description": "Activity title (for matching existing or creating new)"},
                "time_slot": {"type": "string", "enum": ["morning", "afternoon", "evening", "night"]},
                "start_time": {"type": "string"},
                "cost_per_person": {"type": "number"},
                "cost_note": {"type": "string", "description": "Cost description e.g. '¥500 entry'"},
                "address": {"type": "string"},
                "maps_url": {"type": "string", "description": "Google Maps URL for the venue"},
                "description": {"type": "string", "description": "Activity description"},
                "url": {"type": "string", "description": "Official website or booking URL"},
                "notes": {"type": "string"},
                "getting_there": {"type": "string", "description": "Transit tip from previous activity"},
                "category": {"type": "string", "enum": ["temple", "food", "nightlife", "shopping", "nature", "culture", "transit", "logistics", "entertainment"]},
                "book_ahead": {"type": "boolean", "description": "True if advance tickets/reservation needed"},
                "book_ahead_note": {"type": "string", "description": "Where/how to book in advance"},
                "is_optional": {"type": "boolean"},
                "jr_pass_covered": {"type": "boolean"},
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
                "day_number": {"type": "integer", "description": "Day number (1-14)"},
                "notes": {"type": "string", "description": "The notes text (replaces existing)"},
            },
            "required": ["day_number", "notes"]
        }
    },
    {
        "name": "add_transport_route",
        "description": "Add a new transport route between cities/stations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "route_from": {"type": "string", "description": "Origin station/city"},
                "route_to": {"type": "string", "description": "Destination station/city"},
                "transport_type": {"type": "string", "description": "e.g. 'Shinkansen', 'Bus', 'JR Express', 'Limited Express', 'Local Train', 'Ferry', 'Subway'"},
                "day_number": {"type": "integer", "description": "Day number this route is used on"},
                "train_name": {"type": "string", "description": "Specific train service name e.g. 'Hida Limited Express'"},
                "duration": {"type": "string", "description": "Travel time e.g. '2h 20min'"},
                "jr_pass_covered": {"type": "boolean", "description": "True if JR Pass covers this route"},
                "cost_if_not_covered": {"type": "string", "description": "Cost if not covered by JR Pass e.g. '¥3,600'"},
                "notes": {"type": "string"},
                "url": {"type": "string", "description": "Operator website or timetable URL"},
            },
            "required": ["route_from", "route_to", "transport_type"]
        }
    },
    {
        "name": "update_transport_route",
        "description": "Update an existing transport route. Match by from/to station names.",
        "input_schema": {
            "type": "object",
            "properties": {
                "route_from": {"type": "string", "description": "Origin station to match (partial ok)"},
                "route_to": {"type": "string", "description": "Destination station to match (partial ok)"},
                "new_route_from": {"type": "string", "description": "New origin (if changing)"},
                "new_route_to": {"type": "string", "description": "New destination (if changing)"},
                "transport_type": {"type": "string"},
                "day_number": {"type": "integer"},
                "train_name": {"type": "string"},
                "duration": {"type": "string"},
                "jr_pass_covered": {"type": "boolean"},
                "cost_if_not_covered": {"type": "string"},
                "notes": {"type": "string"},
                "url": {"type": "string"},
            },
            "required": ["route_from", "route_to"]
        }
    },
    {
        "name": "delete_transport_route",
        "description": "Delete a transport route. Match by from/to station names.",
        "input_schema": {
            "type": "object",
            "properties": {
                "route_from": {"type": "string", "description": "Origin station to match (partial ok)"},
                "route_to": {"type": "string", "description": "Destination station to match (partial ok)"},
            },
            "required": ["route_from", "route_to"]
        }
    }
]

# Server-side tools — Anthropic executes these automatically, no client-side handler needed
SERVER_TOOLS = [
    {"type": "web_search_20250305", "name": "web_search"},
]
