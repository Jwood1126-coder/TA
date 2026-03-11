import os
import json
import uuid
import requests
from bs4 import BeautifulSoup
from flask import Blueprint, render_template, jsonify, request, current_app
from models import db, AccommodationLocation, AccommodationOption
import services.accommodations as accom_svc

accommodations_bp = Blueprint('accommodations', __name__)


@accommodations_bp.route('/accommodations')
def accommodations_view():
    from services.trip_audit import audit_trip
    locations = AccommodationLocation.query.order_by(
        AccommodationLocation.check_in_date).all()
    # Build booking status summary per location for tab badges
    loc_status = {}
    for loc in locations:
        booked = [o for o in loc.options
                  if o.booking_status in ('booked', 'confirmed')]
        loc_status[loc.id] = {
            'booked_count': len(booked),
            'booked_name': booked[0].name if len(booked) == 1 else None,
            'double_booked': len(booked) > 1,
        }
    # Run audit to surface accommodation blockers
    audit = audit_trip()
    accom_blockers = [b for b in audit.blockers
                      if any(kw in b.lower() for kw in
                             ('accommodation', 'overlap', 'stay', 'multiple stays',
                              'selected options', 'night'))]
    accom_warnings = [w for w in audit.warnings
                      if any(kw in w.lower() for kw in
                             ('night', 'accommodation', 'stay'))]
    return render_template('accommodations.html',
                           locations=locations, loc_status=loc_status,
                           accom_blockers=accom_blockers,
                           accom_warnings=accom_warnings)


@accommodations_bp.route('/api/accommodations/<int:option_id>/select',
                          methods=['POST'])
def select_option(option_id):
    try:
        accom_svc.select(option_id)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True})


@accommodations_bp.route('/api/accommodations/<int:option_id>/eliminate',
                          methods=['POST'])
def eliminate_option(option_id):
    try:
        option = accom_svc.eliminate(option_id)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, 'is_eliminated': option.is_eliminated})


@accommodations_bp.route('/api/accommodations/<int:option_id>/delete',
                          methods=['DELETE'])
def delete_option(option_id):
    accom_svc.delete(option_id)
    return jsonify({'ok': True})


@accommodations_bp.route('/api/accommodations/<int:option_id>/reorder',
                          methods=['PUT'])
def reorder_option(option_id):
    data = request.get_json()
    accom_svc.reorder(option_id, data.get('direction'))
    return jsonify({'ok': True})


@accommodations_bp.route('/api/accommodations/reorder-batch', methods=['PUT'])
def reorder_batch():
    data = request.get_json()
    location_id = data.get('location_id')
    order = data.get('order', [])
    if not location_id or not order:
        return jsonify({'ok': False}), 400
    accom_svc.reorder_batch(location_id, order)
    return jsonify({'ok': True})


@accommodations_bp.route('/api/accommodations/<int:location_id>/add', methods=['POST'])
def add_option(location_id):
    data = request.get_json()
    try:
        option, loc, overlap = accom_svc.add_option(location_id, data)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True, 'id': option.id})


@accommodations_bp.route('/api/accommodations/fetch-url', methods=['POST'])
def fetch_url_info():
    """Fetch a booking URL, extract text, and use Claude to parse property info."""
    data = request.get_json()
    url = (data.get('url') or '').strip()
    if not url:
        return jsonify({'ok': False, 'error': 'No URL provided'}), 400

    from urllib.parse import urlparse
    import ipaddress
    import socket
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return jsonify({'ok': False, 'error': 'URL must use http or https'}), 400
    hostname = parsed.hostname or ''
    try:
        resolved = socket.gethostbyname(hostname)
        ip = ipaddress.ip_address(resolved)
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
            return jsonify({'ok': False, 'error': 'URL resolves to a private address'}), 400
    except (socket.gaierror, ValueError):
        return jsonify({'ok': False, 'error': 'Could not resolve hostname'}), 400

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        return jsonify({'ok': False, 'error': f'Could not fetch URL: {str(e)}'}), 400

    soup = BeautifulSoup(resp.text, 'html.parser')
    for tag in soup(['script', 'style', 'noscript', 'iframe', 'svg']):
        tag.decompose()

    meta_info = {}
    for meta in soup.find_all('meta'):
        prop = meta.get('property', '') or meta.get('name', '')
        content = meta.get('content', '')
        if prop and content:
            meta_info[prop] = content[:500]

    json_ld = []
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            json_ld.append(script.string[:2000])
        except Exception:
            pass

    title = soup.title.string.strip() if soup.title and soup.title.string else ''
    body_text = soup.get_text(separator=' ', strip=True)[:4000]

    page_context = f"Page title: {title}\n\n"
    if meta_info:
        page_context += "Meta tags:\n"
        for k, v in list(meta_info.items())[:20]:
            page_context += f"  {k}: {v}\n"
        page_context += "\n"
    if json_ld:
        page_context += "Structured data (JSON-LD):\n"
        for ld in json_ld[:3]:
            page_context += f"  {ld}\n"
        page_context += "\n"
    page_context += f"Page text (trimmed):\n{body_text}"

    api_key = current_app.config.get('ANTHROPIC_API_KEY')
    if not api_key:
        return jsonify({
            'ok': True,
            'data': {
                'name': meta_info.get('og:title', title).split('|')[0].split('-')[0].strip(),
                'property_type': '',
                'price_low': None,
                'price_high': None,
            }
        })

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=500,
            messages=[{
                'role': 'user',
                'content': f"""Extract accommodation info from this booking page. Return ONLY valid JSON with these fields:
- name: property name (string, clean — no site name like "Booking.com")
- property_type: e.g. Hotel, Ryokan, Hostel, Guesthouse, Apartment (string, best guess)
- price_low: lowest nightly price in USD if visible (number or null)
- price_high: highest nightly price in USD if visible (number or null)
- address: property address if visible (string or null)

{page_context}"""
            }],
        )
        result_text = msg.content[0].text.strip()
        if '```' in result_text:
            result_text = result_text.split('```')[1]
            if result_text.startswith('json'):
                result_text = result_text[4:]
            result_text = result_text.strip()
        parsed_result = json.loads(result_text)
        return jsonify({'ok': True, 'data': parsed_result})
    except Exception:
        return jsonify({
            'ok': True,
            'data': {
                'name': meta_info.get('og:title', title).split('|')[0].split('-')[0].strip(),
                'property_type': '',
                'price_low': None,
                'price_high': None,
            }
        })


@accommodations_bp.route('/api/accommodations/<int:option_id>/status',
                          methods=['PUT'])
def update_status(option_id):
    data = request.get_json()
    try:
        accom_svc.update_status(option_id, data)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True})


@accommodations_bp.route('/api/accommodations/<int:option_id>/upload-image',
                          methods=['POST'])
def upload_booking_image(option_id):
    option = AccommodationOption.query.get_or_404(option_id)
    file = request.files.get('image')
    if not file or not file.filename:
        return jsonify({'ok': False, 'error': 'No file'}), 400

    allowed = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'heic', 'heif'}
    ext = file.filename.rsplit('.', 1)[-1].lower()
    if ext not in allowed:
        return jsonify({'ok': False, 'error': 'Invalid file type'}), 400

    filename = f"booking_{option_id}_{uuid.uuid4().hex[:8]}.{ext}"
    upload_dir = current_app.config['UPLOAD_FOLDER']
    os.makedirs(os.path.join(upload_dir, 'originals'), exist_ok=True)
    file.save(os.path.join(upload_dir, 'originals', filename))

    if option.booking_image:
        old_path = os.path.join(upload_dir, 'originals', option.booking_image)
        if os.path.exists(old_path):
            os.remove(old_path)

    option.booking_image = filename
    db.session.commit()
    return jsonify({'ok': True, 'filename': filename})


@accommodations_bp.route('/api/accommodations/<int:option_id>/delete-image',
                          methods=['DELETE'])
def delete_booking_image(option_id):
    option = AccommodationOption.query.get_or_404(option_id)
    if option.booking_image:
        upload_dir = current_app.config['UPLOAD_FOLDER']
        old_path = os.path.join(upload_dir, 'originals', option.booking_image)
        if os.path.exists(old_path):
            os.remove(old_path)
        option.booking_image = None
        db.session.commit()
    return jsonify({'ok': True})
