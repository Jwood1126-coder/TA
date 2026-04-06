"""Gmail sync service — two-stage pipeline for syncing travel bookings from Gmail.

Stage 1 (Haiku): Fast, cheap extraction of structured data from each email.
Stage 2 (Opus): Intelligent analysis of all extractions as a batch — deduplication,
    geographic filtering, supplementary info consolidation, and clear consequence mapping.

Proposed changes are stored for user review before applying.
"""
import base64
import json
import os
import re
from datetime import datetime

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build as build_gmail

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

# Hard geographic filter — only these cities are valid for this trip
JAPAN_TRIP_CITIES = {
    'tokyo', 'shinjuku', 'shibuya', 'asakusa', 'akihabara', 'ginza', 'roppongi', 'toyosu', 'odaiba', 'azabudai',
    'takayama', 'shirakawa-go', 'shirakawa',
    'kanazawa',
    'kyoto', 'gion', 'arashiyama', 'fushimi',
    'osaka', 'dotonbori', 'namba', 'umeda', 'shinsekai',
    'hakone', 'hiroshima', 'miyajima',
    'narita', 'haneda',
    # Allow blanks (flights, general Japan emails)
    'japan', '',
}

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
    # Airbnb — catch ALL booking-related emails (confirmations, reminders, check-in, host messages, receipts)
    'from:airbnb.com subject:(reservation OR confirmed OR confirmation OR receipt OR "your trip" OR "check-in" OR "getting ready" OR "your stay" OR "your home") after:2025/06/01',
    'from:airbnb.com subject:(itinerary OR "door code" OR "house rules" OR "house manual" OR "directions" OR "arriving" OR "welcome") after:2025/06/01',
    # Airbnb host messages (often contain check-in instructions, wifi, door codes)
    'from:airbnb.com subject:(message from OR "sent you a message" OR "new message") after:2025/06/01',
    # Specific known bookings
    '"Sotetsu Fresa" OR "TAKANOYU" OR "Tsukiya-Mikazuki" OR "Kyotofish" OR "Leben Osaka" OR "KumoMachiya" OR "Kumo Machiya" after:2025/06/01',
    # Cancellations
    'subject:(cancelled OR canceled OR cancellation) (airbnb OR agoda OR hotel OR flight) after:2025/06/01',
    # JR Pass, activities, tickets
    'subject:(order confirmation OR booking confirmation OR e-ticket) (JR Pass OR "Japan Rail" OR klook OR viator OR GetYourGuide) after:2025/06/01',
    # Train reservations
    'subject:(reservation OR "seat reservation" OR ticket) (shinkansen OR "bullet train" OR "japan rail" OR JR) after:2025/06/01',
    # Restaurant reservations — Japan-specific only
    'subject:(reservation OR booking OR "your table") (restaurant OR omakase OR izakaya OR ramen OR sushi OR kaiseki OR yakitori) Japan after:2025/06/01',
    '(from:tablecheck OR from:tabelog OR from:toreta) subject:(reservation OR confirmation) after:2025/06/01',
    # Activity & experience bookings
    '(from:klook OR from:viator OR from:getyourguide OR from:airbnb.com) subject:(experience OR activity OR tour OR ticket OR booking) Japan after:2025/06/01',
    'subject:(order confirmation OR booking confirmation OR e-ticket) (tea ceremony OR kimono OR cooking class OR sumo OR shrine OR temple OR onsen) after:2025/06/01',
    # General Japan travel
    'subject:(confirmation OR reservation OR receipt OR ticket) Japan (Tokyo OR Kyoto OR Osaka OR Takayama OR Hiroshima OR Hakone OR Miyajima) after:2025/06/01',
    # --- Forwarded emails from family (Jessica forwards bookings she makes) ---
    'from:jsa.259@gmail.com subject:(fwd OR forwarded OR ticket OR booking OR confirmation OR order) after:2025/06/01',
    'from:jsa.259@gmail.com (teamlab OR teamLab OR DMM OR ticket OR booking OR reservation OR confirmed) after:2025/06/01',
    # --- DMM / teamLab platform emails (tickets bought via dmm.com) ---
    'from:dmm.com (teamlab OR teamLab OR ticket OR order OR admission OR entry) after:2025/06/01',
    'from:mail.dmm.com after:2025/06/01',
    'subject:(teamlab OR teamLab OR "team lab") after:2025/06/01',
    # --- Broader activity/ticket platforms not yet covered ---
    'subject:(admission OR "QR ticket" OR "entry time" OR "e-ticket" OR "mobile ticket") (Tokyo OR Kyoto OR Osaka OR Japan) after:2025/06/01',
    'subject:(order confirmation OR purchase confirmation) (museum OR exhibit OR art OR aquarium OR zoo OR park OR garden OR tower OR observation) Japan after:2025/06/01',
    # --- Catch forwarded booking emails with common Fwd: patterns ---
    'subject:(fwd OR forwarded) (booking OR confirmation OR reservation OR ticket OR receipt) (Japan OR Tokyo OR Kyoto OR Osaka) after:2025/06/01',
]

# Stage 1: Haiku extraction prompt (per-email, fast/cheap)
EXTRACTION_PROMPT = """You are a travel booking data extractor for a Japan trip (April 5-18, 2026).
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
"action": "new_booking" | "update" | "cancellation" | "confirmation" | "check_in" | "boarding_pass" | "info" | "supplementary"
"property_name": "hotel/property name"
"confirmation_number": "booking reference"
"platform": "Airbnb" | "Agoda" | "Delta" | "United" | etc
"check_in_date": "YYYY-MM-DD"
"check_out_date": "YYYY-MM-DD"
"check_in_time": "e.g. 4:00 PM"
"check_out_time": "e.g. 11:00 AM"
"address": "full address"
"city": "city name"
"country": "country name"
"guests": number
"price_total": number
"price_per_night": number
"currency": "USD" or "JPY"
"host_name": "host name if applicable"
"host_phone": "phone if provided"
"wifi_info": "wifi network/password if provided"
"door_code": "access code if provided"
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
"neighborhood_tips": "nearby stations, restaurants, convenience stores mentioned by host"
"notes": "any other important details"
"has_attachment": true if email has PDF/image attachments worth saving
"cancelled_property": "name of cancelled property if this is a cancellation"
"cancelled_confirmation": "confirmation # of cancelled booking"
```

IMPORTANT RULES:
- Only include fields clearly stated in the email. Do not guess.
- If the email contains helpful info about an EXISTING booking (check-in instructions, house rules, neighborhood guide, wifi info, door codes), set action="supplementary".
- Boarding passes: extract gate, seat, boarding group, and departure time. Set type="boarding_pass".
- Restaurant reservations: extract restaurant_name, activity_date, activity_time, party_size, address, city, country. Set type="restaurant".
- Activity/experience bookings: extract activity_name, activity_date, activity_time, venue, confirmation_number. Set type="activity_ticket".
- If the email has PDF attachments (boarding pass PDFs, tickets, receipts), set "has_attachment": true.
- Match flights by flight number (DL5392, DL275, UA876, UA1470) when possible.
- Match accommodations by confirmation number or property name.
- ALWAYS include "city" and "country" fields when they can be determined from the email.
- Return valid JSON only. If not travel-related: {"type": "other", "action": "info"}

FORWARDED EMAILS:
- Emails may be FORWARDED by a family member (e.g. Jessica / jsa.259@gmail.com). The actual booking info is in the forwarded content below the "Forwarded message" header.
- Look past the forwarding header and extract from the ORIGINAL email body.
- For forwarded booking confirmations, treat them exactly like direct booking emails — extract all fields normally.
- The "from" field may show the forwarder, but the REAL sender is in the forwarded headers.

ACTIVITY TICKETS & MUSEUM ENTRIES:
- Platforms like DMM (dmm.com), teamLab, Klook, Viator send booking confirmations with QR codes and entry times.
- Extract: activity_name, activity_date, activity_time (entry window), venue, address, confirmation_number/inquiry number.
- For teamLab specifically: note which venue (Planets TOKYO in Toyosu vs Borderless in Azabudai) and the entry time window.
- Set type="activity_ticket", action="new_booking".

EMAIL SUBJECT: <<SUBJECT>>
EMAIL FROM: <<SENDER>>
EMAIL DATE: <<DATE>>

EMAIL BODY:
<<BODY>>"""


# Stage 2: Opus analyst prompt (batch analysis, one call per sync)
OPUS_ANALYST_PROMPT = """You are an expert travel analyst for a Japan trip planning app. You've been given a batch of extracted email data (from a fast first-pass scan) along with the current database state.

Your job is to produce a CURATED, DEDUPLICATED list of meaningful changes to propose to the user.

## THE TRIP
- **Dates:** April 5-18, 2026 (14 days, fly in Apr 5, fly out Apr 18)
- **Travelers:** 2 people
- **Route:** Cleveland → Tokyo → Takayama → Kyoto → Osaka → Cleveland

## CONFIRMED BOOKINGS (current DB state)
<<DB_STATE>>

## WHAT YOU MUST DO

For each extracted email, decide ONE of:
1. **SKIP** — not relevant to this Japan trip (wrong country, duplicate of already-processed info, marketing email)
2. **UPDATE** — updates an existing record with new/better information
3. **CREATE** — creates a new record (new restaurant reservation, new activity ticket, etc.)
4. **CANCEL** — cancels an existing booking

## CRITICAL RULES

### Geographic Filter
- This is a JAPAN trip. Any booking for a restaurant, hotel, or activity NOT in Japan must be SKIPPED.
- US restaurants (Pier W, etc.), US hotels, non-Japan activities = SKIP with reason "Not related to Japan trip"

### Deduplication
- Multiple emails about the same booking (confirmation + reminder + receipt) = ONE change, not three
- Use confirmation numbers and property names to identify duplicates
- Pick the email with the MOST complete/recent information

### Supplementary Info (VERY IMPORTANT)
- Airbnb/hosts often send follow-up emails with check-in instructions, house rules, wifi passwords, door codes, neighborhood tips
- These are NOT new bookings — they are UPDATES to existing accommodation records
- Map them to the correct accommodation by confirmation number or property name
- Specify exactly which fields to update:
  - check_in_info: check-in time and method (e.g. "3:00 PM, lockbox code 1234")
  - check_out_info: check-out time and instructions
  - user_notes: house rules, wifi info, door codes, neighborhood tips, luggage storage, etc.
  - phone: host phone number
  - address: full address if more specific than what we have
- APPEND to existing user_notes (don't replace) — format as "\\n--- From [platform] [date] ---\\n[new info]"

### Consequence Clarity
For EVERY proposed change, you MUST specify:
- **consequence**: Exactly what happens in the app when the user approves this. Be specific:
  - "Will update Sotetsu Fresa Inn check_in_info from '3:00 PM' to '3:00 PM, self check-in at lobby kiosk'"
  - "Will add new activity 'Tea Ceremony at Camellia Garden' to Day 8 (Apr 12) afternoon slot"
  - "Will cancel TAKANOYU booking and set status to 'cancelled'"
- **confidence**: "high" (exact match on confirmation # or flight #), "medium" (name match, likely correct), "low" (fuzzy match, user should verify)

### What NOT To Change
- NEVER propose changing booking_status to "confirmed" (requires uploaded document — iron rule)
- NEVER propose overriding data from uploaded PDF documents
- NEVER create duplicate activities that already exist in the itinerary
- NEVER modify flight data that came from PDF booking confirmations unless the email has clearly newer info (gate changes, time changes)

## ACTIVITY PLACEMENT
When creating new activities (restaurants, tours, tickets), you must specify:
- **day_id**: Match the activity date to the correct day (Day 1=Apr 5, Day 2=Apr 6, ..., Day 14=Apr 18)
- **time_slot**: morning, afternoon, evening, or night
- **category**: temple, food, nightlife, shopping, nature, culture, transit, logistics, entertainment
- For restaurants, always use category="food" and time_slot based on meal time

## OUTPUT FORMAT
Return a JSON array. Each item:
```json
{
  "action": "skip" | "update" | "create" | "cancel",
  "email_index": 0,
  "reason": "why this action (especially important for skips)",
  "entity_type": "accommodation" | "flight" | "activity" | "transport" | "other",
  "entity_id": 123,
  "description": "short summary shown to user",
  "consequence": "exactly what happens when approved — be specific about field changes",
  "confidence": "high" | "medium" | "low",
  "reasoning": "your analysis of why this is the right action",
  "fields": {
    "field_name": "new_value"
  },
  "current": {
    "field_name": "current_value"
  }
}
```

For "skip" actions, only include: action, email_index, reason.

IMPORTANT: Return ONLY the JSON array. No markdown, no explanation outside the JSON.

## EXTRACTED EMAILS (from first-pass scan)
<<EXTRACTIONS>>"""


def get_gmail_credentials(app=None):
    """Get Gmail API credentials from token storage."""
    creds = None
    token_paths = []

    vol = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH')
    if vol:
        token_paths.append(os.path.join(vol, 'gmail_token.json'))

    token_paths.append(os.path.join(os.path.expanduser('~'),
                                     '.config', 'japan-travel-app', 'token.json'))

    for path in token_paths:
        if os.path.exists(path):
            try:
                creds = Credentials.from_authorized_user_file(path, SCOPES)
                break
            except Exception:
                continue

    if not creds:
        token_json = os.environ.get('GMAIL_TOKEN_JSON')
        if token_json:
            try:
                creds = Credentials.from_authorized_user_info(
                    json.loads(token_json), SCOPES)
            except Exception as e:
                print(f'  Gmail token from env var failed: {e}')

    if not creds:
        return None

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleRequest())
        except Exception as e:
            print(f'  Gmail token refresh failed: {e}')
            # Token refresh failed — try env var as fallback
            creds = None
            token_json = os.environ.get('GMAIL_TOKEN_JSON')
            if token_json:
                try:
                    creds = Credentials.from_authorized_user_info(
                        json.loads(token_json), SCOPES)
                    if creds and creds.expired and creds.refresh_token:
                        creds.refresh(GoogleRequest())
                    print('  Gmail token recovered from env var')
                except Exception as e2:
                    print(f'  Gmail env var fallback also failed: {e2}')
                    return None
            if not creds:
                return None
        # Save refreshed token (best-effort — don't kill creds if save fails)
        try:
            if token_paths:
                _save_token(creds, token_paths[0])
        except Exception as e:
            print(f'  Gmail token save failed (non-fatal): {e}')

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


def search_travel_emails(service, since_date=None, custom_query=None):
    """Search Gmail for travel-related emails.

    Args:
        since_date: Optional date string (YYYY/MM/DD) to narrow queries.
                    Replaces the hardcoded after: dates for incremental syncs.
    """
    seen_ids = set()
    results = []

    queries = [custom_query] if custom_query else TRAVEL_QUERIES
    for query in queries:
        q = query
        # For incremental syncs, replace the hardcoded after: date with a recent one
        if since_date and not custom_query:
            q = re.sub(r'after:\d{4}/\d{2}/\d{2}', f'after:{since_date}', q)
        try:
            resp = service.users().messages().list(
                userId='me', q=q, maxResults=50).execute()
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


# ---------------------------------------------------------------------------
# Stage 1: Haiku extraction (per email)
# ---------------------------------------------------------------------------

def extract_booking_data(email_content, api_key):
    """Use Claude Haiku to extract structured booking data from one email."""
    import anthropic

    body = email_content.get('body', '')
    body = re.sub(r'[\u200b\u00ad\u034f]+', '', body)
    body = re.sub(r'\s*\u034f\s*', '', body)
    body = re.sub(r'\n\s*\n\s*\n+', '\n\n', body)
    if len(body) > 8000:
        body = body[:8000] + '\n...[truncated]'

    prompt = (EXTRACTION_PROMPT
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

        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1))
        return json.loads(text)
    except Exception:
        return None


def _passes_geographic_filter(extracted):
    """Hard geographic filter — reject non-Japan bookings before Opus stage.

    Returns True if the extraction should be passed to Opus for analysis.
    Flights are always passed through (they connect to Japan).
    Bookings with no city are passed through (let Opus decide).
    """
    etype = extracted.get('type', 'other')

    # Always pass flights and boarding passes
    if etype in ('flight', 'boarding_pass', 'cancellation', 'other'):
        return True

    # Check country if available
    country = (extracted.get('country') or '').strip().lower()
    if country and country not in ('japan', 'jp', ''):
        return False

    # Check city
    city = (extracted.get('city') or '').strip().lower()
    if not city:
        return True  # no city info, let Opus decide

    return city in JAPAN_TRIP_CITIES


# ---------------------------------------------------------------------------
# Stage 2: Opus analyst (batch analysis)
# ---------------------------------------------------------------------------

def _format_db_state_for_opus(db_state):
    """Format current DB state as readable text for the Opus prompt."""
    lines = []

    lines.append("### Accommodations")
    for opt in db_state.get('accommodations', []):
        status = opt['booking_status']
        selected = " [SELECTED]" if opt.get('is_selected') else ""
        eliminated = " [ELIMINATED]" if opt.get('is_eliminated') else ""
        doc = " [HAS DOCUMENT]" if opt.get('document_id') else ""
        lines.append(
            f"- ID {opt['id']}: {opt['name']} @ {opt['location_name']} "
            f"| status={status}{selected}{eliminated}{doc}"
            f" | conf={opt.get('confirmation_number', 'none')}"
            f" | check_in_info={opt.get('check_in_info', 'none')}"
            f" | check_out_info={opt.get('check_out_info', 'none')}"
            f" | phone={opt.get('phone', 'none')}"
            f" | address={opt.get('address', 'none')}"
            f" | user_notes={opt.get('user_notes', 'none')[:200] if opt.get('user_notes') else 'none'}"
        )

    lines.append("\n### Flights")
    for fl in db_state.get('flights', []):
        doc = " [HAS DOCUMENT]" if fl.get('document_id') else ""
        lines.append(
            f"- ID {fl['id']}: {fl['flight_number']} ({fl['airline']}) "
            f"| status={fl['booking_status']}{doc}"
            f" | conf={fl.get('confirmation_number', 'none')}"
            f" | depart={fl.get('depart_time', '?')} arrive={fl.get('arrive_time', '?')}"
            f" | notes={fl.get('notes', 'none')[:100] if fl.get('notes') else 'none'}"
        )

    lines.append("\n### Activities (recent/relevant)")
    for act in db_state.get('activities', []):
        lines.append(
            f"- ID {act['id']}: \"{act['title']}\" Day {act['day_id']} "
            f"| {act.get('time_slot', '?')} | category={act.get('category', '?')}"
            f" | book_ahead={act.get('book_ahead', False)}"
        )

    return '\n'.join(lines)


def analyze_with_opus(extractions, db_state, api_key):
    """Stage 2: Send all Haiku extractions to Opus for intelligent analysis.

    Returns a list of curated, deduplicated change proposals with consequences.
    """
    import anthropic

    db_text = _format_db_state_for_opus(db_state)

    # Format extractions for the prompt
    extraction_lines = []
    for i, (content, extracted) in enumerate(extractions):
        extraction_lines.append(json.dumps({
            'index': i,
            'email_subject': content.get('subject', ''),
            'email_from': content.get('from', ''),
            'email_date': content.get('date', ''),
            'extracted_data': extracted,
        }, indent=2))

    extractions_text = '\n---\n'.join(extraction_lines)

    prompt = (OPUS_ANALYST_PROMPT
              .replace('<<DB_STATE>>', db_text)
              .replace('<<EXTRACTIONS>>', extractions_text))

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model='claude-opus-4-6',
            max_tokens=8192,
            messages=[{'role': 'user', 'content': prompt}],
        )
        text = response.content[0].text

        # Extract JSON array from response
        json_match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1))
        # Try parsing whole response as JSON
        return json.loads(text)
    except Exception as e:
        # If Opus fails, return empty — don't crash the sync
        return []


# ---------------------------------------------------------------------------
# DB state gathering
# ---------------------------------------------------------------------------

def get_db_state():
    """Get current DB state for diffing. Must be called in app context."""
    from models import AccommodationOption, AccommodationLocation, Flight, Activity, Day

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

    # Include activities that are book-ahead or confirmed, for dedup checking
    activities = []
    for act in Activity.query.filter(
        (Activity.book_ahead == True) |
        (Activity.is_confirmed == True) |
        (Activity.category == 'food')
    ).all():
        activities.append({
            'id': act.id,
            'title': act.title,
            'day_id': act.day_id,
            'time_slot': act.time_slot,
            'category': act.category,
            'book_ahead': act.book_ahead,
            'book_ahead_note': act.book_ahead_note,
            'is_confirmed': act.is_confirmed,
            'is_eliminated': act.is_eliminated,
            'notes': act.notes,
        })

    return {'accommodations': accommodations, 'flights': flights, 'activities': activities}


# ---------------------------------------------------------------------------
# Attachment handling
# ---------------------------------------------------------------------------

def download_and_upload_attachments(service, content, extracted, app):
    """Download email attachments and upload them to the app's document system."""
    if not extracted.get('has_attachment') or not content.get('attachments'):
        return []

    from models import db, Document
    import uuid
    from werkzeug.utils import secure_filename

    uploaded_ids = []
    docs_folder = os.path.join(app.config.get('UPLOAD_FOLDER', 'uploads'), 'documents')
    os.makedirs(docs_folder, exist_ok=True)

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


# ---------------------------------------------------------------------------
# Main sync pipeline
# ---------------------------------------------------------------------------

def run_sync(app):
    """Run a full two-stage Gmail sync cycle.

    Stage 1: Haiku scans each email for structured data (fast, cheap).
    Stage 2: Opus analyzes the batch — deduplicates, filters, and produces
             curated changes with clear consequences.
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
            existing_email_ids = {
                c.gmail_message_id
                for c in PendingGmailChange.query.with_entities(
                    PendingGmailChange.gmail_message_id).all()
            }

            # Use last successful sync date for incremental search
            # (first sync uses the hardcoded dates in TRAVEL_QUERIES)
            since_date = None
            last_completed = GmailSyncLog.query.filter_by(
                status='completed').order_by(
                GmailSyncLog.completed_at.desc()).first()
            if last_completed and last_completed.completed_at:
                # Go back 7 days from last sync for safety margin
                from datetime import timedelta
                lookback = last_completed.completed_at - timedelta(days=7)
                since_date = lookback.strftime('%Y/%m/%d')

            email_stubs = search_travel_emails(service, since_date=since_date)
            log.emails_found = len(email_stubs)

            new_stubs = [s for s in email_stubs
                         if s['id'] not in existing_email_ids]

            if not new_stubs:
                log.changes_detected = 0
                log.completed_at = datetime.utcnow()
                log.status = 'completed'
                db.session.commit()
                return {
                    'ok': True,
                    'emails_found': log.emails_found,
                    'new_emails': 0,
                    'changes_proposed': 0,
                    'sync_id': log.id,
                }

            # ----- Stage 1: Haiku extraction -----
            haiku_extractions = []  # list of (content_dict, extracted_dict)
            skipped_emails = []     # emails that Haiku said aren't travel-related

            for stub in new_stubs:
                try:
                    content = fetch_email_content(service, stub['id'])
                    extracted = extract_booking_data(content, api_key)

                    if not extracted or extracted.get('type') == 'other':
                        skipped_emails.append((stub['id'], content))
                        continue

                    # Hard geographic filter before passing to Opus
                    if not _passes_geographic_filter(extracted):
                        # Record as skipped with reason
                        city = extracted.get('city', 'unknown')
                        country = extracted.get('country', 'unknown')
                        skip = PendingGmailChange(
                            gmail_message_id=stub['id'],
                            email_subject=content.get('subject', '')[:500],
                            email_from=content.get('from', '')[:200],
                            email_date=content.get('date', '')[:100],
                            change_type='none',
                            entity_type='other',
                            description=f'Filtered: not Japan trip ({city}, {country})',
                            proposed_data=json.dumps(extracted),
                            status='skipped',
                            opus_reasoning=f'Geographic filter: {city}, {country} is not in Japan trip cities',
                        )
                        db.session.add(skip)
                        continue

                    haiku_extractions.append((content, extracted))

                except Exception as e:
                    log.errors = (log.errors or '') + f"\nHaiku stage {stub['id']}: {str(e)}"

            # Record skipped emails
            for email_id, content in skipped_emails:
                skip = PendingGmailChange(
                    gmail_message_id=email_id,
                    email_subject=content.get('subject', '')[:500],
                    email_from=content.get('from', '')[:200],
                    email_date=content.get('date', '')[:100],
                    change_type='none',
                    entity_type='other',
                    description='Not a travel booking email',
                    proposed_data='{}',
                    status='skipped',
                )
                db.session.add(skip)

            # ----- Stage 2: Opus analysis -----
            changes_created = 0

            if haiku_extractions:
                db_state = get_db_state()
                opus_results = analyze_with_opus(haiku_extractions, db_state, api_key)

                # Track which email indices Opus referenced, so we can record the rest
                referenced_indices = set()

                for result in opus_results:
                    action = result.get('action', 'skip')
                    email_idx = result.get('email_index', 0)

                    # Get the corresponding email content
                    if email_idx < 0 or email_idx >= len(haiku_extractions):
                        continue

                    referenced_indices.add(email_idx)
                    content, extracted = haiku_extractions[email_idx]
                    gmail_msg_id = content['id']

                    if action == 'skip':
                        skip = PendingGmailChange(
                            gmail_message_id=gmail_msg_id,
                            email_subject=content.get('subject', '')[:500],
                            email_from=content.get('from', '')[:200],
                            email_date=content.get('date', '')[:100],
                            change_type='none',
                            entity_type=result.get('entity_type', 'other'),
                            description=result.get('reason', 'Skipped by analysis'),
                            proposed_data=json.dumps(extracted),
                            status='skipped',
                            opus_reasoning=result.get('reason', ''),
                        )
                        db.session.add(skip)
                        continue

                    # Map Opus action to change_type
                    change_type = {'update': 'update', 'create': 'create',
                                   'cancel': 'cancel'}.get(action, 'update')

                    # Download attachments if the original extraction flagged them
                    if extracted.get('has_attachment') and content.get('attachments'):
                        uploaded_ids = download_and_upload_attachments(
                            service, content, extracted, app)
                        if uploaded_ids:
                            fields = result.get('fields', {})
                            fields['_uploaded_doc_ids'] = uploaded_ids
                            result['fields'] = fields

                    pending = PendingGmailChange(
                        gmail_message_id=gmail_msg_id,
                        email_subject=content.get('subject', '')[:500],
                        email_from=content.get('from', '')[:200],
                        email_date=content.get('date', '')[:100],
                        change_type=change_type,
                        entity_type=result.get('entity_type', 'other'),
                        entity_id=result.get('entity_id'),
                        description=result.get('description', 'Change proposed')[:500],
                        proposed_data=json.dumps(result.get('fields', {})),
                        current_data=json.dumps(result.get('current', {})),
                        consequence=result.get('consequence', '')[:1000],
                        confidence=result.get('confidence', 'medium'),
                        opus_reasoning=result.get('reasoning', '')[:1000],
                        status='pending',
                    )
                    db.session.add(pending)
                    changes_created += 1

                # Record any emails Opus didn't reference (prevents reprocessing)
                for idx, (content, extracted) in enumerate(haiku_extractions):
                    if idx not in referenced_indices:
                        skip = PendingGmailChange(
                            gmail_message_id=content['id'],
                            email_subject=content.get('subject', '')[:500],
                            email_from=content.get('from', '')[:200],
                            email_date=content.get('date', '')[:100],
                            change_type='none',
                            entity_type=extracted.get('type', 'other'),
                            description='No changes needed — data already up to date',
                            proposed_data=json.dumps(extracted),
                            status='skipped',
                            opus_reasoning='Email passed to analyst but no action required',
                        )
                        db.session.add(skip)

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


# ---------------------------------------------------------------------------
# Apply approved changes
# ---------------------------------------------------------------------------

def apply_change(change_id):
    """Apply an approved pending change to the DB.

    Handles all entity types: accommodations, flights, activities, transport.
    For supplementary info (check-in details, house rules), appends to existing data.
    """
    from models import (db, PendingGmailChange, AccommodationOption, Flight,
                        Activity, Day)
    import services.accommodations as accom_svc
    import services.flights as flight_svc
    import services.activities as activity_svc

    change = PendingGmailChange.query.get(change_id)
    if not change or change.status != 'pending':
        return {'ok': False, 'error': 'Change not found or not pending'}

    fields = json.loads(change.proposed_data)
    # Remove internal-only fields
    uploaded_doc_ids = fields.pop('_uploaded_doc_ids', [])

    # Fields that must never be overwritten via setattr
    PROTECTED_FIELDS = frozenset({
        'id', 'location_id', 'document_id', 'day_id', 'rank',
    })

    try:
        if change.entity_type == 'accommodation' and change.entity_id:
            opt = AccommodationOption.query.get(change.entity_id)
            if not opt:
                change.status = 'failed'
                change.errors = 'Accommodation option not found in DB'
                db.session.commit()
                return {'ok': False, 'error': 'Accommodation not found'}

            if change.change_type == 'cancel':
                # Use service layer for proper checklist cascade
                accom_svc.update_status(opt.id, {'booking_status': 'cancelled'})
                if opt.is_selected:
                    opt.is_selected = False
                    db.session.commit()

            elif change.change_type == 'update':
                # Handle selection separately via service layer
                if 'is_selected' in fields:
                    if fields.pop('is_selected'):
                        accom_svc.select(opt.id)

                # Route booking_status and related fields through service layer
                status_fields = {}
                if 'booking_status' in fields:
                    bs = fields.pop('booking_status')
                    # Never auto-set confirmed (requires document)
                    if bs != 'confirmed':
                        status_fields['booking_status'] = bs

                # Fields the service layer handles (with validation + cascades)
                for svc_field in ('confirmation_number', 'check_in_info',
                                  'check_out_info', 'address', 'maps_url',
                                  'booking_url'):
                    if svc_field in fields:
                        status_fields[svc_field] = fields.pop(svc_field)

                # For user_notes, APPEND to existing rather than replacing
                if 'user_notes' in fields:
                    if opt.user_notes:
                        existing = opt.user_notes.strip()
                        new_notes = fields['user_notes'].strip()
                        if new_notes not in existing:
                            status_fields['user_notes'] = existing + '\n' + new_notes
                        # else: already present, skip
                    else:
                        status_fields['user_notes'] = fields['user_notes']
                    fields.pop('user_notes')

                if status_fields:
                    accom_svc.update_status(opt.id, status_fields)

                # Apply remaining safe fields directly (phone, etc.)
                for k, v in fields.items():
                    if k in PROTECTED_FIELDS:
                        continue
                    if hasattr(opt, k) and v is not None:
                        setattr(opt, k, v)
                db.session.commit()

        elif change.entity_type == 'flight' and change.entity_id:
            flight = Flight.query.get(change.entity_id)
            if not flight:
                change.status = 'failed'
                change.errors = 'Flight not found in DB'
                db.session.commit()
                return {'ok': False, 'error': 'Flight not found'}

            # For flight notes, append rather than replace
            if 'notes' in fields and flight.notes:
                existing = flight.notes.strip()
                new_notes = fields['notes'].strip()
                if new_notes not in existing:
                    fields['notes'] = existing + ' | ' + new_notes

            # flight_svc.update handles its own Socket.IO emit
            flight_svc.update(flight.id, fields)

        elif change.entity_type == 'activity':
            if change.change_type == 'create':
                # Create a new activity
                day_id = fields.pop('day_id', None)
                if not day_id:
                    # Try to find day by date
                    activity_date = fields.pop('date', None)
                    if activity_date:
                        day = Day.query.filter_by(date=activity_date).first()
                        if day:
                            day_id = day.id

                if not day_id:
                    change.status = 'failed'
                    change.errors = 'Could not determine which day to add activity to'
                    db.session.commit()
                    return {'ok': False, 'error': 'No day_id for new activity'}

                # Build activity data for the service layer
                activity_data = {
                    'title': fields.get('title', 'Untitled'),
                    'time_slot': fields.get('time_slot', 'evening'),
                    'category': fields.get('category', 'food'),
                    'description': fields.get('description') or fields.get('notes'),
                    'address': fields.get('address'),
                    'book_ahead': fields.get('book_ahead', False),
                    'book_ahead_note': fields.get('book_ahead_note'),
                    'url': fields.get('url'),
                    'maps_url': fields.get('maps_url'),
                    'is_confirmed': True,
                }
                # Remove None values
                activity_data = {k: v for k, v in activity_data.items() if v is not None}

                # activity_svc.add handles its own Socket.IO emit
                activity_svc.add(day_id, activity_data)

            elif change.change_type == 'update' and change.entity_id:
                act = Activity.query.get(change.entity_id)
                if not act:
                    change.status = 'failed'
                    change.errors = 'Activity not found in DB'
                    db.session.commit()
                    return {'ok': False, 'error': 'Activity not found'}

                # Append to notes/description if existing
                for note_field in ('notes', 'description'):
                    if note_field in fields and getattr(act, note_field):
                        existing = getattr(act, note_field).strip()
                        new_notes = fields[note_field].strip()
                        if new_notes not in existing:
                            fields[note_field] = existing + '\n' + new_notes

                update_data = {k: v for k, v in fields.items()
                               if k not in PROTECTED_FIELDS
                               and hasattr(act, k) and v is not None}
                if update_data:
                    # activity_svc.update handles its own Socket.IO emit
                    activity_svc.update(act.id, update_data)

        elif change.entity_type == 'transport':
            if change.change_type == 'create':
                import services.transport as transport_svc
                # transport_svc.add handles its own Socket.IO emit
                transport_svc.add(fields)

        # Mark as approved
        change.status = 'approved'
        change.reviewed_at = datetime.utcnow()
        db.session.commit()

        return {'ok': True, 'message': change.description}

    except Exception as e:
        change.status = 'failed'
        change.errors = str(e)
        db.session.commit()
        return {'ok': False, 'error': str(e)}
