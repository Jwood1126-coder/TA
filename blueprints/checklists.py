from flask import Blueprint, render_template, jsonify, request
from models import db, ChecklistItem, ChecklistOption, Flight, \
    AccommodationLocation, AccommodationOption, TransportRoute, Day, Activity, Trip
from datetime import datetime, date

checklists_bp = Blueprint('checklists', __name__)

# Map categories to high-level groupings
CATEGORY_MAP = {
    'pre_departure_today': 'preparation',
    'pre_departure_week': 'preparation',
    'pre_departure_miles': 'preparation',
    'pre_departure_month': 'preparation',
    'packing_essential': 'packing',
    'packing_helpful': 'packing',
}


def _section_progress(items_list):
    done = sum(1 for i in items_list if i.is_completed)
    return {'done': done, 'total': len(items_list), 'decided': 0}


def _build_pretip_sections(items):
    """Build structured checklist sections for pre-trip tab."""
    preparation = []
    packing_essential = []
    packing_helpful = []

    for item in items:
        group = CATEGORY_MAP.get(item.category, 'preparation')

        if group == 'packing':
            if item.category == 'packing_essential':
                packing_essential.append(item)
            else:
                packing_helpful.append(item)
        else:
            preparation.append(item)

    sections = []

    if preparation:
        sections.append({
            'key': 'preparation',
            'label': 'Preparation',
            'icon': 'clipboard',
            'entries': preparation,
            'subgroups': None,
            'progress': _section_progress(preparation),
        })

    if packing_essential or packing_helpful:
        all_packing = packing_essential + packing_helpful
        subgroups = []
        if packing_essential:
            subgroups.append({'name': 'Essential', 'entries': packing_essential})
        if packing_helpful:
            subgroups.append({'name': 'Helpful', 'entries': packing_helpful})
        sections.append({
            'key': 'packing',
            'label': 'Packing',
            'icon': 'backpack',
            'entries': None,
            'subgroups': subgroups,
            'progress': _section_progress(all_packing),
        })

    return sections


@checklists_bp.route('/checklists')
def checklists_view():
    tab = request.args.get('tab', '')

    # Auto-select tab based on trip dates
    if not tab:
        trip = Trip.query.first()
        today = date.today()
        if trip and today >= trip.start_date:
            tab = 'on_trip'
        else:
            tab = 'pre_trip'

    if tab == 'on_trip':
        upcoming = _build_upcoming_events()
        return render_template('checklists.html', tab=tab, upcoming=upcoming,
                               sections=None)

    # Pre-trip: eagerly load options and accommodation data
    items = ChecklistItem.query.filter(
        ChecklistItem.sort_order < 9999
    ).options(
        db.joinedload(ChecklistItem.options),
        db.joinedload(ChecklistItem.accommodation_location)
            .joinedload(AccommodationLocation.options)
    ).order_by(ChecklistItem.sort_order).all()

    sections = _build_pretip_sections(items)

    return render_template('checklists.html', tab=tab, sections=sections,
                           upcoming=None)


# ---------- Existing toggle endpoint ----------

@checklists_bp.route('/api/checklists/<int:item_id>/toggle', methods=['POST'])
def toggle_checklist(item_id):
    import services.checklists as checklist_svc
    item = checklist_svc.toggle(item_id)
    return jsonify({'ok': True, 'is_completed': item.is_completed})


# ---------- New: Update item status ----------

VALID_CHECKLIST_STATUSES = {'pending', 'researching', 'decided', 'booked', 'completed'}


@checklists_bp.route('/api/checklists/<int:item_id>/status', methods=['PUT'])
def update_checklist_status(item_id):
    import services.checklists as checklist_svc
    data = request.get_json()
    item = ChecklistItem.query.get_or_404(item_id)
    new_status = data.get('status', item.status)
    try:
        checklist_svc.update_status(item_id, new_status)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True})


# ---------- New: ChecklistOption endpoints ----------

@checklists_bp.route('/api/checklist-options/<int:option_id>/eliminate', methods=['POST'])
def toggle_option_elimination(option_id):
    option = ChecklistOption.query.get_or_404(option_id)
    option.is_eliminated = not option.is_eliminated
    db.session.commit()

    from extensions import socketio
    socketio.emit('checklist_option_updated', {
        'checklist_item_id': option.checklist_item_id,
        'option_id': option.id,
        'is_eliminated': option.is_eliminated,
    })
    return jsonify({'ok': True, 'is_eliminated': option.is_eliminated})


@checklists_bp.route('/api/checklist-options/<int:option_id>/select', methods=['POST'])
def select_checklist_option(option_id):
    option = ChecklistOption.query.get_or_404(option_id)
    # Deselect others in same item
    ChecklistOption.query.filter_by(
        checklist_item_id=option.checklist_item_id
    ).update({'is_selected': False})
    option.is_selected = True
    # Update parent status
    item = ChecklistItem.query.get(option.checklist_item_id)
    if item and item.status in ('pending', 'researching'):
        item.status = 'decided'
    db.session.commit()

    from extensions import socketio
    socketio.emit('checklist_option_updated', {
        'checklist_item_id': option.checklist_item_id,
        'selected_id': option.id,
    })
    return jsonify({'ok': True})


@checklists_bp.route('/api/checklist-options/<int:option_id>/notes', methods=['PUT'])
def update_option_notes(option_id):
    option = ChecklistOption.query.get_or_404(option_id)
    data = request.get_json()
    option.user_notes = data.get('user_notes', '')
    db.session.commit()
    return jsonify({'ok': True})


ADDABLE_CATEGORIES = {'pre_departure_month', 'packing_essential', 'packing_helpful'}


@checklists_bp.route('/api/checklists', methods=['POST'])
def create_checklist_item():
    """Create a new checklist item (preparation or packing only)."""
    import services.checklists as checklist_svc
    data = request.get_json()
    try:
        item = checklist_svc.create(data)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, 'id': item.id})


DELETABLE_CATEGORIES = {'pre_departure_month', 'packing_essential', 'packing_helpful'}


@checklists_bp.route('/api/checklists/<int:item_id>', methods=['DELETE'])
def delete_checklist_item(item_id):
    """Delete a checklist item (preparation or packing only)."""
    import services.checklists as checklist_svc
    try:
        checklist_svc.delete(item_id)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True})


@checklists_bp.route('/api/checklists/<int:item_id>/options', methods=['POST'])
def add_checklist_option(item_id):
    item = ChecklistItem.query.get_or_404(item_id)
    data = request.get_json()
    max_order = db.session.query(
        db.func.max(ChecklistOption.sort_order)
    ).filter_by(checklist_item_id=item_id).scalar() or 0
    option = ChecklistOption(
        checklist_item_id=item_id,
        name=data['name'],
        description=data.get('description'),
        why=data.get('why'),
        url=data.get('url'),
        maps_url=data.get('maps_url'),
        price_note=data.get('price_note'),
        sort_order=max_order + 1,
    )
    db.session.add(option)
    db.session.commit()
    return jsonify({'ok': True, 'option': option.to_dict()})


# ---------- Upcoming events (On-Trip tab) ----------

def _build_upcoming_events():
    """Build dynamic list of upcoming logistics from itinerary data."""
    today = date.today()
    events = []

    # Upcoming flights
    flights = Flight.query.filter(
        Flight.depart_date >= today
    ).order_by(Flight.depart_date, Flight.depart_time).all()
    for f in flights:
        events.append({
            'type': 'flight',
            'date': f.depart_date,
            'time': f.depart_time,
            'title': f'{f.airline} {f.flight_number}',
            'subtitle': f'{f.route_from} \u2192 {f.route_to}',
            'status': f.booking_status or 'not_booked',
            'icon': 'plane',
            'address': None,
        })

    # Accommodation check-ins
    checkins = AccommodationLocation.query.filter(
        AccommodationLocation.check_in_date >= today
    ).order_by(AccommodationLocation.check_in_date).all()
    for a in checkins:
        selected = AccommodationOption.query.filter_by(
            location_id=a.id, is_selected=True).first()
        name = selected.name if selected else a.location_name
        status = selected.booking_status if selected else 'not_booked'
        address = selected.address if selected else None
        events.append({
            'type': 'checkin',
            'date': a.check_in_date,
            'time': None,
            'title': f'Check in: {name}',
            'subtitle': a.location_name,
            'status': status,
            'icon': 'hotel',
            'address': address,
        })

    # Accommodation check-outs
    checkouts = AccommodationLocation.query.filter(
        AccommodationLocation.check_out_date >= today
    ).order_by(AccommodationLocation.check_out_date).all()
    for a in checkouts:
        selected = AccommodationOption.query.filter_by(
            location_id=a.id, is_selected=True).first()
        if selected:
            events.append({
                'type': 'checkout',
                'date': a.check_out_date,
                'time': None,
                'title': f'Check out: {selected.name}',
                'subtitle': a.location_name,
                'status': 'info',
                'icon': 'bellhop',
                'address': selected.address,
            })

    # Transport routes
    days = Day.query.filter(Day.date >= today).order_by(Day.date).all()
    day_ids = [d.id for d in days]
    day_map = {d.id: d for d in days}

    routes = TransportRoute.query.filter(
        TransportRoute.day_id.in_(day_ids)
    ).order_by(TransportRoute.sort_order).all()
    for r in routes:
        day = day_map.get(r.day_id)
        events.append({
            'type': 'transport',
            'date': day.date if day else today,
            'time': None,
            'title': f'{r.route_from} \u2192 {r.route_to}',
            'subtitle': f'{r.transport_type} {r.train_name or ""}',
            'status': 'jr_covered' if r.jr_pass_covered else ('ticket_needed' if r.cost_if_not_covered else 'info'),
            'icon': 'train',
            'address': None,
        })

    # Also find transport routes without day_id (matched by location)
    if not routes:
        for day in days:
            if day.day_number <= 1:
                continue
            prev = Day.query.filter_by(day_number=day.day_number - 1).first()
            if prev and prev.location and day.location and prev.location.name != day.location.name:
                found = TransportRoute.query.filter_by(
                    route_from=prev.location.name, route_to=day.location.name
                ).all()
                for r in found:
                    events.append({
                        'type': 'transport',
                        'date': day.date,
                        'time': None,
                        'title': f'{r.route_from} \u2192 {r.route_to}',
                        'subtitle': f'{r.transport_type} {r.train_name or ""}',
                        'status': 'jr_covered' if r.jr_pass_covered else ('ticket_needed' if r.cost_if_not_covered else 'info'),
                        'icon': 'train',
                        'address': None,
                    })

    # Ticketed activities (cost > 0)
    for day in days:
        for a in day.activities:
            if a.cost_per_person and a.cost_per_person > 0 and not a.is_substitute and not a.is_completed:
                events.append({
                    'type': 'ticketed',
                    'date': day.date,
                    'time': a.start_time,
                    'title': a.title,
                    'subtitle': f'Day {day.day_number} \u2022 \u00a5{int(a.cost_per_person)}/person',
                    'status': 'upcoming',
                    'icon': 'ticket',
                    'address': a.address,
                })

    # Sort by date, then time
    events.sort(key=lambda e: (e['date'], e['time'] or ''))
    return events
