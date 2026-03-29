"""Gmail sync service — fetches travel emails and extracts booking data.

Uses Google Gmail API to search for travel-related emails, then uses
Claude API to extract structured booking data from email content.
Proposed changes are stored for user review before applying.
"""
import base64
import json
import os
import re
from datetime import datetime

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build as build_gmail

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

# Search queries for travel-related emails
TRAVEL_QUERIES = [
    # Flights — receipts, itineraries, confirmations
    'subject:(flight receipt OR eTicket OR itinerary) (Delta OR United OR "DL275" OR "DL5392" OR "UA876" OR "UA1470" OR "HBPF75" OR "I91ZHJ") after:2025/06/01',
    # Boarding passes & check-in
    'subject:(boarding pass OR "check-in" OR "checked in" OR "mobile boarding" OR "your trip") (Delta OR United OR "DL275" OR "DL5392" OR "UA876" OR "UA1470") after:2026/03/01',
    'from:(delta.com OR united.com) subject:(boarding OR "check in" OR "ready to go" OR "trip is coming") after:2026/03/01',
    # Accommodations
    'subject:(booking confirmation OR reservation confirmed OR "your receipt") (Agoda OR Airbnb) after:2025/06/01',
    # Broader travel bookings
    'subject:(booking OR confirmation OR reservation OR receipt) (hotel OR ryokan OR hostel OR machiya) after:2025/06/01',
    # Specific known bookings
    '"Sotetsu Fresa" OR "TAKANOYU" OR "Tsukiya-Mikazuki" OR "Kyotofish" OR "Leben Osaka" OR "KumoMachiya" after:2025/06/01',
    # Cancellations
    'subject:(cancelled OR canceled OR cancellation) (airbnb OR agoda OR hotel OR flight) after:2025/06/01',
    # JR Pass, activities, tickets
    'subject:(order confirmation OR booking confirmation OR e-ticket) (JR Pass OR "Japan Rail" OR klook OR viator OR GetYourGuide) after:2025/06/01',
    # Train reservations
    'subject:(reservation OR "seat reservation" OR ticket) (shinkansen OR "bullet train" OR "japan rail" OR JR) after:2025/06/01',
    # Restaurant reservations
    'subject:(reservation OR booking OR "your table") (restaurant OR omakase OR izakaya OR ramen OR sushi OR kaiseki OR yakitori) Japan after:2025/06/01',
    '(from:tablecheck OR from:tabelog OR from:toreta OR from:opentable) subject:(reservation OR confirmation) after:2025/06/01',
    # Activity & experience bookings
    '(from:klook OR from:viator OR from:getyourguide OR from:airbnb.com) subject:(experience OR activity OR tour OR ticket OR booking) Japan after:2025/06/01',
    'subject:(order confirmation OR booking confirmation OR e-ticket) (tea ceremony OR kimono OR cooking class OR sumo OR shrine OR temple OR onsen) after:2025/06/01',
    # General Japan travel
    'subject:(confirmation OR reservation OR receipt OR ticket) Japan (Tokyo OR Kyoto OR Osaka OR Takayama OR Hiroshima OR Hakone OR Miyajima) after:2025/06/01',
]

# Extraction prompt for Claude
EXTRACTION_PROMPT_TEMPLATE = """You are a travel booking data extractor for a Japan trip (April 5-18, 2026).
Analyze this email and extract structured booking data.

Context — these are the confirmed bookings to match against:
- Flights: DL5392 CLE->DTW, DL275 DTW->HND (Apr 5, conf HBPF75), UA876 HND->SFO, UA1470 SFO->CLE (Apr 18, conf I91ZHJ)
- Tokyo: Sotetsu Fresa Inn, Apr 6-9, Agoda #976558450
- Takayama: TAKANOYU, Apr 9-12, Airbnb #HMDDRX4NFX
- Kyoto Stay 1: Tsukiya-Mikazuki, Apr 12-14, Airbnb #HMXTP9H2Z9
- Kyoto Stay 2: KumoMachiya KOSUGI, Apr 14-16, Airbnb #HMYR9JPSN4
- Osaka: Hotel The Leben, Apr 16-18, Agoda #976698966

Return a JSON object with these fields (omit fields that aren't present):

```
"type": one of: "boarding_pass", "flight", "accommodation", "restaurant", "activity_ticket", "transport_ticket", "cancellation", "other"
"action": "new_booking" | "update" | "cancellation" | "confirmation" | "check_in" | "boarding_pass" | "info"
"property_name": "hotel/property name"
"confirmation_number": "booking reference"
"platform": "Airbnb" | "Agoda" | "Delta" | "United" | etc
"check_in_date": "YYYY-MM-DD"
"check_out_date": "YYYY-MM-DD"
"check_in_time": "e.g. 4:00 PM"
"check_out_time": "e.g. 11:00 AM"
"address": "full address"
"city": "city name"
"guests": number
"price_total": number
"price_per_night": number
"currency": "USD" or "JPY"
"host_name": "host name if applicable"
"host_phone": "phone if provided"
"flight_number": "e.g. DL275"
"airline": "airline name"
"departure_airport": "code"
"arrival_airport": "code"
"departure_date": "YYYY-MM-DD"
"departure_time": "HH:MM"
"arrival_time": "HH:MM"
"gate": "gate number if shown"
"seat": "seat assignment if shown"
"boarding_group": "boarding group/zone"
"passenger_name": "name on booking"
"activity_name": "activity/tour/experience name"
"activity_date": "YYYY-MM-DD"
"activity_time": "HH:MM"
"activity_duration": "e.g. 2 hours"
"venue": "venue/restaurant name"
"restaurant_name": "restaurant name"
"party_size": number of diners
"special_instructions": "any check-in instructions, door codes, luggage rules, etc."
"house_rules": "quiet hours, max guests, etc."
"notes": "any other important details (gate changes, delays, special requests, dietary notes)"
"has_attachment": true if email has PDF/image attachments worth saving
"cancelled_property": "name of cancelled property if this is a cancellation"
"cancelled_confirmation": "confirmation # of cancelled booking"
```

IMPORTANT RULES:
- Only include fields clearly stated in the email. Do not guess.
- Boarding passes: extract gate, seat, boarding group, and departure time. Set type="boarding_pass".
- Restaurant reservations: extract restaurant_name, activity_date, activity_time, party_size, address. Set type="restaurant".
- Activity/experience bookings: extract activity_name, activity_date, activity_time, venue, confirmation_number. Set type="activity_ticket".
- If the email has PDF attachments (boarding pass PDFs, tickets, receipts), set "has_attachment": true.
- Match flights by flight number (DL5392, DL275, UA876, UA1470) when possible.
- Match accommodations by confirmation number or property name.
- Return valid JSON only. If not travel-related: {"type": "other", "action": "info"}

EMAIL SUBJECT: <<SUBJECT>>
EMAIL FROM: <<SENDER>>
EMAIL DATE: <<DATE>>

EMAIL BODY:
<<BODY>>"""


def get_gmail_credentials(app=None):
    """Get Gmail API credentials from token storage.

    Priority:
    1. Persistent volume token file (Railway production)
    2. GMAIL_TOKEN_JSON env var (initial Railway setup)
    3. Local config file (~/.config/japan-travel-app/token.json)
    """
    creds = None
    token_paths = []

    # Railway persistent volume
    vol = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH')
    if vol:
        token_paths.append(os.path.join(vol, 'gmail_token.json'))

    # Local config
    token_paths.append(os.path.join(os.path.expanduser('~'),
                                     '.config', 'japan-travel-app', 'token.json'))

    # Try loading from file
    for path in token_paths:
        if os.path.exists(path):
            try:
                creds = Credentials.from_authorized_user_file(path, SCOPES)
                break
            except Exception:
                continue

    # Try env var (initial Railway setup)
    if not creds:
        token_json = os.environ.get('GMAIL_TOKEN_JSON')
        if token_json:
            try:
                creds = Credentials.from_authorized_user_info(
                    json.loads(token_json), SCOPES)
            except Exception:
                pass

    if not creds:
        return None

    # Refresh if expired
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleRequest())
            # Save refreshed token back
            _save_token(creds, token_paths[0])
        except Exception:
            return None

    return creds


def _save_token(creds, path):
    """Save credentials to file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(creds.to_json())


def get_gmail_service(app=None):
    """Build Gmail API service client."""
    creds = get_gmail_credentials(app)
    if not creds:
        return None
    return build_gmail('gmail', 'v1', credentials=creds)


def search_travel_emails(service, since_message_id=None, custom_query=None):
    """Search Gmail for travel-related emails.

    Returns list of {id, subject, from, date, snippet, has_attachments}.
    Deduplicates across multiple query results.
    """
    seen_ids = set()
    results = []

    queries = [custom_query] if custom_query else TRAVEL_QUERIES
    for query in queries:
        try:
            resp = service.users().messages().list(
                userId='me', q=query, maxResults=50).execute()
            for m in resp.get('messages', []):
                if m['id'] not in seen_ids:
                    seen_ids.add(m['id'])
                    results.append(m)
        except Exception:
            continue

    return results


def fetch_email_content(service, msg_id):
    """Fetch full email content including body text."""
    msg = service.users().messages().get(
        userId='me', id=msg_id, format='full').execute()

    headers = {h['name']: h['value']
               for h in msg['payload'].get('headers', [])}

    body = _extract_body(msg['payload'])
    attachments = _find_attachments(msg['payload'], msg_id)

    return {
        'id': msg_id,
        'subject': headers.get('Subject', '(no subject)'),
        'from': headers.get('From', ''),
        'date': headers.get('Date', ''),
        'internal_date': msg.get('internalDate', ''),
        'snippet': msg.get('snippet', ''),
        'body': body or '',
        'attachments': attachments,
    }


def _extract_body(payload):
    """Recursively extract text body from email payload."""
    if payload.get('body', {}).get('data'):
        mime = payload.get('mimeType', '')
        if 'text/plain' in mime or not payload.get('parts'):
            return base64.urlsafe_b64decode(
                payload['body']['data']).decode('utf-8', errors='replace')

    for part in payload.get('parts', []):
        if part.get('mimeType') == 'text/plain' and part.get('body', {}).get('data'):
            return base64.urlsafe_b64decode(
                part['body']['data']).decode('utf-8', errors='replace')
        result = _extract_body(part)
        if result:
            return result
    return None


def _find_attachments(payload, msg_id):
    """Recursively find attachments."""
    attachments = []
    if payload.get('filename') and payload.get('body', {}).get('attachmentId'):
        attachments.append({
            'filename': payload['filename'],
            'attachment_id': payload['body']['attachmentId'],
            'mime_type': payload.get('mimeType', ''),
            'size': payload.get('body', {}).get('size', 0),
        })
    for part in payload.get('parts', []):
        attachments.extend(_find_attachments(part, msg_id))
    return attachments


def extract_booking_data(email_content, api_key):
    """Use Claude API to extract structured booking data from email.

    Returns parsed JSON dict or None on failure.
    """
    import anthropic

    # Clean body — remove excessive whitespace from HTML emails
    body = email_content.get('body', '')
    # Strip invisible chars and collapse whitespace
    body = re.sub(r'[\u200b\u00ad\u034f]+', '', body)
    body = re.sub(r'\s*\u034f\s*', '', body)
    body = re.sub(r'\n\s*\n\s*\n+', '\n\n', body)
    # Truncate very long emails
    if len(body) > 8000:
        body = body[:8000] + '\n...[truncated]'

    prompt = (EXTRACTION_PROMPT_TEMPLATE
              .replace('<<SUBJECT>>', email_content.get('subject', ''))
              .replace('<<SENDER>>', email_content.get('from', ''))
              .replace('<<DATE>>', email_content.get('date', ''))
              .replace('<<BODY>>', body))

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=2048,
            messages=[{'role': 'user', 'content': prompt}],
        )
        text = response.content[0].text

        # Extract JSON from response (handle markdown code blocks)
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1))
        # Try parsing the whole response as JSON
        return json.loads(text)
    except Exception:
        return None


def diff_against_db(extracted, db_state):
    """Compare extracted email data against current DB state.

    Returns a list of proposed changes, each being a dict:
    {
        'change_type': 'create' | 'update' | 'cancel',
        'entity_type': 'accommodation' | 'flight' | 'activity' | 'transport',
        'entity_id': int or None,
        'description': 'human-readable summary',
        'fields': {field: new_value, ...},
        'current': {field: current_value, ...},
    }
    """
    if not extracted:
        return []

    changes = []
    etype = extracted.get('type', 'other')
    action = extracted.get('action', 'info')

    if etype == 'cancellation' or action == 'cancellation':
        cancelled_name = extracted.get('cancelled_property', '')
        cancelled_conf = extracted.get('cancelled_confirmation', '')
        if cancelled_name or cancelled_conf:
            # Find matching accommodation
            for opt in db_state.get('accommodations', []):
                name_match = (cancelled_name and
                              cancelled_name.lower() in opt['name'].lower())
                conf_match = (cancelled_conf and
                              opt.get('confirmation_number') == cancelled_conf)
                if name_match or conf_match:
                    if opt['booking_status'] != 'cancelled':
                        changes.append({
                            'change_type': 'cancel',
                            'entity_type': 'accommodation',
                            'entity_id': opt['id'],
                            'description': f"Cancel {opt['name']} (was {opt['booking_status']})",
                            'fields': {'booking_status': 'cancelled'},
                            'current': {'booking_status': opt['booking_status']},
                        })
        return changes

    if etype == 'accommodation':
        prop_name = extracted.get('property_name', '')
        conf_num = extracted.get('confirmation_number', '')
        city = extracted.get('city', '')

        # Try to match existing option
        matched = None
        for opt in db_state.get('accommodations', []):
            if conf_num and opt.get('confirmation_number') == conf_num:
                matched = opt
                break
            if prop_name and prop_name.lower() in opt['name'].lower():
                matched = opt
                break

        if matched:
            # Build update fields
            update_fields = {}
            current = {}
            field_map = {
                'confirmation_number': 'confirmation_number',
                'address': 'address',
                'check_in_time': 'check_in_info',
                'check_out_time': 'check_out_info',
                'host_phone': 'phone',
            }
            for src, dst in field_map.items():
                val = extracted.get(src)
                if val and val != matched.get(dst):
                    update_fields[dst] = val
                    current[dst] = matched.get(dst)

            # Special instructions go to user_notes
            instructions = extracted.get('special_instructions', '')
            house_rules = extracted.get('house_rules', '')
            notes_parts = [p for p in [instructions, house_rules] if p]
            if notes_parts:
                new_notes = ' | '.join(notes_parts)
                if new_notes != matched.get('user_notes'):
                    update_fields['user_notes'] = new_notes
                    current['user_notes'] = matched.get('user_notes')

            if update_fields:
                changes.append({
                    'change_type': 'update',
                    'entity_type': 'accommodation',
                    'entity_id': matched['id'],
                    'description': f"Update {matched['name']}: {', '.join(update_fields.keys())}",
                    'fields': update_fields,
                    'current': current,
                })

            # If not yet selected/booked, propose selection
            if not matched.get('is_selected') and action in ('new_booking', 'confirmation'):
                changes.append({
                    'change_type': 'update',
                    'entity_type': 'accommodation',
                    'entity_id': matched['id'],
                    'description': f"Select {matched['name']} as chosen stay & set to booked",
                    'fields': {'is_selected': True, 'booking_status': 'booked'},
                    'current': {
                        'is_selected': matched.get('is_selected'),
                        'booking_status': matched.get('booking_status'),
                    },
                })
        else:
            # New accommodation not in DB
            if prop_name and city:
                changes.append({
                    'change_type': 'create',
                    'entity_type': 'accommodation',
                    'entity_id': None,
                    'description': f"New accommodation: {prop_name} in {city}",
                    'fields': {
                        'name': prop_name,
                        'city': city,
                        'confirmation_number': conf_num,
                        'check_in_date': extracted.get('check_in_date'),
                        'check_out_date': extracted.get('check_out_date'),
                        'check_in_info': extracted.get('check_in_time'),
                        'check_out_info': extracted.get('check_out_time'),
                        'address': extracted.get('address'),
                        'phone': extracted.get('host_phone'),
                    },
                    'current': {},
                })

    elif etype == 'flight':
        flight_num = extracted.get('flight_number', '')
        conf_num = extracted.get('confirmation_number', '')

        for fl in db_state.get('flights', []):
            if flight_num and flight_num.upper() in fl['flight_number'].upper():
                update_fields = {}
                current = {}
                if conf_num and conf_num != fl.get('confirmation_number'):
                    update_fields['confirmation_number'] = conf_num
                    current['confirmation_number'] = fl.get('confirmation_number')
                dep_time = extracted.get('departure_time')
                if dep_time and dep_time != fl.get('depart_time'):
                    update_fields['depart_time'] = dep_time
                    current['depart_time'] = fl.get('depart_time')
                arr_time = extracted.get('arrival_time')
                if arr_time and arr_time != fl.get('arrive_time'):
                    update_fields['arrive_time'] = arr_time
                    current['arrive_time'] = fl.get('arrive_time')

                if update_fields:
                    changes.append({
                        'change_type': 'update',
                        'entity_type': 'flight',
                        'entity_id': fl['id'],
                        'description': f"Update flight {fl['flight_number']}: {', '.join(update_fields.keys())}",
                        'fields': update_fields,
                        'current': current,
                    })

    elif etype == 'boarding_pass':
        flight_num = extracted.get('flight_number', '')
        for fl in db_state.get('flights', []):
            if flight_num and flight_num.upper() in fl['flight_number'].upper():
                # Build notes with boarding pass details
                bp_parts = []
                if extracted.get('gate'):
                    bp_parts.append(f"Gate: {extracted['gate']}")
                if extracted.get('seat'):
                    bp_parts.append(f"Seat: {extracted['seat']}")
                if extracted.get('boarding_group'):
                    bp_parts.append(f"Group: {extracted['boarding_group']}")
                if extracted.get('departure_time'):
                    bp_parts.append(f"Departs: {extracted['departure_time']}")
                bp_info = ' | '.join(bp_parts) if bp_parts else 'Boarding pass received'

                changes.append({
                    'change_type': 'update',
                    'entity_type': 'flight',
                    'entity_id': fl['id'],
                    'description': f"Boarding pass for {fl['flight_number']}: {bp_info}",
                    'fields': {
                        'notes': bp_info,
                        **(({'depart_time': extracted['departure_time']}
                            if extracted.get('departure_time') and
                            extracted['departure_time'] != fl.get('depart_time') else {})),
                    },
                    'current': {'notes': fl.get('notes', '')},
                })
                # Flag that attachments should be saved
                if extracted.get('has_attachment'):
                    changes.append({
                        'change_type': 'upload',
                        'entity_type': 'document',
                        'entity_id': fl['id'],
                        'description': f"Save boarding pass PDF for {fl['flight_number']}",
                        'fields': {'doc_type': 'boarding_pass', 'linked_flight_id': fl['id']},
                        'current': {},
                    })
                break

    elif etype == 'restaurant':
        restaurant_name = extracted.get('restaurant_name') or extracted.get('venue', '')
        activity_date = extracted.get('activity_date', '')
        activity_time = extracted.get('activity_time', '')
        party_size = extracted.get('party_size', '')
        if restaurant_name:
            notes_parts = []
            if extracted.get('confirmation_number'):
                notes_parts.append(f"Conf: {extracted['confirmation_number']}")
            if party_size:
                notes_parts.append(f"Party of {party_size}")
            if extracted.get('notes'):
                notes_parts.append(extracted['notes'])
            changes.append({
                'change_type': 'create',
                'entity_type': 'activity',
                'entity_id': None,
                'description': f"Restaurant reservation: {restaurant_name} on {activity_date or '?'} at {activity_time or '?'}",
                'fields': {
                    'title': f"Dinner: {restaurant_name}" if not restaurant_name.startswith('Dinner') else restaurant_name,
                    'date': activity_date,
                    'time': activity_time,
                    'category': 'food',
                    'address': extracted.get('address'),
                    'venue': restaurant_name,
                    'confirmation_number': extracted.get('confirmation_number'),
                    'notes': ' | '.join(notes_parts) if notes_parts else None,
                    'book_ahead': True,
                    'book_ahead_note': f"Reserved via {extracted.get('platform', 'restaurant')}"
                                       + (f", conf {extracted['confirmation_number']}" if extracted.get('confirmation_number') else ''),
                },
                'current': {},
            })

    elif etype == 'activity_ticket':
        activity_name = extracted.get('activity_name', '')
        activity_date = extracted.get('activity_date', '')
        if activity_name:
            notes_parts = []
            if extracted.get('confirmation_number'):
                notes_parts.append(f"Conf: {extracted['confirmation_number']}")
            if extracted.get('activity_duration'):
                notes_parts.append(f"Duration: {extracted['activity_duration']}")
            if extracted.get('notes'):
                notes_parts.append(extracted['notes'])
            changes.append({
                'change_type': 'create',
                'entity_type': 'activity',
                'entity_id': None,
                'description': f"New activity: {activity_name} on {activity_date or '?'}",
                'fields': {
                    'title': activity_name,
                    'date': activity_date,
                    'time': extracted.get('activity_time'),
                    'address': extracted.get('address'),
                    'venue': extracted.get('venue'),
                    'confirmation_number': extracted.get('confirmation_number'),
                    'notes': ' | '.join(notes_parts) if notes_parts else None,
                    'book_ahead': True,
                    'book_ahead_note': f"Booked via {extracted.get('platform', 'online')}"
                                       + (f", conf {extracted['confirmation_number']}" if extracted.get('confirmation_number') else ''),
                },
                'current': {},
            })

    elif etype == 'transport_ticket':
        notes_parts = []
        if extracted.get('confirmation_number'):
            notes_parts.append(f"Conf: {extracted['confirmation_number']}")
        if extracted.get('notes'):
            notes_parts.append(extracted['notes'])
        train_name = extracted.get('activity_name') or extracted.get('notes', '')
        changes.append({
            'change_type': 'create',
            'entity_type': 'transport',
            'entity_id': None,
            'description': f"Transport ticket: {train_name or 'train'} on {extracted.get('activity_date', '?')}",
            'fields': {
                'name': train_name,
                'date': extracted.get('activity_date'),
                'time': extracted.get('activity_time'),
                'confirmation_number': extracted.get('confirmation_number'),
                'notes': ' | '.join(notes_parts) if notes_parts else None,
            },
            'current': {},
        })

    return changes


def download_and_upload_attachments(service, content, extracted, app):
    """Download email attachments and upload them to the app's document system.

    Returns list of uploaded document IDs.
    """
    if not extracted.get('has_attachment') or not content.get('attachments'):
        return []

    from models import db, Document
    import uuid
    from werkzeug.utils import secure_filename

    uploaded_ids = []
    docs_folder = os.path.join(app.config.get('UPLOAD_FOLDER', 'uploads'), 'documents')
    os.makedirs(docs_folder, exist_ok=True)

    # Determine doc_type from extracted type
    type_map = {
        'boarding_pass': 'flight_receipt',
        'flight': 'flight_receipt',
        'accommodation': 'accommodation_booking',
        'restaurant': 'activity_ticket',
        'activity_ticket': 'activity_ticket',
        'transport_ticket': 'transport_ticket',
    }
    doc_type = type_map.get(extracted.get('type', ''), 'other')

    for att in content['attachments']:
        fname = att.get('filename', '')
        # Only download PDFs and images
        ext = fname.rsplit('.', 1)[-1].lower() if '.' in fname else ''
        if ext not in ('pdf', 'png', 'jpg', 'jpeg', 'webp'):
            continue

        try:
            result = service.users().messages().attachments().get(
                userId='me',
                messageId=content['id'],
                id=att['attachment_id'],
            ).execute()
            data = base64.urlsafe_b64decode(result['data'])

            safe_name = secure_filename(fname)
            unique_name = f"{uuid.uuid4().hex[:8]}__{safe_name}"
            filepath = os.path.join(docs_folder, unique_name)

            with open(filepath, 'wb') as f:
                f.write(data)

            doc = Document(
                filename=unique_name,
                original_name=safe_name,
                file_type=ext,
                file_size=len(data),
                doc_type=doc_type,
                notes=f"Auto-imported from Gmail: {content.get('subject', '')[:200]}",
            )
            db.session.add(doc)
            db.session.commit()
            uploaded_ids.append(doc.id)
        except Exception:
            continue

    return uploaded_ids


def get_db_state():
    """Get current DB state for diffing. Must be called in app context."""
    from models import AccommodationOption, AccommodationLocation, Flight

    accommodations = []
    for opt in AccommodationOption.query.all():
        loc = AccommodationLocation.query.get(opt.location_id)
        accommodations.append({
            'id': opt.id,
            'name': opt.name,
            'location_name': loc.location_name if loc else '',
            'is_selected': opt.is_selected,
            'is_eliminated': opt.is_eliminated,
            'booking_status': opt.booking_status,
            'confirmation_number': opt.confirmation_number,
            'address': opt.address,
            'check_in_info': opt.check_in_info,
            'check_out_info': opt.check_out_info,
            'phone': opt.phone,
            'user_notes': opt.user_notes,
            'document_id': opt.document_id,
        })

    flights = []
    for fl in Flight.query.all():
        flights.append({
            'id': fl.id,
            'flight_number': fl.flight_number,
            'airline': fl.airline,
            'booking_status': fl.booking_status,
            'confirmation_number': fl.confirmation_number,
            'depart_time': fl.depart_time,
            'arrive_time': fl.arrive_time,
            'notes': fl.notes,
            'document_id': fl.document_id,
        })

    return {'accommodations': accommodations, 'flights': flights}


def run_sync(app):
    """Run a full Gmail sync cycle. Returns sync result dict.

    Must be called with app context available.
    """
    from models import db, GmailSyncLog, PendingGmailChange

    with app.app_context():
        api_key = app.config.get('ANTHROPIC_API_KEY', '')
        service = get_gmail_service(app)
        if not service:
            return {'ok': False, 'error': 'Gmail not connected. Check credentials.'}

        log = GmailSyncLog(started_at=datetime.utcnow())
        db.session.add(log)
        db.session.commit()

        try:
            # Get already-processed email IDs
            existing_email_ids = {
                c.gmail_message_id
                for c in PendingGmailChange.query.with_entities(
                    PendingGmailChange.gmail_message_id).all()
            }

            # Search for emails
            email_stubs = search_travel_emails(service)
            log.emails_found = len(email_stubs)

            # Filter to unprocessed
            new_stubs = [s for s in email_stubs
                         if s['id'] not in existing_email_ids]

            db_state = get_db_state()
            changes_created = 0

            for stub in new_stubs:
                try:
                    content = fetch_email_content(service, stub['id'])
                    extracted = extract_booking_data(content, api_key)

                    if not extracted or extracted.get('type') == 'other':
                        # Still record that we processed this email
                        skip = PendingGmailChange(
                            gmail_message_id=stub['id'],
                            email_subject=content.get('subject', '')[:500],
                            email_from=content.get('from', '')[:200],
                            email_date=content.get('date', '')[:100],
                            change_type='none',
                            entity_type='other',
                            description='Not a travel booking email',
                            proposed_data=json.dumps(extracted or {}),
                            status='skipped',
                        )
                        db.session.add(skip)
                        continue

                    # Download and upload attachments if the email has them
                    uploaded_doc_ids = []
                    if extracted.get('has_attachment') and content.get('attachments'):
                        uploaded_doc_ids = download_and_upload_attachments(
                            service, content, extracted, app)

                    proposed = diff_against_db(extracted, db_state)

                    # For upload-type changes, attach the uploaded doc IDs
                    for change in proposed:
                        if change.get('change_type') == 'upload' and uploaded_doc_ids:
                            change['fields']['uploaded_doc_ids'] = uploaded_doc_ids

                    if not proposed:
                        # Email is travel-related but no changes needed
                        skip = PendingGmailChange(
                            gmail_message_id=stub['id'],
                            email_subject=content.get('subject', '')[:500],
                            email_from=content.get('from', '')[:200],
                            email_date=content.get('date', '')[:100],
                            change_type='none',
                            entity_type=extracted.get('type', 'other'),
                            description='No changes needed — data already up to date',
                            proposed_data=json.dumps(extracted),
                            status='skipped',
                        )
                        db.session.add(skip)
                        continue

                    for change in proposed:
                        pending = PendingGmailChange(
                            gmail_message_id=stub['id'],
                            email_subject=content.get('subject', '')[:500],
                            email_from=content.get('from', '')[:200],
                            email_date=content.get('date', '')[:100],
                            change_type=change['change_type'],
                            entity_type=change['entity_type'],
                            entity_id=change.get('entity_id'),
                            description=change['description'],
                            proposed_data=json.dumps(change['fields']),
                            current_data=json.dumps(change.get('current', {})),
                            status='pending',
                        )
                        db.session.add(pending)
                        changes_created += 1

                except Exception as e:
                    log.errors = (log.errors or '') + f"\n{stub['id']}: {str(e)}"

            log.changes_detected = changes_created
            log.completed_at = datetime.utcnow()
            log.status = 'completed'
            db.session.commit()

            return {
                'ok': True,
                'emails_found': log.emails_found,
                'new_emails': len(new_stubs),
                'changes_proposed': changes_created,
                'sync_id': log.id,
            }

        except Exception as e:
            log.status = 'failed'
            log.errors = str(e)
            log.completed_at = datetime.utcnow()
            db.session.commit()
            return {'ok': False, 'error': str(e)}


def apply_change(change_id):
    """Apply an approved pending change to the DB.

    Must be called in app context.
    """
    from models import db, PendingGmailChange, AccommodationOption, Flight
    import services.accommodations as accom_svc
    import services.flights as flight_svc

    change = PendingGmailChange.query.get(change_id)
    if not change or change.status != 'pending':
        return {'ok': False, 'error': 'Change not found or not pending'}

    fields = json.loads(change.proposed_data)

    try:
        if change.entity_type == 'accommodation' and change.entity_id:
            opt = AccommodationOption.query.get(change.entity_id)
            if not opt:
                change.status = 'failed'
                db.session.commit()
                return {'ok': False, 'error': 'Accommodation not found'}

            if change.change_type == 'cancel':
                opt.booking_status = 'cancelled'
                if opt.is_selected:
                    opt.is_selected = False
                db.session.commit()
            elif change.change_type == 'update':
                # Handle selection separately
                if 'is_selected' in fields:
                    if fields.pop('is_selected'):
                        accom_svc.select(opt.id)
                if 'booking_status' in fields:
                    bs = fields.pop('booking_status')
                    accom_svc.update_status(opt.id, {'booking_status': bs})
                # Apply remaining fields directly
                for k, v in fields.items():
                    if hasattr(opt, k):
                        setattr(opt, k, v)
                db.session.commit()

        elif change.entity_type == 'flight' and change.entity_id:
            flight = Flight.query.get(change.entity_id)
            if not flight:
                change.status = 'failed'
                db.session.commit()
                return {'ok': False, 'error': 'Flight not found'}
            flight_svc.update(flight.id, fields)

        change.status = 'approved'
        change.reviewed_at = datetime.utcnow()
        db.session.commit()

        from extensions import socketio
        if change.entity_type == 'accommodation':
            socketio.emit('accommodation_updated', {})
        elif change.entity_type == 'flight':
            socketio.emit('flight_updated', {})

        return {'ok': True, 'message': change.description}

    except Exception as e:
        change.status = 'failed'
        change.errors = str(e)
        db.session.commit()
        return {'ok': False, 'error': str(e)}
