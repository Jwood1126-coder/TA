"""Checklist mutation service — single canonical write path."""
from datetime import datetime
from models import db, ChecklistItem, ChecklistOption, AccommodationOption
from extensions import socketio

VALID_STATUSES = {'pending', 'researching', 'decided', 'booked', 'completed'}
ADDABLE_CATEGORIES = {'pre_departure_month', 'packing_essential', 'packing_helpful'}
DELETABLE_CATEGORIES = {'pre_departure_month', 'packing_essential', 'packing_helpful'}


def toggle(item_id):
    """Toggle checklist item completion. Syncs status field."""
    item = ChecklistItem.query.get_or_404(item_id)
    item.is_completed = not item.is_completed
    item.completed_at = datetime.utcnow() if item.is_completed else None
    # Keep status in sync
    if item.is_completed and item.status != 'completed':
        item.status = 'completed'
    elif not item.is_completed and item.status == 'completed':
        item.status = 'pending'
    db.session.commit()

    socketio.emit('checklist_toggled', {
        'id': item.id,
        'is_completed': item.is_completed,
    })
    return item


def update_status(item_id, new_status):
    """Update checklist status. Cascades to linked accommodation."""
    if new_status not in VALID_STATUSES:
        raise ValueError(f'Invalid status: {new_status}')

    item = ChecklistItem.query.get_or_404(item_id)
    item.status = new_status
    if item.status == 'completed':
        item.is_completed = True
        item.completed_at = datetime.utcnow()
    elif item.is_completed and item.status != 'completed':
        item.is_completed = False
        item.completed_at = None

    # Cascade to linked accommodation
    accom_synced = False
    if item.accommodation_location_id:
        selected_opt = AccommodationOption.query.filter_by(
            location_id=item.accommodation_location_id,
            is_selected=True
        ).first()
        if selected_opt:
            if new_status in ('booked', 'completed') and \
               selected_opt.booking_status not in ('booked', 'confirmed'):
                selected_opt.booking_status = 'booked'
                accom_synced = True
            elif new_status in ('pending', 'researching') and \
                 selected_opt.booking_status == 'booked':
                selected_opt.booking_status = 'not_booked'
                accom_synced = True

    db.session.commit()

    socketio.emit('checklist_status_changed', {
        'id': item.id, 'status': item.status,
    })
    if accom_synced:
        socketio.emit('accommodation_updated', {
            'location_id': item.accommodation_location_id,
        })
    return item


def create(fields):
    """Create a new checklist item."""
    title = (fields.get('title') or '').strip()
    category = fields.get('category', 'pre_departure_month')
    if not title:
        raise ValueError('Title is required')
    if category not in ADDABLE_CATEGORIES:
        raise ValueError(f'Cannot add items to category: {category}')

    max_order = db.session.query(
        db.func.max(ChecklistItem.sort_order)
    ).filter_by(category=category).scalar() or 0

    item = ChecklistItem(
        category=category,
        title=title,
        description=fields.get('description'),
        priority=fields.get('priority', 'medium'),
        url=fields.get('url'),
        item_type=fields.get('item_type', 'task'),
        status='pending',
        sort_order=max_order + 1,
    )
    db.session.add(item)
    db.session.commit()

    socketio.emit('checklist_added', {'id': item.id, 'category': item.category})
    return item


def delete(item_id, enforce_category=True):
    """Delete a checklist item and its child options."""
    item = ChecklistItem.query.get_or_404(item_id)
    if enforce_category and item.category not in DELETABLE_CATEGORIES:
        raise ValueError('Cannot delete booking/accommodation items')

    ChecklistOption.query.filter_by(checklist_item_id=item.id).delete()
    db.session.delete(item)
    db.session.commit()

    socketio.emit('checklist_deleted', {'id': item_id})
    return item_id
