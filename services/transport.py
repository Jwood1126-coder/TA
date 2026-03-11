"""Transport route mutation service — single canonical write path.

Every mutation follows: validate → normalize → write → cascade → emit.
Both UI routes and AI chat tools call these functions.
"""
from models import db, TransportRoute, Day
from guardrails import validate_transport_type
from extensions import socketio


def _strip_or_none(value):
    """Strip whitespace from string, return None if empty."""
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        return v if v else None
    return value


def _emit_transport_updated(day_id=None):
    """Emit Socket.IO event for any transport change."""
    socketio.emit('transport_updated', {'day_id': day_id})


def add(fields):
    """Add a new transport route.

    Args:
        fields: dict with route_from, route_to, transport_type (required),
                plus optional day_id, train_name, duration, jr_pass_covered,
                cost_if_not_covered, notes, maps_url, url
    """
    route_from = _strip_or_none(fields.get('route_from'))
    route_to = _strip_or_none(fields.get('route_to'))
    if not route_from or not route_to:
        raise ValueError("route_from and route_to are required")

    transport_type = validate_transport_type(fields.get('transport_type'))

    # Validate day_id if provided
    day_id = fields.get('day_id')
    if day_id is not None:
        day = Day.query.get(int(day_id))
        if not day:
            raise ValueError(f"Day {day_id} not found")
        day_id = day.id

    max_order = db.session.query(db.func.max(TransportRoute.sort_order)).scalar() or 0

    route = TransportRoute(
        route_from=route_from,
        route_to=route_to,
        transport_type=transport_type,
        train_name=_strip_or_none(fields.get('train_name')),
        duration=_strip_or_none(fields.get('duration')),
        jr_pass_covered=bool(fields.get('jr_pass_covered', False)),
        cost_if_not_covered=_strip_or_none(fields.get('cost_if_not_covered')),
        notes=_strip_or_none(fields.get('notes')),
        maps_url=_strip_or_none(fields.get('maps_url')),
        url=_strip_or_none(fields.get('url')),
        day_id=day_id,
        sort_order=max_order + 1,
    )
    db.session.add(route)
    db.session.commit()

    _emit_transport_updated(day_id)
    return route


def update(route_id, fields):
    """Update an existing transport route's fields."""
    route = TransportRoute.query.get_or_404(route_id)
    old_day_id = route.day_id

    if 'transport_type' in fields and fields['transport_type'] is not None:
        route.transport_type = validate_transport_type(fields['transport_type'])

    if 'day_id' in fields:
        new_day_id = fields['day_id']
        if new_day_id is not None:
            day = Day.query.get(int(new_day_id))
            if not day:
                raise ValueError(f"Day {new_day_id} not found")
            route.day_id = day.id
        else:
            route.day_id = None

    # String fields
    for field in ('route_from', 'route_to', 'train_name', 'duration',
                  'cost_if_not_covered', 'notes', 'maps_url', 'url'):
        if field in fields:
            value = _strip_or_none(fields[field])
            # route_from and route_to must not be empty
            if field in ('route_from', 'route_to') and not value:
                raise ValueError(f"{field} cannot be empty")
            setattr(route, field, value)

    # Boolean fields
    if 'jr_pass_covered' in fields and fields['jr_pass_covered'] is not None:
        route.jr_pass_covered = bool(fields['jr_pass_covered'])

    db.session.commit()

    _emit_transport_updated(route.day_id)
    # If day changed, also emit for the old day
    if old_day_id and old_day_id != route.day_id:
        _emit_transport_updated(old_day_id)

    return route


def delete(route_id):
    """Delete a transport route."""
    route = TransportRoute.query.get_or_404(route_id)
    day_id = route.day_id
    route_desc = f"{route.route_from} → {route.route_to}"
    db.session.delete(route)
    db.session.commit()

    _emit_transport_updated(day_id)
    return route_desc, day_id
