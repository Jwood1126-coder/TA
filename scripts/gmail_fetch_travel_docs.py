#!/usr/bin/env python3
"""Fetch travel booking documents from Gmail.

Searches Gmail for booking confirmations, flight receipts, and reservation
emails related to the Japan trip. Downloads PDF attachments and optionally
uploads them to the app's document system.

First run opens a browser for Google OAuth consent. After that, uses a
stored refresh token.

Usage:
    python scripts/gmail_fetch_travel_docs.py                  # search & list
    python scripts/gmail_fetch_travel_docs.py --download       # download PDFs
    python scripts/gmail_fetch_travel_docs.py --download --upload  # download + upload to app

Credentials: ~/.config/japan-travel-app/credentials.json
Token cache: ~/.config/japan-travel-app/token.json
"""
import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Gmail read-only scope
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

CONFIG_DIR = Path.home() / '.config' / 'japan-travel-app'
CREDENTIALS_FILE = CONFIG_DIR / 'credentials.json'
TOKEN_FILE = CONFIG_DIR / 'token.json'
DOWNLOAD_DIR = Path(__file__).parent.parent / 'downloads' / 'gmail'

# Search queries for travel-related emails
TRAVEL_QUERIES = [
    # Flights
    'subject:(flight receipt OR eTicket OR itinerary) (Delta OR United OR "DL275" OR "DL5392" OR "UA876" OR "UA1470" OR "HBPF75" OR "I91ZHJ")',
    # Accommodations
    'subject:(booking confirmation OR reservation confirmed OR "your receipt") (Agoda OR Airbnb OR "976558450" OR "976698966" OR "HMDDRX4NFX" OR "HMXTP9H2Z9")',
    # Broader travel bookings
    'subject:(booking OR confirmation OR reservation OR receipt) (hotel OR ryokan OR hostel OR machiya) after:2025/01/01',
    # Specific known bookings
    '"Sotetsu Fresa" OR "TAKANOYU" OR "Tsukiya-Mikazuki" OR "Kyotofish" OR "Leben Osaka" OR "KumoMachiya"',
    # JR Pass, activities, tickets
    'subject:(order confirmation OR booking confirmation OR e-ticket) (JR Pass OR Japan Rail OR Shinkansen OR klook OR viator OR GetYourGuide) after:2025/01/01',
    # Trip planning services
    '(from:tripit OR from:kayak OR from:google) subject:(trip OR itinerary OR travel) Japan after:2025/01/01',
]


def authenticate():
    """Authenticate with Gmail API, opening browser on first run."""
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing expired token...")
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                print(f"ERROR: No credentials file at {CREDENTIALS_FILE}")
                print("Download it from Google Cloud Console > APIs > Credentials")
                sys.exit(1)
            print("Opening browser for Google sign-in...")
            print("(Sign in with the Gmail account that has your booking emails)")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)

        # Save token for next time
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(TOKEN_FILE, 'w') as f:
            f.write(creds.to_json())
        print("Token saved — won't need browser auth again.")

    return build('gmail', 'v1', credentials=creds)


def search_emails(service, query, max_results=50):
    """Search Gmail with a query string. Returns list of message stubs."""
    try:
        result = service.users().messages().list(
            userId='me', q=query, maxResults=max_results).execute()
        return result.get('messages', [])
    except Exception as e:
        print(f"  Search error: {e}")
        return []


def get_message(service, msg_id):
    """Fetch full message by ID."""
    return service.users().messages().get(
        userId='me', id=msg_id, format='full').execute()


def extract_message_info(msg):
    """Extract useful info from a Gmail message."""
    headers = {h['name']: h['value'] for h in msg['payload'].get('headers', [])}
    info = {
        'id': msg['id'],
        'subject': headers.get('Subject', '(no subject)'),
        'from': headers.get('From', ''),
        'date': headers.get('Date', ''),
        'snippet': msg.get('snippet', ''),
        'attachments': [],
    }

    # Find attachments recursively
    _find_attachments(msg['payload'], info['attachments'], msg['id'])
    return info


def _find_attachments(part, attachments, msg_id):
    """Recursively find attachments in message parts."""
    if part.get('filename') and part.get('body', {}).get('attachmentId'):
        attachments.append({
            'filename': part['filename'],
            'attachment_id': part['body']['attachmentId'],
            'mime_type': part.get('mimeType', ''),
            'size': part.get('body', {}).get('size', 0),
            'message_id': msg_id,
        })

    for sub in part.get('parts', []):
        _find_attachments(sub, attachments, msg_id)


def download_attachment(service, msg_id, attachment_id, filename, output_dir):
    """Download an attachment to disk."""
    result = service.users().messages().attachments().get(
        userId='me', messageId=msg_id, id=attachment_id).execute()
    data = base64.urlsafe_b64decode(result['data'])

    output_dir.mkdir(parents=True, exist_ok=True)
    # Sanitize filename
    safe_name = re.sub(r'[<>:"/\\|?*]', '_', filename)
    path = output_dir / safe_name

    # Don't overwrite existing files
    if path.exists():
        print(f"    Already exists: {safe_name}")
        return path

    with open(path, 'wb') as f:
        f.write(data)
    return path


def classify_email(info):
    """Classify an email by type based on subject/sender."""
    subject = info['subject'].lower()
    sender = info['from'].lower()
    snippet = info['snippet'].lower()

    if any(k in subject for k in ['flight', 'eticket', 'itinerary']):
        if 'delta' in subject or 'delta' in sender:
            return 'flight_delta'
        if 'united' in subject or 'united' in sender:
            return 'flight_united'
        return 'flight_other'
    if any(k in subject or k in sender for k in ['agoda']):
        return 'accommodation_agoda'
    if any(k in subject or k in sender for k in ['airbnb']):
        return 'accommodation_airbnb'
    if any(k in subject for k in ['booking confirmation', 'reservation confirmed']):
        return 'accommodation_other'
    if any(k in subject or k in sender for k in ['jr pass', 'japan rail', 'klook', 'viator']):
        return 'activity_ticket'
    if 'tripit' in sender or 'tripit' in subject:
        return 'trip_aggregator'
    return 'other'


def main():
    parser = argparse.ArgumentParser(description='Fetch travel docs from Gmail')
    parser.add_argument('--download', action='store_true',
                        help='Download PDF attachments')
    parser.add_argument('--upload', action='store_true',
                        help='Upload downloaded docs to the app (requires --download)')
    parser.add_argument('--app-url', default='https://web-production-f84b27.up.railway.app',
                        help='App URL for uploading documents')
    parser.add_argument('--query', type=str, default=None,
                        help='Custom Gmail search query (overrides built-in queries)')
    args = parser.parse_args()

    print("=== Gmail Travel Document Fetcher ===\n")

    # Authenticate
    service = authenticate()
    print()

    # Search for travel emails
    seen_ids = set()
    all_emails = []

    queries = [args.query] if args.query else TRAVEL_QUERIES
    for i, query in enumerate(queries):
        label = query[:60] + '...' if len(query) > 60 else query
        print(f"Searching ({i+1}/{len(queries)}): {label}")
        messages = search_emails(service, query)
        new = 0
        for m in messages:
            if m['id'] not in seen_ids:
                seen_ids.add(m['id'])
                all_emails.append(m)
                new += 1
        print(f"  Found {len(messages)} results ({new} new)")

    print(f"\nTotal unique emails: {len(all_emails)}")
    print()

    # Fetch details for each email
    results = []
    for i, stub in enumerate(all_emails):
        msg = get_message(service, stub['id'])
        info = extract_message_info(msg)
        info['category'] = classify_email(info)
        results.append(info)

    # Sort by date
    results.sort(key=lambda x: x['date'])

    # Display results
    print("=" * 80)
    print(f"{'#':>3}  {'Category':<22} {'Attachments':>5}  Subject")
    print("-" * 80)
    for i, info in enumerate(results):
        att_count = len(info['attachments'])
        att_str = f"{att_count} PDF" if att_count else "  -  "
        cat = info['category']
        subj = info['subject'][:50].encode('ascii', 'replace').decode()
        print(f"{i+1:>3}  {cat:<22} {att_str:>5}  {subj}")

    # Summary
    with_attachments = [r for r in results if r['attachments']]
    print(f"\n{len(with_attachments)} emails have downloadable attachments")
    total_pdfs = sum(len(r['attachments']) for r in results)
    print(f"{total_pdfs} total PDF/attachments found")

    if args.download and with_attachments:
        print(f"\n=== Downloading attachments to {DOWNLOAD_DIR} ===\n")
        downloaded = []
        for info in results:
            for att in info['attachments']:
                print(f"  Downloading: {att['filename']}")
                path = download_attachment(
                    service, att['message_id'], att['attachment_id'],
                    att['filename'], DOWNLOAD_DIR)
                downloaded.append({
                    'path': str(path),
                    'filename': att['filename'],
                    'category': info['category'],
                    'subject': info['subject'],
                    'email_date': info['date'],
                })
                print(f"    -> {path}")

        # Save manifest
        manifest_path = DOWNLOAD_DIR / 'manifest.json'
        with open(manifest_path, 'w') as f:
            json.dump(downloaded, f, indent=2)
        print(f"\nManifest saved: {manifest_path}")
        print(f"Downloaded {len(downloaded)} files")

        if args.upload:
            print("\n=== Upload to app ===")
            print(f"Target: {args.app_url}")
            _upload_to_app(downloaded, args.app_url)

    elif not args.download:
        print("\nRun with --download to save attachments")
        print("Run with --download --upload to also push to the app")

    # Save search results for reference
    summary_path = CONFIG_DIR / 'last_search_results.json'
    summary = [{
        'subject': r['subject'],
        'from': r['from'],
        'date': r['date'],
        'category': r['category'],
        'attachment_count': len(r['attachments']),
        'attachment_names': [a['filename'] for a in r['attachments']],
    } for r in results]
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2)
    print(f"\nSearch summary saved: {summary_path}")


def _upload_to_app(downloaded, app_url):
    """Upload downloaded documents to the app's document system."""
    import requests

    upload_url = f"{app_url.rstrip('/')}/api/documents/upload"
    uploaded = 0
    skipped = 0

    for doc in downloaded:
        path = Path(doc['path'])
        if not path.exists():
            print(f"  SKIP (missing): {doc['filename']}")
            skipped += 1
            continue

        # Determine doc_type from category
        cat = doc['category']
        if 'flight' in cat:
            doc_type = 'flight_receipt'
        elif 'accommodation' in cat:
            doc_type = 'accommodation_booking'
        elif 'activity' in cat or 'ticket' in cat:
            doc_type = 'activity_ticket'
        else:
            doc_type = 'other'

        print(f"  Uploading: {doc['filename']} (type: {doc_type})")
        try:
            with open(path, 'rb') as f:
                resp = requests.post(upload_url, files={
                    'file': (doc['filename'], f, 'application/pdf'),
                }, data={'doc_type': doc_type})
            if resp.ok:
                print(f"    OK: {resp.json().get('message', 'uploaded')}")
                uploaded += 1
            else:
                print(f"    FAILED ({resp.status_code}): {resp.text[:100]}")
                skipped += 1
        except Exception as e:
            print(f"    ERROR: {e}")
            skipped += 1

    print(f"\nUploaded: {uploaded}, Skipped: {skipped}")


if __name__ == '__main__':
    main()
