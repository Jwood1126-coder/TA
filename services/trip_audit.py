"""Trip audit service — pre-export reconciliation and integrity checks.

Compares canonical structured facts (accommodations, routes, flights) against
authored narrative (activity titles, descriptions, day notes) and flags
contradictions, staleness, and data integrity issues.

Returns an AuditResult that the export pipeline uses to gate rendering:
- blockers: severe contradictions that prevent trusted export
- warnings: planning issues worth surfacing but not blocking
- stale_refs: activity IDs where narrative references contradict canonical facts
"""
import re
from collections import defaultdict
from datetime import timedelta

from models import (db, Trip, Day, Activity, AccommodationLocation,
                    AccommodationOption, TransportRoute, Flight)


class AuditResult:
    """Container for trip audit findings."""

    __slots__ = ('blockers', 'warnings', 'stale_refs')

    def __init__(self):
        self.blockers = []   # Severe: block export
        self.warnings = []   # Advisory: show in export
        self.stale_refs = set()  # Activity IDs with stale hotel/route references

    @property
    def ok(self):
        return not self.blockers

    @property
    def exportable(self):
        return not self.blockers

    def to_dict(self):
        return {
            'exportable': self.exportable,
            'blockers': self.blockers,
            'warnings': self.warnings,
            'stale_activity_ids': sorted(self.stale_refs),
        }


def audit_trip():
    """Run all audit checks and return an AuditResult.

    Must be called within a Flask app context.
    """
    result = AuditResult()
    trip = Trip.query.first()
    if not trip:
        result.blockers.append('No trip record found')
        return result

    # Build canonical state
    selected_accoms = _get_selected_accommodations()
    eliminated_names = _get_eliminated_hotel_names(selected_accoms)

    # Run checks
    _check_accommodation_chain(selected_accoms, result)
    _check_accommodation_nights(result)
    _check_multi_select(result)
    _check_eliminated_status(result)
    _check_duplicate_locations(result)
    _check_route_chain(selected_accoms, result)
    _check_narrative_references(selected_accoms, eliminated_names, result)
    _check_document_integrity(result)
    _check_departure_conflicts(result)
    _check_overpacked_days(result)
    _check_activity_completeness(result)
    _check_transport_day_linkage(result)
    _check_day_location_consistency(selected_accoms, result)

    return result


# ---------------------------------------------------------------------------
# Canonical state helpers
# ---------------------------------------------------------------------------

def _get_selected_accommodations():
    """Return selected AccommodationOptions ordered by check-in date.

    Skips locations where all options are eliminated (e.g. Kanazawa day-trip).
    """
    locs = AccommodationLocation.query.order_by(
        AccommodationLocation.check_in_date).all()
    selected = []
    for loc in locs:
        if all(o.is_eliminated for o in loc.options):
            continue
        sel = next((o for o in loc.options if o.is_selected), None)
        if sel:
            selected.append((loc, sel))
    return selected


def _get_eliminated_hotel_names(selected_accoms):
    """Return set of eliminated hotel name fragments (words >=4 chars).

    Excludes names that match the selected hotel for that location
    and generic words that would cause false positives.
    """
    selected_names = {opt.name for _, opt in selected_accoms}
    eliminated = AccommodationOption.query.filter_by(is_eliminated=True).all()

    # Generic words to skip (too common, cause false matches)
    skip_words = {
        'hotel', 'hostel', 'guesthouse', 'guest', 'house', 'ryokan',
        'premium', 'station', 'kyoto', 'tokyo', 'osaka', 'kanazawa',
        'takayama', 'shinjuku', 'gion', 'nishijin', 'none', 'annex',
        'residence', 'lounge',
    }

    names = set()
    for opt in eliminated:
        if opt.name in selected_names:
            continue
        # Use the full name for matching (more precise)
        names.add(opt.name)
    return names


# ---------------------------------------------------------------------------
# Check A: Accommodation chain integrity
# ---------------------------------------------------------------------------

def _check_accommodation_chain(selected_accoms, result):
    """Verify exactly one selected stay per night, no overlaps, no gaps."""
    if not selected_accoms:
        result.blockers.append('No selected accommodations found')
        return

    # Check chain continuity
    for i in range(len(selected_accoms) - 1):
        curr_loc, _ = selected_accoms[i]
        next_loc, _ = selected_accoms[i + 1]
        if curr_loc.check_out_date and next_loc.check_in_date:
            gap = (next_loc.check_in_date - curr_loc.check_out_date).days
            if gap > 0:
                result.blockers.append(
                    f'Accommodation gap: {gap} night(s) between '
                    f'{curr_loc.location_name} checkout ({curr_loc.check_out_date}) '
                    f'and {next_loc.location_name} checkin ({next_loc.check_in_date})')
            elif gap < 0:
                result.blockers.append(
                    f'Accommodation overlap: {abs(gap)} night(s) between '
                    f'{curr_loc.location_name} and {next_loc.location_name}')

    # Check for duplicate coverage of the same night
    night_map = defaultdict(list)
    for loc, opt in selected_accoms:
        if loc.check_in_date and loc.check_out_date:
            d = loc.check_in_date
            while d < loc.check_out_date:
                night_map[d].append(f'{opt.name} @ {loc.location_name}')
                d += timedelta(days=1)

    for night, stays in sorted(night_map.items()):
        if len(stays) > 1:
            result.blockers.append(
                f'Multiple stays on {night}: {", ".join(stays)}')


def _check_accommodation_nights(result):
    """Verify num_nights matches date arithmetic."""
    locs = AccommodationLocation.query.all()
    for loc in locs:
        if all(o.is_eliminated for o in loc.options):
            continue
        if loc.check_in_date and loc.check_out_date:
            expected = (loc.check_out_date - loc.check_in_date).days
            if loc.num_nights and loc.num_nights != expected:
                result.warnings.append(
                    f'{loc.location_name}: says {loc.num_nights} nights '
                    f'but dates span {expected} '
                    f'({loc.check_in_date} to {loc.check_out_date})')


def _check_multi_select(result):
    """Flag locations with multiple selected options."""
    for loc in AccommodationLocation.query.all():
        selected_count = sum(1 for o in loc.options if o.is_selected)
        if selected_count > 1:
            result.blockers.append(
                f'{loc.location_name}: {selected_count} selected options '
                f'(must be 0 or 1)')


# ---------------------------------------------------------------------------
# Check B: Transport route chain
# ---------------------------------------------------------------------------

def _check_route_chain(selected_accoms, result):
    """Verify transport routes are consistent with accommodation progression."""
    routes = TransportRoute.query.order_by(TransportRoute.sort_order).all()

    for route in routes:
        # Self-routes are suspicious only for intercity routes
        # (intra-city transit like Shinagawa -> Haneda is normal)
        if route.route_from and route.route_to:
            from_norm = route.route_from.lower().strip()
            to_norm = route.route_to.lower().strip()
            # Only flag if the literal station names match
            # (not normalized cities — "Shinagawa -> Haneda" is fine)
            if from_norm == to_norm:
                result.warnings.append(
                    f'Self-route: {route.route_from} -> {route.route_to} '
                    f'(origin equals destination)')


def _normalize_city(station_name):
    """Extract city name from a station/location string.

    'Kanazawa Station' -> 'kanazawa'
    'Tokyo' -> 'tokyo'
    'Haneda Airport' -> 'tokyo'  (special case)
    """
    s = station_name.lower().strip()
    # Remove common suffixes
    for suffix in (' station', ' airport', ' port'):
        s = s.replace(suffix, '')
    # Airport mapping
    airport_map = {'haneda': 'tokyo', 'narita': 'tokyo', 'kansai': 'osaka',
                   'itami': 'osaka', 'shinagawa': 'tokyo'}
    return airport_map.get(s, s)


# ---------------------------------------------------------------------------
# Check C: Narrative reference validation
# ---------------------------------------------------------------------------

def _check_narrative_references(selected_accoms, eliminated_names, result):
    """Scan activity titles/descriptions for references to eliminated hotels.

    Uses brand-name matching: extracts the distinctive brand portion of each
    eliminated hotel name and searches for it as a phrase. This avoids false
    positives from neighborhood names (Asakusa, Sanjo, Gion, etc.).
    """
    # Build patterns from eliminated hotel brand names
    patterns = []
    for name in eliminated_names:
        brand = _extract_brand(name)
        if brand and len(brand) >= 4:
            # Use word boundaries to avoid substring matches
            # ("GATE" shouldn't match "Torii Gate" as a generic word)
            pat = r'\b' + re.escape(brand) + r'\b'
            patterns.append((re.compile(pat, re.IGNORECASE), name))

    if not patterns:
        return

    # Scan all active activities
    activities = Activity.query.filter_by(
        is_eliminated=False, is_substitute=False).all()

    for act in activities:
        text = ' '.join(filter(None, [act.title, act.description,
                                       act.getting_there, act.notes]))
        if not text.strip():
            continue

        for pattern, hotel_name in patterns:
            if pattern.search(text):
                day = Day.query.get(act.day_id)
                result.stale_refs.add(act.id)
                result.warnings.append(
                    f'Stale reference: Day {day.day_number} activity '
                    f'"{_truncate(act.title, 50)}" mentions eliminated hotel '
                    f'"{hotel_name}"')
                break  # One flag per activity is enough


def _extract_brand(hotel_name):
    """Extract the distinctive brand portion of a hotel name.

    Strips generic prefixes/suffixes (Hotel, Inn, Hostel, Ryokan, etc.)
    and location suffixes (Kyoto, Shinjuku, Asakusa, etc.) to get the
    brand name that would indicate a specific reference.

    Examples:
        'Dormy Inn Asakusa' -> 'Dormy Inn'
        'Piece Hostel Sanjo' -> 'Piece'
        'K\\'s House Kyoto' -> 'K\\'s House'
        'Nui. Hostel & Bar Lounge' -> 'Nui.'
        'CITAN Hostel' -> 'CITAN'
        'Airbnb machiya' -> 'Airbnb machiya'
        'Machiya Residence Inn' -> 'Machiya Residence'
        'THE GATE HOTEL Kaminarimon' -> 'GATE HOTEL'
    """
    # Location/neighborhood words to strip from the end
    locations = {
        'kyoto', 'tokyo', 'osaka', 'kanazawa', 'takayama', 'shinjuku',
        'asakusa', 'gion', 'nishijin', 'sanjo', 'shijo', 'kaminarimon',
        'kabukicho', 'kawaramachi', 'jingugaien',
    }
    # Generic hospitality words
    generic_words = {
        'hotel', 'hostel', 'guesthouse', 'inn', 'ryokan', 'premium',
        'the', 'and', '&', 'bar', 'lounge', 'annex', 'residence',
    }

    words = hotel_name.split()
    # Strip location words from the end
    while words and words[-1].lower().rstrip('()') in locations:
        words.pop()
    # Strip generic words from the start (but keep at least one word)
    while len(words) > 1 and words[0].lower() in generic_words:
        words.pop(0)
    # Strip generic suffixes (but keep at least one word)
    while len(words) > 1 and words[-1].lower() in generic_words:
        words.pop()

    brand = ' '.join(words)

    # Final check: reject single common English words that cause false positives
    common_words = {
        'gate', 'piece', 'garden', 'view', 'park', 'star', 'grand',
        'cross', 'green', 'east', 'west', 'north', 'south', 'central',
        'royal', 'house', 'home', 'nest', 'haven', 'base', 'stay',
    }
    if brand.lower() in common_words:
        return ''

    return brand


def _truncate(s, n):
    return s if len(s) <= n else s[:n - 3] + '...'


# ---------------------------------------------------------------------------
# Check D: Document integrity
# ---------------------------------------------------------------------------

def _check_document_integrity(result):
    """Confirmed bookings must have documents."""
    for loc in AccommodationLocation.query.all():
        for opt in loc.options:
            if opt.booking_status == 'confirmed' and not opt.document_id:
                result.blockers.append(
                    f'{opt.name}: confirmed without linked document')
            if (opt.confirmation_number and not opt.document_id
                    and opt.is_selected):
                result.warnings.append(
                    f'{opt.name}: has confirmation #{opt.confirmation_number} '
                    f'but no linked document')

    for f in Flight.query.all():
        if f.booking_status == 'confirmed' and not f.document_id:
            result.blockers.append(
                f'Flight {f.flight_number}: confirmed without linked document')


# ---------------------------------------------------------------------------
# Check E: Schedule sanity
# ---------------------------------------------------------------------------

def _check_departure_conflicts(result):
    """Flag evening/night activities on departure day."""
    flights = Flight.query.filter_by(direction='return').all()
    days = Day.query.all()
    for f in flights:
        if not f.depart_date:
            continue
        dep_day = next((d for d in days if d.date == f.depart_date), None)
        if not dep_day:
            continue
        late = Activity.query.filter_by(
            day_id=dep_day.id, is_eliminated=False
        ).filter(Activity.time_slot.in_(['evening', 'night'])).all()
        for a in late:
            if not a.is_substitute:
                result.warnings.append(
                    f'Departure conflict: "{a.title}" ({a.time_slot}) '
                    f'on departure day {dep_day.day_number}')


def _check_overpacked_days(result):
    """Flag days with more than 10 active non-substitute activities."""
    for d in Day.query.all():
        count = Activity.query.filter_by(
            day_id=d.id, is_eliminated=False, is_substitute=False).count()
        if count > 10:
            result.warnings.append(
                f'Overpacked: Day {d.day_number} ({d.title}) '
                f'has {count} active activities')


# ---------------------------------------------------------------------------
# Check F: Activity completeness
# ---------------------------------------------------------------------------

def _check_activity_completeness(result):
    """Flag activities missing key structural fields."""
    activities = Activity.query.filter_by(
        is_eliminated=False, is_substitute=False).all()

    no_timeslot = 0
    no_category = 0
    book_ahead_no_note = 0

    for a in activities:
        # Skip transit/logistics for time_slot check
        if not a.time_slot and a.category not in ('transit', 'logistics'):
            no_timeslot += 1
        if not a.category:
            no_category += 1
        if a.book_ahead and not a.book_ahead_note:
            day = Day.query.get(a.day_id)
            result.warnings.append(
                f'Book-ahead without details: Day {day.day_number} '
                f'"{_truncate(a.title, 40)}" — needs booking info')
            book_ahead_no_note += 1

    if no_category > 10:
        result.warnings.append(
            f'Activity completeness: {no_category} activities missing category')
    if no_timeslot > 5:
        result.warnings.append(
            f'Activity completeness: {no_timeslot} activities missing time_slot')


# ---------------------------------------------------------------------------
# Check G: Transport route day linkage
# ---------------------------------------------------------------------------

def _check_transport_day_linkage(result):
    """Flag transport routes not linked to a day."""
    routes = TransportRoute.query.all()
    unlinked = [r for r in routes if r.day_id is None]
    if unlinked:
        for r in unlinked:
            result.warnings.append(
                f'Unlinked transport: {r.route_from} → {r.route_to} '
                f'has no day assignment')


# ---------------------------------------------------------------------------
# Check H: Eliminated option status integrity
# ---------------------------------------------------------------------------

def _check_eliminated_status(result):
    """Flag eliminated options that still claim booked/confirmed status.

    The service layer prevents this at mutation time, so finding these means
    something bypassed the service layer (direct DB edit, legacy data, etc.).
    """
    for opt in AccommodationOption.query.filter_by(is_eliminated=True).all():
        if opt.booking_status in ('booked', 'confirmed'):
            loc = AccommodationLocation.query.get(opt.location_id)
            loc_name = loc.location_name if loc else '(unknown)'
            result.warnings.append(
                f'Eliminated but {opt.booking_status}: "{opt.name}" at '
                f'{loc_name} — should be cancelled or not_booked')


# ---------------------------------------------------------------------------
# Check I: Duplicate location detection
# ---------------------------------------------------------------------------

def _check_duplicate_locations(result):
    """Flag multiple accommodation locations covering the same date range.

    Detects locations with overlapping dates that might be duplicates from
    legacy data or failed merges. Skips all-eliminated locations.
    """
    locs = AccommodationLocation.query.order_by(
        AccommodationLocation.check_in_date).all()
    active_locs = [loc for loc in locs
                   if loc.check_in_date and loc.check_out_date
                   and loc.options
                   and not all(o.is_eliminated for o in loc.options)]

    for i, a in enumerate(active_locs):
        for b in active_locs[i + 1:]:
            # True overlap (not just same-day transition)
            if (a.check_in_date < b.check_out_date and
                    a.check_out_date > b.check_in_date and
                    a.check_in_date != b.check_out_date and
                    a.check_out_date != b.check_in_date):
                # Check if they look like duplicates (same city prefix)
                a_city = a.location_name.split('(')[0].split(' Stay')[0].strip().lower()
                b_city = b.location_name.split('(')[0].split(' Stay')[0].strip().lower()
                if a_city == b_city:
                    result.warnings.append(
                        f'Possible duplicate locations: "{a.location_name}" '
                        f'({a.check_in_date}–{a.check_out_date}) and '
                        f'"{b.location_name}" '
                        f'({b.check_in_date}–{b.check_out_date})')


# ---------------------------------------------------------------------------
# Check J: Day-location consistency
# ---------------------------------------------------------------------------

def _check_day_location_consistency(selected_accoms, result):
    """Verify each day's location matches the accommodation covering that date.

    The accommodation chain defines which city the traveler is in on each date.
    If a day's location_id points to a different city, the day view will show
    wrong weather, vibe, and context for activities on that day.

    Only checks days covered by the accommodation chain (skips travel days
    before first check-in and after last check-out).
    """
    from models import Location
    if not selected_accoms:
        return

    # Build date→city map from accommodation chain
    date_to_city = {}
    for loc, _ in selected_accoms:
        if not loc.check_in_date or not loc.check_out_date:
            continue
        city = loc.location_name.split('(')[0].split(' Stay')[0].strip().lower()
        d = loc.check_in_date
        while d < loc.check_out_date:
            date_to_city[d] = (city, loc.location_name)
            d += timedelta(days=1)

    # Check each day's assigned location against the chain
    days = Day.query.all()
    for day in days:
        if not day.date or day.date not in date_to_city:
            continue
        expected_city, expected_loc_name = date_to_city[day.date]
        if day.location_id:
            location = Location.query.get(day.location_id)
            if location:
                day_city = location.name.lower().strip()
                if day_city != expected_city:
                    result.warnings.append(
                        f'Day {day.day_number} ({day.date}): assigned to '
                        f'{location.name} but accommodation chain says '
                        f'{expected_loc_name}')
