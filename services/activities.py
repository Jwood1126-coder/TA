"""Activity mutation service — single canonical write path."""
from datetime import datetime
from models import db, Activity, Day
from guardrails import validate_time_slot, validate_non_negative
from extensions import socketio


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
                url, notes, is_optional
    """
    day = Day.query.get_or_404(day_id)
    validated_ts = validate_time_slot(fields.get('time_slot'))
    validated_cost = validate_non_negative(fields.get('cost_per_person'), 'cost_per_person')

    max_order = max([a.sort_order for a in day.activities] or [0])
    activity = Activity(
        day_id=day.id,
        title=fields['title'],
        time_slot=validated_ts,
        start_time=fields.get('start_time'),
        cost_per_person=validated_cost,
        cost_note=fields.get('cost_note'),
        address=fields.get('address'),
        description=fields.get('description'),
        url=fields.get('url'),
        notes=fields.get('notes'),
        is_optional=fields.get('is_optional', False),
        sort_order=max_order + 1,
    )
    db.session.add(activity)
    db.session.commit()

    socketio.emit('activity_added', {'day_id': day.id})
    return activity


def update(activity_id, fields):
    """Update an existing activity's fields."""
    activity = Activity.query.get_or_404(activity_id)

    if 'time_slot' in fields and fields['time_slot'] is not None:
        activity.time_slot = validate_time_slot(fields['time_slot'])
    if 'cost_per_person' in fields and fields['cost_per_person'] is not None:
        activity.cost_per_person = validate_non_negative(fields['cost_per_person'], 'cost_per_person')

    for field in ('address', 'notes', 'start_time', 'cost_note',
                  'description', 'url', 'is_optional', 'title'):
        if field in fields and fields[field] is not None:
            setattr(activity, field, fields[field])
    db.session.commit()
    return activity


def eliminate(activity_id):
    """Toggle elimination status."""
    activity = Activity.query.get_or_404(activity_id)
    activity.is_eliminated = not activity.is_eliminated
    db.session.commit()
    return activity


def delete(activity_id):
    """Delete an activity."""
    activity = Activity.query.get_or_404(activity_id)
    title = activity.title
    day_id = activity.day_id
    db.session.delete(activity)
    db.session.commit()
    return title, day_id


def update_notes(activity_id, notes):
    """Update activity notes."""
    activity = Activity.query.get_or_404(activity_id)
    activity.notes = notes
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
    return activity


def unflag_bookahead(activity_id):
    """Clear book-ahead flag and note."""
    activity = Activity.query.get_or_404(activity_id)
    activity.book_ahead = False
    activity.book_ahead_note = None
    db.session.commit()
    return activity


def update_why(activity_id, why):
    """Update activity reasoning/comparison notes."""
    activity = Activity.query.get_or_404(activity_id)
    activity.why = why
    db.session.commit()
    return activity


def update_maps_url(activity_id, maps_url):
    """Update activity Google Maps URL."""
    activity = Activity.query.get_or_404(activity_id)
    activity.maps_url = maps_url or None
    db.session.commit()
    return activity


def update_day_notes(day_id, notes):
    """Update day-level notes."""
    day = Day.query.get_or_404(day_id)
    day.notes = notes
    db.session.commit()

    socketio.emit('notes_updated', {
        'type': 'day',
        'id': day.id,
        'notes': day.notes,
    })
    return day
