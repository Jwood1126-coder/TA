import re
from flask import Blueprint, render_template, request, jsonify
from models import db, Location, Day, Activity
from guardrails import validate_time_slot, validate_non_negative

activities_bp = Blueprint('activities', __name__)

# Patterns that indicate logistics, not sightseeing/dining/experiences
_LOGISTICS_PATTERNS = re.compile(
    r'(?i)'
    r'^check\s*(in|out|into)\b'           # check-in/out
    r'|^activate\b'                        # activate passes/SIM
    r'|^pick\s+up\b'                       # pick up cards/WiFi
    r'|^arrange\b'                         # arrange luggage forwarding
    r'|^buy\s.*(pass|ticket)\b'            # buy passes
    r'|\bshinkansen\b'                     # bullet train transit
    r'|\bexpress\b.*→'                     # limited express transit
    r'|→'                                  # any A → B route
    r'|\bbus:'                             # bus routes
    r'|\bferry\b'                          # ferry transit
    r'|\bline\s+to\b'                      # train line to X
    r'|\blight\s+rail\b'                   # light rail
    r'|\bnarita\s+express\b'              # airport express
    r'|\bkeihan\s+line\b'                 # specific train line
    r'|^JR\s+(Special|Rapid|Limited|Hida)'  # JR transit routes
)

# Hotel-specific amenities tied to a particular accommodation
_HOTEL_AMENITY_PATTERNS = re.compile(
    r'(?i)'
    r'\bat\s+(dormy\s+inn|ryokan|hostel|toyoko|hotel)\b'
    r'|ryokan\s+breakfast'
    r'|kaiseki\s+dinner\s+at\s+ryokan'
)

CATEGORY_LABELS = {
    'temple': 'Temples & Shrines',
    'food': 'Food & Dining',
    'nightlife': 'Nightlife',
    'shopping': 'Shopping',
    'nature': 'Nature & Outdoors',
    'culture': 'Culture & Museums',
    'transit': 'Transit',
}

CATEGORY_ORDER = ['temple', 'culture', 'food', 'nature', 'nightlife', 'shopping']


def _is_browseable_activity(activity):
    """Return True if activity is a real experience, not logistics."""
    if activity.category == 'transit':
        return False
    title = activity.title
    if _LOGISTICS_PATTERNS.search(title):
        return False
    if _HOTEL_AMENITY_PATTERNS.search(title):
        return False
    return True


@activities_bp.route('/activities')
def activities_view():
    from models import Trip
    from datetime import date as dt_date
    locations = Location.query.order_by(Location.sort_order).all()
    days = Day.query.order_by(Day.day_number).all()
    trip = Trip.query.first()
    today = dt_date.today()
    current_day = None
    trip_active = False
    if trip and trip.start_date <= today <= trip.end_date:
        trip_active = True
        current_day = Day.query.filter(Day.date == today).first()

    location_activities = []
    all_categories = set()
    for loc in locations:
        loc_days = [d for d in days if d.location_id == loc.id]
        # Group activities by day within this location
        day_groups = []
        substitutes = []
        total_count = 0
        for d in loc_days:
            day_activities = []
            for a in d.activities:
                if not a.is_substitute and _is_browseable_activity(a):
                    day_activities.append({'activity': a, 'day': d})
                    if a.category:
                        all_categories.add(a.category)
                elif a.is_substitute and _is_browseable_activity(a):
                    substitutes.append({'activity': a, 'day': d})
                    if a.category:
                        all_categories.add(a.category)
            if day_activities:
                day_groups.append({'day': d, 'activities': day_activities})
                total_count += len(day_activities)
        if day_groups or substitutes:
            location_activities.append({
                'location': loc,
                'day_groups': day_groups,
                'substitutes': substitutes,
                'total_count': total_count,
            })

    # Sort categories in display order
    sorted_categories = [c for c in CATEGORY_ORDER if c in all_categories]

    return render_template('activities.html',
                           location_activities=location_activities,
                           all_days=days,
                           categories=sorted_categories,
                           category_labels=CATEGORY_LABELS,
                           current_day=current_day,
                           trip_active=trip_active)


@activities_bp.route('/api/activities/add', methods=['POST'])
def add_activity():
    data = request.get_json()
    day_id = data.get('day_id')
    title = (data.get('title') or '').strip()
    if not day_id or not title:
        return jsonify({'ok': False, 'error': 'Day and title are required'}), 400

    try:
        time_slot = validate_time_slot(data.get('time_slot'))
        cost = validate_non_negative(data.get('cost_per_person'), 'cost_per_person')
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400

    day = Day.query.get_or_404(int(day_id))
    max_order = max([a.sort_order for a in day.activities] or [0])

    activity = Activity(
        day_id=day.id,
        title=title,
        description=(data.get('description') or '').strip() or None,
        time_slot=time_slot,
        start_time=(data.get('start_time') or '').strip() or None,
        cost_per_person=cost,
        cost_note=(data.get('cost_note') or '').strip() or None,
        address=(data.get('address') or '').strip() or None,
        url=(data.get('url') or '').strip() or None,
        category=data.get('category') or None,
        is_optional=bool(data.get('is_optional')),
        sort_order=max_order + 1,
    )
    db.session.add(activity)
    db.session.commit()

    from extensions import socketio
    socketio.emit('activity_added', {'day_id': day.id})

    return jsonify({'ok': True, 'id': activity.id})


@activities_bp.route('/api/activities/<int:activity_id>/delete', methods=['DELETE'])
def delete_activity(activity_id):
    activity = Activity.query.get_or_404(activity_id)
    db.session.delete(activity)
    db.session.commit()
    return jsonify({'ok': True})


@activities_bp.route('/api/activities/<int:activity_id>/eliminate', methods=['POST'])
def eliminate_activity(activity_id):
    activity = Activity.query.get_or_404(activity_id)
    activity.is_eliminated = not activity.is_eliminated
    db.session.commit()
    return jsonify({'ok': True, 'is_eliminated': activity.is_eliminated})


@activities_bp.route('/api/activities/<int:activity_id>/confirm', methods=['POST'])
def confirm_activity(activity_id):
    activity = Activity.query.get_or_404(activity_id)
    activity.is_confirmed = not activity.is_confirmed
    # If confirming, also un-eliminate
    if activity.is_confirmed and activity.is_eliminated:
        activity.is_eliminated = False
    db.session.commit()
    return jsonify({'ok': True, 'is_confirmed': activity.is_confirmed})


@activities_bp.route('/api/activities/<int:activity_id>/notes', methods=['PUT'])
def update_activity_notes(activity_id):
    activity = Activity.query.get_or_404(activity_id)
    data = request.get_json()
    activity.notes = data.get('notes', '').strip() or None
    db.session.commit()
    return jsonify({'ok': True})


@activities_bp.route('/api/activities/<int:activity_id>/unflag-bookahead', methods=['POST'])
def unflag_bookahead(activity_id):
    activity = Activity.query.get_or_404(activity_id)
    activity.book_ahead = False
    activity.book_ahead_note = None
    db.session.commit()
    return jsonify({'ok': True})


@activities_bp.route('/api/activities/<int:activity_id>/why', methods=['PUT'])
def update_activity_why(activity_id):
    activity = Activity.query.get_or_404(activity_id)
    data = request.get_json()
    activity.why = data.get('why', '').strip() or None
    db.session.commit()
    return jsonify({'ok': True})
