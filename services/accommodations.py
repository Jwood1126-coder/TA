"""Accommodation mutation service — single canonical write path.

Both UI routes and chat tools delegate here. Every mutation gets the same
validation, cascade, and Socket.IO emit regardless of entry point.
"""
from datetime import datetime
from models import db, AccommodationOption, AccommodationLocation, ChecklistItem
from guardrails import (validate_booking_status, validate_non_negative,
                        validate_document_status, check_accom_date_overlap)
from extensions import socketio


def select(option_id):
    """Mark an option as selected, deselecting all siblings."""
    option = AccommodationOption.query.get_or_404(option_id)
    AccommodationOption.query.filter_by(
        location_id=option.location_id).update({'is_selected': False})
    option.is_selected = True
    db.session.commit()

    socketio.emit('accommodation_updated', {
        'location_id': option.location_id,
        'selected_id': option.id,
    })
    return option


def eliminate(option_id, eliminate=None):
    """Toggle or set elimination status. Blocks if booked/confirmed."""
    option = AccommodationOption.query.get_or_404(option_id)
    target = eliminate if eliminate is not None else (not option.is_eliminated)
    if target and not option.is_eliminated and option.booking_status in ('booked', 'confirmed'):
        raise ValueError(f"Cannot eliminate — {option.name} is {option.booking_status}. "
                         f"Change booking status first.")
    option.is_eliminated = target
    if target and option.is_selected:
        option.is_selected = False
    db.session.commit()

    socketio.emit('accommodation_updated', {
        'location_id': option.location_id,
        'option_id': option.id,
        'is_eliminated': option.is_eliminated,
    })
    return option


def delete(option_id):
    """Delete an option and re-rank siblings."""
    option = AccommodationOption.query.get_or_404(option_id)
    loc_id = option.location_id
    name = option.name
    db.session.delete(option)
    _rerank(loc_id)
    db.session.commit()

    socketio.emit('accommodation_updated', {'location_id': loc_id})
    return name, loc_id


def update_status(option_id, fields):
    """Update booking status and related fields. Cascades to checklist.

    Args:
        option_id: AccommodationOption PK
        fields: dict with any of: booking_status, confirmation_number,
                user_notes, booking_url, address, maps_url,
                check_in_info, check_out_info, price_low, price_high
    """
    option = AccommodationOption.query.get_or_404(option_id)

    # Validate booking status + document-first rule
    new_status = fields.get('booking_status')
    if new_status is not None:
        new_status = validate_booking_status(new_status)
        validate_document_status(new_status, option.document_id,
                                 f"accommodation '{option.name}'")
        option.booking_status = new_status

    # Apply simple string fields
    for field in ('confirmation_number', 'user_notes', 'check_in_info', 'check_out_info'):
        if field in fields:
            setattr(option, field, fields[field])
    # Nullable URL fields
    for field in ('booking_url', 'address', 'maps_url'):
        if field in fields:
            setattr(option, field, fields[field] or None)

    # Validate and set prices
    if 'price_low' in fields:
        option.price_low = validate_non_negative(fields['price_low'], 'price_low')
    if 'price_high' in fields:
        option.price_high = validate_non_negative(fields['price_high'], 'price_high')

    # Recalculate derived totals
    _recalc_totals(option)

    # Cascade: sync linked checklist item
    if new_status is not None:
        _sync_checklist_status(option)

    db.session.commit()

    socketio.emit('accommodation_updated', {
        'location_id': option.location_id,
        'option_id': option.id,
        'booking_status': option.booking_status,
    })
    return option


def add_option(location_id, fields):
    """Add a new accommodation option to a location.

    Args:
        location_id: AccommodationLocation PK
        fields: dict with name (required), plus optional property_type,
                price_low, price_high, booking_url, maps_url, address,
                alt_booking_url, standout, breakfast_included, has_onsen,
                user_notes
    """
    loc = AccommodationLocation.query.get_or_404(location_id)
    name = (fields.get('name') or '').strip()
    if not name:
        raise ValueError('Name is required')

    max_rank = db.session.query(db.func.max(AccommodationOption.rank)).filter_by(
        location_id=location_id).scalar() or 0

    price_low = fields.get('price_low')
    price_high = fields.get('price_high')
    if price_low is not None:
        price_low = float(price_low)
    if price_high is not None:
        price_high = float(price_high)

    option = AccommodationOption(
        location_id=location_id,
        rank=max_rank + 1,
        name=name,
        property_type=fields.get('property_type', ''),
        price_low=price_low,
        price_high=price_high,
        total_low=price_low * loc.num_nights if price_low else None,
        total_high=(price_high or price_low) * loc.num_nights if price_low else None,
        booking_url=fields.get('booking_url') or None,
        alt_booking_url=fields.get('alt_booking_url') or None,
        maps_url=fields.get('maps_url') or None,
        address=fields.get('address') or None,
        standout=fields.get('standout'),
        breakfast_included=fields.get('breakfast_included', False),
        has_onsen=fields.get('has_onsen', False),
        user_notes=fields.get('user_notes'),
    )
    db.session.add(option)
    db.session.commit()

    overlap_warning = check_accom_date_overlap(loc)

    socketio.emit('accommodation_updated', {'location_id': location_id})
    return option, loc, overlap_warning


def reorder(option_id, direction):
    """Swap an option's rank with its neighbor (up or down)."""
    option = AccommodationOption.query.get_or_404(option_id)
    siblings = AccommodationOption.query.filter_by(
        location_id=option.location_id
    ).order_by(AccommodationOption.rank).all()
    idx = next((i for i, o in enumerate(siblings) if o.id == option.id), None)
    if idx is None:
        return
    if direction == 'up' and idx > 0:
        siblings[idx].rank, siblings[idx-1].rank = siblings[idx-1].rank, siblings[idx].rank
    elif direction == 'down' and idx < len(siblings) - 1:
        siblings[idx].rank, siblings[idx+1].rank = siblings[idx+1].rank, siblings[idx].rank
    db.session.commit()

    socketio.emit('accommodation_updated', {'location_id': option.location_id})


def reorder_batch(location_id, order):
    """Set explicit rank order for all options in a location."""
    for rank, oid in enumerate(order, 1):
        opt = AccommodationOption.query.get(int(oid))
        if opt and opt.location_id == int(location_id):
            opt.rank = rank
    db.session.commit()

    socketio.emit('accommodation_updated', {'location_id': int(location_id)})


# -- Internal helpers --

def _rerank(location_id):
    """Re-rank remaining options 1..N after a deletion."""
    remaining = AccommodationOption.query.filter_by(
        location_id=location_id).order_by(AccommodationOption.rank).all()
    for i, opt in enumerate(remaining, 1):
        opt.rank = i


def _recalc_totals(option):
    """Recalculate total_low/total_high from per-night prices."""
    if option.price_low and option.location_id:
        loc = AccommodationLocation.query.get(option.location_id)
        if loc:
            option.total_low = option.price_low * loc.num_nights
            option.total_high = (option.price_high or option.price_low) * loc.num_nights


def _sync_checklist_status(option):
    """Keep the linked ChecklistItem status in sync with accommodation booking."""
    cl_item = ChecklistItem.query.filter_by(
        accommodation_location_id=option.location_id).first()
    if not cl_item:
        return
    status_map = {
        'booked': 'booked',
        'confirmed': 'booked',
        'not_booked': 'pending',
        'researching': 'researching',
        'cancelled': 'pending',
    }
    new_cl_status = status_map.get(option.booking_status)
    if new_cl_status and cl_item.status != new_cl_status:
        cl_item.status = new_cl_status
        if new_cl_status == 'booked':
            cl_item.is_completed = True
            cl_item.completed_at = datetime.utcnow()
        elif cl_item.is_completed:
            cl_item.is_completed = False
            cl_item.completed_at = None
