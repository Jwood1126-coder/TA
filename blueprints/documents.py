import os
import uuid
from flask import (Blueprint, render_template, jsonify, request,
                   send_from_directory, current_app)
from werkzeug.utils import secure_filename
from models import (db, Document, Flight, AccommodationLocation,
                    AccommodationOption, TransportRoute, Location, Day)
from guardrails import validate_booking_status, validate_document_status

documents_bp = Blueprint('documents', __name__)

ALLOWED_DOC_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'webp'}


def _docs_folder():
    """Return path to documents storage folder, creating if needed."""
    folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'documents')
    os.makedirs(folder, exist_ok=True)
    return folder


def seed_document_records():
    """Create Document records for files on disk that don't have DB records yet.
    Called on boot to sync disk -> DB."""
    folder = _docs_folder()
    created = 0
    if os.path.isdir(folder):
        for fname in os.listdir(folder):
            ext = fname.rsplit('.', 1)[-1].lower() if '.' in fname else ''
            if ext not in ALLOWED_DOC_EXTENSIONS:
                continue
            existing = Document.query.filter_by(filename=fname).first()
            if existing:
                continue
            display = fname.split('__', 1)[1] if '__' in fname else fname
            filepath = os.path.join(folder, fname)
            file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
            doc = Document(
                filename=fname,
                original_name=display,
                file_type=ext,
                file_size=file_size,
                doc_type='other',
            )
            db.session.add(doc)
            created += 1
    db.session.commit()
    return created


def auto_link_documents():
    """Auto-link unlinked Document records to bookings by matching
    confirmation numbers, name keywords, and date ranges in filenames."""
    docs = Document.query.all()
    linked = 0

    # Get all confirmed/booked accommodations with their locations
    options = AccommodationOption.query.filter(
        AccommodationOption.is_selected == True,
        AccommodationOption.booking_status.in_(['booked', 'confirmed'])
    ).all()

    # Preload locations for date matching
    from models import AccommodationLocation
    loc_by_id = {loc.id: loc for loc in AccommodationLocation.query.all()}

    flights = Flight.query.all()

    for doc in docs:
        # Skip docs already linked to something
        if doc.accommodations or doc.flights:
            continue

        fname_lower = (doc.original_name or doc.filename).lower()

        # Try to match to accommodations
        matched_accom = False
        for opt in options:
            if opt.document_id:
                continue
            # Match by confirmation number
            if opt.confirmation_number and opt.confirmation_number.lower() in fname_lower:
                opt.document_id = doc.id
                doc.doc_type = 'accommodation_booking'
                linked += 1
                matched_accom = True
                break
            # Match by accommodation name keywords
            name_words = opt.name.lower().split()
            if any(w in fname_lower for w in name_words if len(w) > 3):
                opt.document_id = doc.id
                doc.doc_type = 'accommodation_booking'
                linked += 1
                matched_accom = True
                break
            # Match by date range (e.g. "Apr 12 – 14", "Apr_12_14", "april_9_12")
            loc = loc_by_id.get(opt.location_id)
            if loc and loc.check_in_date and loc.check_out_date:
                ci = loc.check_in_date
                co = loc.check_out_date
                month = ci.strftime('%b').lower()  # "apr"
                month_full = ci.strftime('%B').lower()  # "april"
                date_patterns = [
                    f'{month}_{ci.day}_{co.day}',
                    f'{month} {ci.day}',
                    f'{month}_{ci.day}',
                    f'{month_full}_{ci.day}_{co.day}',
                    f'{month_full}_{ci.day}',
                    f'reservation_for_{month}_{ci.day}',
                ]
                if any(p in fname_lower for p in date_patterns):
                    opt.document_id = doc.id
                    doc.doc_type = 'accommodation_booking'
                    linked += 1
                    matched_accom = True
                    break

        if not matched_accom:
            # Try to match to flights — don't break after first match,
            # because multi-leg flights share one confirmation number
            for flight in flights:
                if flight.document_id:
                    continue
                matched = False
                if flight.confirmation_number and flight.confirmation_number.lower() in fname_lower:
                    matched = True
                elif flight.flight_number and flight.flight_number.lower() in fname_lower:
                    matched = True
                elif flight.depart_date:
                    # Match flight receipts by date pattern
                    day_str = f'{flight.depart_date.day:02d}'
                    month_str = flight.depart_date.strftime('%b').lower()
                    if ('flight' in fname_lower or 'receipt' in fname_lower or 'eticket' in fname_lower):
                        if f'{day_str}{month_str}' in fname_lower or f'{month_str}{day_str}' in fname_lower:
                            matched = True
                if matched:
                    flight.document_id = doc.id
                    doc.doc_type = 'flight_receipt'
                    linked += 1

    db.session.commit()
    return linked


@documents_bp.route('/documents')
def documents_view():
    flights = Flight.query.order_by(Flight.direction, Flight.leg_number).all()

    # Only show booked or confirmed accommodations
    accom_locations = AccommodationLocation.query.order_by(
        AccommodationLocation.check_in_date).all()
    accommodations = []
    for loc in accom_locations:
        selected = next((o for o in loc.options if o.is_selected), None)
        if selected and selected.booking_status in ('booked', 'confirmed'):
            accommodations.append({'location': loc, 'selected': selected})

    transport = TransportRoute.query.order_by(TransportRoute.sort_order).all()

    # Activities with tickets/bookings (have URL or cost)
    locations = Location.query.order_by(Location.sort_order).all()
    days = Day.query.order_by(Day.day_number).all()
    ticketed_activities = []
    for loc in locations:
        loc_days = [d for d in days if d.location_id == loc.id]
        for d in loc_days:
            for a in d.activities:
                if a.is_substitute or a.is_eliminated:
                    continue
                if a.url or a.cost_per_person:
                    ticketed_activities.append({
                        'activity': a, 'day': d, 'location': loc
                    })

    # All Document records from DB
    all_docs = Document.query.order_by(Document.uploaded_at.desc()).all()

    # Build doc_links from DB relationships (document_id on options/flights)
    doc_links = {}
    linked_doc_ids = set()

    for item in accommodations:
        opt = item['selected']
        loc = item['location']
        key = f'accom_{loc.id}'
        if opt.document_id and opt.document:
            doc_links[key] = [opt.document]
            linked_doc_ids.add(opt.document_id)
        else:
            doc_links[key] = []

    for flight in flights:
        key = f'flight_{flight.id}'
        if flight.document_id and flight.document:
            doc_links[key] = [flight.document]
            linked_doc_ids.add(flight.document_id)
        else:
            doc_links[key] = []

    # Unlinked documents (not attached to any booking)
    unlinked_docs = [d for d in all_docs if d.id not in linked_doc_ids]

    return render_template('documents.html',
                           flights=flights,
                           accommodations=accommodations,
                           transport=transport,
                           ticketed_activities=ticketed_activities,
                           all_docs=all_docs,
                           unlinked_docs=unlinked_docs,
                           doc_links=doc_links)


@documents_bp.route('/api/documents/flight/<int:flight_id>/confirmation',
                     methods=['PUT'])
def update_flight_confirmation(flight_id):
    flight = Flight.query.get_or_404(flight_id)
    data = request.get_json()
    new_status = data.get('booking_status')
    if new_status is not None:
        try:
            new_status = validate_booking_status(new_status)
        except ValueError as e:
            return jsonify({'ok': False, 'error': str(e)}), 400
        # Enforce document-first rule
        try:
            validate_document_status(new_status, flight.document_id,
                                     f'flight {flight.flight_number}')
        except ValueError as e:
            return jsonify({'ok': False, 'error': str(e)}), 400
        flight.booking_status = new_status
    flight.confirmation_number = data.get('confirmation_number',
                                          flight.confirmation_number)
    db.session.commit()

    from extensions import socketio
    socketio.emit('document_updated', {'type': 'flight', 'id': flight.id})

    return jsonify({'ok': True})


@documents_bp.route('/api/documents/upload', methods=['POST'])
def upload_document():
    """Upload a PDF or image document, creating a Document record."""
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'No file provided'}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({'ok': False, 'error': 'No file selected'}), 400

    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
    if ext not in ALLOWED_DOC_EXTENSIONS:
        return jsonify({'ok': False, 'error': f'File type .{ext} not allowed'}), 400

    original_name = secure_filename(f.filename)
    unique_name = f"{uuid.uuid4().hex[:8]}__{original_name}"
    filepath = os.path.join(_docs_folder(), unique_name)
    f.save(filepath)

    file_size = os.path.getsize(filepath)
    doc_type = request.form.get('doc_type', 'other')

    # Create Document record in DB
    doc = Document(
        filename=unique_name,
        original_name=original_name,
        file_type=ext,
        file_size=file_size,
        doc_type=doc_type,
    )
    db.session.add(doc)
    db.session.commit()

    return jsonify({
        'ok': True,
        'document_id': doc.id,
        'filename': unique_name,
        'display_name': original_name,
    })


@documents_bp.route('/api/documents/<int:doc_id>/link', methods=['POST'])
def link_document(doc_id):
    """Link a document to a booking (accommodation or flight)."""
    doc = Document.query.get_or_404(doc_id)
    data = request.get_json() or {}

    entity_type = data.get('entity_type')  # 'accommodation' or 'flight'
    entity_id = data.get('entity_id')

    if not entity_type or not entity_id:
        return jsonify({'ok': False, 'error': 'entity_type and entity_id required'}), 400

    if entity_type == 'accommodation':
        option = AccommodationOption.query.get_or_404(entity_id)
        option.document_id = doc.id
        doc.doc_type = 'accommodation_booking'
        db.session.commit()
        from extensions import socketio
        socketio.emit('document_updated', {'type': 'accommodation', 'id': option.id})
        return jsonify({'ok': True, 'message': f"Linked document to {option.name}"})

    elif entity_type == 'flight':
        flight = Flight.query.get_or_404(entity_id)
        flight.document_id = doc.id
        doc.doc_type = 'flight_receipt'
        db.session.commit()
        from extensions import socketio
        socketio.emit('document_updated', {'type': 'flight', 'id': flight.id})
        return jsonify({'ok': True, 'message': f"Linked document to {flight.flight_number}"})

    return jsonify({'ok': False, 'error': f"Unknown entity_type '{entity_type}'"}), 400


@documents_bp.route('/api/documents/<int:doc_id>/unlink', methods=['POST'])
def unlink_document(doc_id):
    """Unlink a document from its booking. Downgrades confirmed->booked."""
    doc = Document.query.get_or_404(doc_id)

    # Find and unlink from accommodations
    for opt in doc.accommodations:
        opt.document_id = None
        if opt.booking_status == 'confirmed':
            opt.booking_status = 'booked'

    # Find and unlink from flights
    for flight in doc.flights:
        flight.document_id = None
        if flight.booking_status == 'confirmed':
            flight.booking_status = 'booked'

    db.session.commit()
    return jsonify({'ok': True})


@documents_bp.route('/api/documents/file/<path:filename>')
def serve_document(filename):
    """Serve an uploaded document file."""
    return send_from_directory(_docs_folder(), filename)


@documents_bp.route('/api/documents/file/<path:filename>', methods=['DELETE'])
def delete_document(filename):
    """Delete an uploaded document and its DB record."""
    docs_dir = os.path.realpath(_docs_folder())
    filepath = os.path.realpath(os.path.join(docs_dir, os.path.basename(filename)))
    if not filepath.startswith(docs_dir + os.sep):
        return jsonify({'ok': False, 'error': 'Invalid filename'}), 400

    # Remove DB record if it exists
    doc = Document.query.filter_by(filename=filename).first()
    if doc:
        # Unlink from any bookings first (downgrades confirmed->booked)
        for opt in doc.accommodations:
            opt.document_id = None
            if opt.booking_status == 'confirmed':
                opt.booking_status = 'booked'
        for flight in doc.flights:
            flight.document_id = None
            if flight.booking_status == 'confirmed':
                flight.booking_status = 'booked'
        db.session.delete(doc)
        db.session.commit()

    if os.path.exists(filepath):
        os.remove(filepath)
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': 'File not found'}), 404
