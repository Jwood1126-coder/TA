"""Boot-time schedule validation. Delegates to the trip audit service.

Prints warnings on boot so developers see issues immediately.
The same audit runs before export to gate rendering.
"""
from models import AccommodationLocation, db
from services.trip_audit import audit_trip


def validate_schedule(app):
    """Post-migration schedule validation. Prints warnings for conflicts.
    Runs on every boot to catch data issues early."""

    # Auto-fix: eliminated options should not claim confirmed/booked
    _autofix_eliminated_status()

    result = audit_trip()

    all_issues = result.blockers + result.warnings
    if all_issues:
        print(f"\n{'='*60}")
        print(f"SCHEDULE VALIDATION: {len(all_issues)} issue(s) "
              f"({len(result.blockers)} blockers, {len(result.warnings)} warnings)")
        print(f"{'='*60}")
        for b in result.blockers:
            print(f"  !! BLOCKER: {b}")
        for w in result.warnings:
            print(f"  !  {w}")
        if result.stale_refs:
            print(f"  (+ {len(result.stale_refs)} activities with stale hotel references)")
        print(f"{'='*60}\n")
    else:
        print("Schedule validation: all checks passed")


def _autofix_eliminated_status():
    """Downgrade eliminated options that still claim booked/confirmed."""
    for loc in AccommodationLocation.query.all():
        for opt in loc.options:
            if opt.is_eliminated and opt.booking_status in ('confirmed', 'booked'):
                old_status = opt.booking_status
                opt.booking_status = 'cancelled'
                db.session.commit()
                print(f"  AUTO-FIX: '{opt.name}' was eliminated but {old_status} "
                      f"— downgraded to cancelled")
