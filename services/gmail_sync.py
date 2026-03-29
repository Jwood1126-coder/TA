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
    # Flights
    'subject:(flight receipt OR eTicket OR itinerary) (Delta OR United OR "DL275" OR "DL5392" OR "UA876" OR "UA1470" OR "HBPF75" OR "I91ZHJ") after:2025/06/01',
    # Accommodations
    'subject:(booking confirmation OR reservation confirmed OR "your receipt") (Agoda OR Airbnb) after:2025/06/01',
    # Broader travel bookings
    'subject:(booking OR confirmation OR reservation OR receipt) (hotel OR ryokan OR hostel OR machiya) after:2025/06/01',
    # Specific known bookings
    '"Sotetsu Fresa" OR "TAKANOYU" OR "Tsukiya-Mikazuki" OR "Kyotofish" OR "Leben Osaka" OR "KumoMachiya" after:2025/06/01',
    # Cancellations
    'subject:(cancelled OR canceled OR cancellation) (airbnb OR agoda OR hotel OR flight) after:2025/06/01',
    # JR Pass, activities, tickets
    'subject:(order confirmation OR booking confirmation OR e-ticket) (JR Pass OR Japan Rail OR klook OR viator OR GetYourGuide) after:2025/06/01',
    # Train reservations
    'subject:(reservation OR seat reservation OR ticket) (shinkansen OR "bullet train" OR "japan rail" OR JR) after:2025/06/01',
]

# Extraction prompt for Claude
EXTRACTION_PROMPT_TEMPLATE = """You are a travel booking data extractor. Analyze this email and extract structured booking data.

Return a JSON object with these fields (omit fields that aren't present):

```
"type": "accommodation" | "flight" | "cancellation" | "activity_ticket" | "transport_ticket" | "other"
"action": "new_booking" | "update" | "cancellation" | "confirmation" | "info"
"property_name": "hotel/property name"
"confirmation_number": "booking reference"
"platform": "Airbnb" | "Agoda" | "Booking.com" | "Priceline" | etc
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
"passenger_name": "name on booking"
"activity_name": "activity/tour name"
"activity_date": "YYYY-MM-DD"
"activity_time": "HH:MM"
"venue": "venue name"
"special_instructions": "any check-in instructions, door codes, luggage rules, etc."
"house_rules": "quiet hours, max guests, etc."
"notes": "any other important details"
"cancelled_property": "name of cancelled property if this is a cancellation"
"cancelled_confirmation": "confirmation # of cancelled booking"
```

Only include fields that are clearly stated in the email. Do not guess or infer missing data.
Return valid JSON only. If this email is not travel-related, return: {"type": "other", "action": "info"}

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

    elif etype == 'activity_ticket':
        activity_name = extracted.get('activity_name', '')
        activity_date = extracted.get('activity_date', '')
        if activity_name:
            changes.append({
                'change_type': 'create',
                'entity_type': 'activity',
                'entity_id': None,
                'description': f"New ticketed activity: {activity_name} on {activity_date or '?'}",
                'fields': {
                    'title': activity_name,
                    'date': activity_date,
                    'time': extracted.get('activity_time'),
                    'venue': extracted.get('venue'),
                    'confirmation_number': extracted.get('confirmation_number'),
                    'notes': extracted.get('notes'),
                },
                'current': {},
            })

    return changes


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

                    proposed = diff_against_db(extracted, db_state)

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
