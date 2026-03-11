"""Activity mutation service — single canonical write path.

Every mutation follows: validate → normalize → write → cascade → emit.
Both UI routes and AI chat tools call these functions.
"""
from datetime import datetime
from models import db, Activity, Day
from guardrails import validate_time_slot, validate_non_negative, validate_category
from extensions import socketio


def _emit_activity_updated(activity):
    """Emit Socket.IO event for any activity change."""
    socketio.emit('activity_updated', {
        'id': activity.id,
        'day_id': activity.day_id,
    })


def _strip_or_none(value):
    """Strip whitespace from string, return None if empty."""
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        return v if v else None
    return value


def toggle(activity_id):
    """Toggle activity completion status."""
    activity = Activity.query.get_or_404(activity_id)
    activity.is_completed = not activity.is_completed
    activity.completed_at = datetime.utcnow() if activity.is_completed else None
    db.session.commit()

    socketio.emit('activity_toggled', {
        'id': activity.id,
        'is_completed': activity.is_completed,
        'day_id': activity.day_id,
    })
    return activity


def set_completed(activity_id, completed):
    """Set activity completion to an explicit state (not toggle).

    Used by chat tools where the AI specifies the desired end state.
    """
    activity = Activity.query.get_or_404(activity_id)
    activity.is_completed = completed
    activity.completed_at = datetime.utcnow() if completed else None
    db.session.commit()

    socketio.emit('activity_toggled', {
        'id': activity.id,
        'is_completed': activity.is_completed,
        'day_id': activity.day_id,
    })
    return activity


def add(day_id, fields):
    """Add a new activity to a day.

    Args:
        day_id: Day PK
        fields: dict with title (required), plus optional time_slot,
                start_time, cost_per_person, cost_note, address, description,
                url, notes, is_optional, category, maps_url, getting_there,
                book_ahead, book_ahead_note, why
    """
    day = Day.query.get_or_404(day_id)
    validated_ts = validate_time_slot(fields.get('time_slot'))
    validated_cost = validate_non_negative(fields.get('cost_per_person'), 'cost_per_person')
    validated_cat = validate_category(fields.get('category'))

    max_order = max([a.sort_order for a in day.activities] or [0])
    activity = Activity(
        day_id=day.id,
        title=_strip_or_none(fields['title']),
        time_slot=validated_ts,
        start_time=_strip_or_none(fields.get('start_time')),
        cost_per_person=validated_cost,
        cost_note=_strip_or_none(fields.get('cost_note')),
        address=_strip_or_none(fields.get('address')),
        description=_strip_or_none(fields.get('description')),
        url=_strip_or_none(fields.get('url')),
        maps_url=_strip_or_none(fields.get('maps_url')),
        notes=_strip_or_none(fields.get('notes')),
        getting_there=_strip_or_none(fields.get('getting_there')),
        why=_strip_or_none(fields.get('why')),
        category=validated_cat,
        book_ahead=bool(fields.get('book_ahead', False)),
        book_ahead_note=_strip_or_none(fields.get('book_ahead_note')),
        is_optional=fields.get('is_optional', False),
        sort_order=max_order + 1,
    )
    db.session.add(activity)
    db.session.commit()

    _emit_activity_updated(activity)
    return activity


def update(activity_id, fields):
    """Update an existing activity's fields.

    Validates and normalizes all inputs before writing.
    """
    activity = Activity.query.get_or_404(activity_id)

    # Validated fields
    if 'time_slot' in fields and fields['time_slot'] is not None:
        activity.time_slot = validate_time_slot(fields['time_slot'])
    if 'cost_per_person' in fields and fields['cost_per_person'] is not None:
        activity.cost_per_person = validate_non_negative(fields['cost_per_person'], 'cost_per_person')
    if 'category' in fields:
        activity.category = validate_category(fields['category'])

    # String fields — strip whitespace, normalize empties to None
    for field in ('title', 'address', 'notes', 'start_time', 'cost_note',
                  'description', 'url', 'maps_url', 'getting_there',
                  'book_ahead_note', 'why'):
        if field in fields:
            setattr(activity, field, _strip_or_none(fields[field]))

    # Boolean fields
    for field in ('is_optional', 'book_ahead', 'jr_pass_covered'):
        if field in fields and fields[field] is not None:
            setattr(activity, field, bool(fields[field]))

    db.session.commit()

    _emit_activity_updated(activity)
    return activity


def eliminate(activity_id):
    """Toggle elimination status."""
    activity = Activity.query.get_or_404(activity_id)
    activity.is_eliminated = not activity.is_eliminated
    # Eliminating a confirmed activity clears confirmation
    if activity.is_eliminated and activity.is_confirmed:
        activity.is_confirmed = False
    db.session.commit()

    _emit_activity_updated(activity)
    return activity


def delete(activity_id):
    """Delete an activity and re-rank remaining activities."""
    activity = Activity.query.get_or_404(activity_id)
    title = activity.title
    day_id = activity.day_id
    db.session.delete(activity)
    db.session.commit()

    # Re-rank remaining activities to close gaps
    remaining = Activity.query.filter_by(day_id=day_id).order_by(
        Activity.sort_order).all()
    for i, act in enumerate(remaining, 1):
        act.sort_order = i
    if remaining:
        db.session.commit()

    socketio.emit('activity_updated', {'day_id': day_id})
    return title, day_id


def update_notes(activity_id, notes):
    """Update activity notes."""
    activity = Activity.query.get_or_404(activity_id)
    activity.notes = _strip_or_none(notes)
    db.session.commit()

    socketio.emit('notes_updated', {
        'type': 'activity',
        'id': activity.id,
        'notes': activity.notes,
    })
    return activity


def confirm(activity_id):
    """Toggle activity confirmation. Un-eliminates if confirming."""
    activity = Activity.query.get_or_404(activity_id)
    activity.is_confirmed = not activity.is_confirmed
    if activity.is_confirmed and activity.is_eliminated:
        activity.is_eliminated = False
    db.session.commit()

    _emit_activity_updated(activity)
    return activity


def unflag_bookahead(activity_id):
    """Clear book-ahead flag and note."""
    activity = Activity.query.get_or_404(activity_id)
    activity.book_ahead = False
    activity.book_ahead_note = None
    db.session.commit()

    _emit_activity_updated(activity)
    return activity


def update_why(activity_id, why):
    """Update activity reasoning/comparison notes."""
    activity = Activity.query.get_or_404(activity_id)
    activity.why = _strip_or_none(why)
    db.session.commit()

    _emit_activity_updated(activity)
    return activity


def update_maps_url(activity_id, maps_url):
    """Update activity Google Maps URL."""
    activity = Activity.query.get_or_404(activity_id)
    activity.maps_url = _strip_or_none(maps_url)
    db.session.commit()

    _emit_activity_updated(activity)
    return activity


def update_day_notes(day_id, notes):
    """Update day-level notes."""
    day = Day.query.get_or_404(day_id)
    day.notes = _strip_or_none(notes)
    db.session.commit()

    socketio.emit('notes_updated', {
        'type': 'day',
        'id': day.id,
        'notes': day.notes,
    })
    return day
