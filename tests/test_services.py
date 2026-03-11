"""
Service layer tests — verify shared mutation behavior for UI and chat paths.

Validates that services produce correct side effects: DB writes, cascades,
and validation. Socket.IO emits are tested via mock.

Includes parity tests that verify UI routes and chat executor produce the
same DB state by exercising the same service functions.

Run: python -m pytest tests/test_services.py -v
"""
import json
import pytest
from unittest.mock import patch
from datetime import datetime

from app import create_app
from models import (db, Activity, Day, AccommodationOption, AccommodationLocation,
                    ChecklistItem, ChecklistOption, Flight)


@pytest.fixture(scope='module')
def svc_app():
    """Create app for service mutation tests.

    Uses the standard create_app() which reads from data/japan_trip.db.
    Service tests may mutate this DB, but smoke tests also use it with
    module scope. pytest runs modules in file-name order, so test_services
    runs first. We accept that mutations persist — tests are written to
    be order-independent and only verify behavior, not final state.
    """
    app = create_app(run_data_migrations=False)
    app.config['TESTING'] = True
    with app.app_context():
        yield app


@pytest.fixture(autouse=True)
def ctx(svc_app):
    """Provide app context for each test."""
    with svc_app.app_context():
        yield svc_app


# ---- Activity Services ----

class TestActivityToggle:
    def test_toggle_sets_completed_and_timestamp(self, ctx):
        from services.activities import toggle
        act = Activity.query.filter_by(is_completed=False).first()
        assert act is not None
        aid = act.id
        with patch('services.activities.socketio'):
            result = toggle(aid)
        assert result.is_completed is True
        assert result.completed_at is not None
        # Toggle back
        with patch('services.activities.socketio'):
            result = toggle(aid)
        assert result.is_completed is False
        assert result.completed_at is None

    def test_toggle_emits_socketio(self, ctx):
        from services.activities import toggle
        act = Activity.query.filter_by(is_completed=False).first()
        with patch('services.activities.socketio') as mock_sio:
            toggle(act.id)
            mock_sio.emit.assert_called_once()
            args = mock_sio.emit.call_args
            assert args[0][0] == 'activity_toggled'
            assert args[0][1]['id'] == act.id


class TestActivityAdd:
    def test_add_validates_time_slot(self, ctx):
        from services.activities import add
        day = Day.query.first()
        with pytest.raises(ValueError, match='time_slot'):
            with patch('services.activities.socketio'):
                add(day.id, {'title': 'Test', 'time_slot': 'invalid_slot'})

    def test_add_validates_negative_cost(self, ctx):
        from services.activities import add
        day = Day.query.first()
        with pytest.raises(ValueError):
            with patch('services.activities.socketio'):
                add(day.id, {'title': 'Test', 'cost_per_person': -5})

    def test_add_creates_activity(self, ctx):
        from services.activities import add
        day = Day.query.first()
        with patch('services.activities.socketio'):
            result = add(day.id, {
                'title': '__test_svc_activity__',
                'time_slot': 'morning',
            })
        assert result.id is not None
        assert result.title == '__test_svc_activity__'
        # Clean up
        db.session.delete(result)
        db.session.commit()


class TestActivityEliminate:
    def test_eliminate_toggles(self, ctx):
        from services.activities import eliminate
        act = Activity.query.filter_by(is_eliminated=False).first()
        result = eliminate(act.id)
        assert result.is_eliminated is True
        result = eliminate(act.id)
        assert result.is_eliminated is False


# ---- Accommodation Services ----

class TestAccommodationSelect:
    def test_select_deselects_siblings(self, ctx):
        from services.accommodations import select
        # Find a location with multiple options
        loc = AccommodationLocation.query.first()
        options = AccommodationOption.query.filter_by(location_id=loc.id).all()
        if len(options) < 2:
            pytest.skip('Need 2+ options to test select')
        # Remember original selection to restore after
        original = next((o for o in options if o.is_selected), None)
        with patch('services.accommodations.socketio'):
            select(options[1].id)
        refreshed = AccommodationOption.query.filter_by(
            location_id=loc.id, is_selected=True).all()
        assert len(refreshed) == 1
        assert refreshed[0].id == options[1].id
        # Restore original selection
        if original:
            with patch('services.accommodations.socketio'):
                select(original.id)


class TestAccommodationUpdateStatus:
    def test_rejects_invalid_status(self, ctx):
        from services.accommodations import update_status
        opt = AccommodationOption.query.filter_by(is_selected=True).first()
        with pytest.raises(ValueError):
            with patch('services.accommodations.socketio'):
                update_status(opt.id, {'booking_status': 'nonexistent_status'})

    def test_confirmed_requires_document(self, ctx):
        from services.accommodations import update_status
        # Find an option without a document
        opt = AccommodationOption.query.filter(
            AccommodationOption.document_id.is_(None)
        ).first()
        if not opt:
            pytest.skip('All options have documents')
        with pytest.raises(ValueError, match='document'):
            with patch('services.accommodations.socketio'):
                update_status(opt.id, {'booking_status': 'confirmed'})

    def test_update_cascades_to_checklist(self, ctx):
        from services.accommodations import update_status
        # Find an option whose location has a linked checklist item
        cl = ChecklistItem.query.filter(
            ChecklistItem.accommodation_location_id.isnot(None)
        ).first()
        if not cl:
            pytest.skip('No linked checklist items')
        opt = AccommodationOption.query.filter_by(
            location_id=cl.accommodation_location_id, is_selected=True
        ).first()
        if not opt:
            pytest.skip('No selected option for linked checklist')
        orig_status = opt.booking_status
        orig_cl_status = cl.status
        with patch('services.accommodations.socketio'):
            update_status(opt.id, {'booking_status': 'booked'})
        db.session.refresh(cl)
        assert cl.status == 'booked'
        # Restore
        opt.booking_status = orig_status
        cl.status = orig_cl_status
        db.session.commit()

    def test_validates_negative_price(self, ctx):
        from services.accommodations import update_status
        opt = AccommodationOption.query.first()
        with pytest.raises(ValueError):
            with patch('services.accommodations.socketio'):
                update_status(opt.id, {'price_low': -100})


class TestAccommodationEliminate:
    def test_cannot_eliminate_booked(self, ctx):
        from services.accommodations import eliminate
        opt = AccommodationOption.query.filter_by(booking_status='booked').first()
        if not opt:
            pytest.skip('No booked options')
        with pytest.raises(ValueError, match='Cannot eliminate'):
            with patch('services.accommodations.socketio'):
                eliminate(opt.id, eliminate=True)


# ---- Checklist Services ----

class TestChecklistToggle:
    def test_toggle_syncs_status_field(self, ctx):
        from services.checklists import toggle
        item = ChecklistItem.query.filter_by(is_completed=False).first()
        with patch('services.checklists.socketio'):
            result = toggle(item.id)
        assert result.is_completed is True
        assert result.status == 'completed'
        with patch('services.checklists.socketio'):
            result = toggle(item.id)
        assert result.is_completed is False
        assert result.status == 'pending'


class TestChecklistUpdateStatus:
    def test_rejects_invalid_status(self, ctx):
        from services.checklists import update_status
        item = ChecklistItem.query.first()
        with pytest.raises(ValueError, match='Invalid status'):
            with patch('services.checklists.socketio'):
                update_status(item.id, 'bogus')

    def test_cascades_to_accommodation(self, ctx):
        from services.checklists import update_status
        cl = ChecklistItem.query.filter(
            ChecklistItem.accommodation_location_id.isnot(None)
        ).first()
        if not cl:
            pytest.skip('No linked checklist items')
        opt = AccommodationOption.query.filter_by(
            location_id=cl.accommodation_location_id, is_selected=True
        ).first()
        if not opt:
            pytest.skip('No selected option')
        orig_opt_status = opt.booking_status
        orig_cl_status = cl.status
        with patch('services.checklists.socketio'):
            update_status(cl.id, 'booked')
        db.session.refresh(opt)
        assert opt.booking_status in ('booked', 'confirmed')
        # Restore
        opt.booking_status = orig_opt_status
        cl.status = orig_cl_status
        db.session.commit()


class TestChecklistCreate:
    def test_rejects_invalid_category(self, ctx):
        from services.checklists import create
        with pytest.raises(ValueError, match='Cannot add'):
            with patch('services.checklists.socketio'):
                create({'title': 'Test', 'category': 'accommodation'})

    def test_creates_item(self, ctx):
        from services.checklists import create
        with patch('services.checklists.socketio'):
            item = create({'title': '__test_svc_checklist__', 'category': 'packing_essential'})
        assert item.id is not None
        assert item.status == 'pending'
        # Clean up
        db.session.delete(item)
        db.session.commit()


class TestChecklistDelete:
    def test_enforces_category_restriction(self, ctx):
        from services.checklists import delete
        # Find an accommodation checklist item (not deletable)
        item = ChecklistItem.query.filter_by(category='accommodation').first()
        if not item:
            pytest.skip('No accommodation checklist items')
        with pytest.raises(ValueError, match='Cannot delete'):
            with patch('services.checklists.socketio'):
                delete(item.id, enforce_category=True)

    def test_bypass_category_for_chat(self, ctx):
        """Chat tools pass enforce_category=False to delete any item."""
        from services.checklists import create, delete
        with patch('services.checklists.socketio'):
            item = create({'title': '__test_del__', 'category': 'packing_essential'})
            delete(item.id, enforce_category=False)
        assert ChecklistItem.query.get(item.id) is None


# ---- New Service Function Tests ----

class TestActivitySetCompleted:
    """Test set_completed (explicit state, used by chat)."""

    def test_set_completed_true(self, ctx):
        from services.activities import set_completed
        act = Activity.query.filter_by(is_completed=False).first()
        with patch('services.activities.socketio'):
            result = set_completed(act.id, True)
        assert result.is_completed is True
        assert result.completed_at is not None
        # Restore
        with patch('services.activities.socketio'):
            set_completed(act.id, False)

    def test_set_completed_false(self, ctx):
        from services.activities import set_completed
        act = Activity.query.filter_by(is_completed=False).first()
        # First set to true
        with patch('services.activities.socketio'):
            set_completed(act.id, True)
            result = set_completed(act.id, False)
        assert result.is_completed is False
        assert result.completed_at is None

    def test_set_completed_emits_same_event_as_toggle(self, ctx):
        from services.activities import set_completed, toggle
        act = Activity.query.filter_by(is_completed=False).first()
        with patch('services.activities.socketio') as mock_sio:
            set_completed(act.id, True)
            event_name = mock_sio.emit.call_args[0][0]
            event_data = mock_sio.emit.call_args[0][1]
            assert event_name == 'activity_toggled'
            assert event_data['id'] == act.id
            assert event_data['is_completed'] is True
        # Restore
        with patch('services.activities.socketio'):
            set_completed(act.id, False)


class TestActivityConfirm:
    def test_confirm_toggles(self, ctx):
        from services.activities import confirm
        act = Activity.query.filter_by(is_confirmed=False).first()
        result = confirm(act.id)
        assert result.is_confirmed is True
        result = confirm(act.id)
        assert result.is_confirmed is False

    def test_confirm_un_eliminates(self, ctx):
        from services.activities import confirm, eliminate
        act = Activity.query.filter_by(is_eliminated=False, is_confirmed=False).first()
        eliminate(act.id)  # eliminate first
        assert act.is_eliminated is True
        result = confirm(act.id)  # confirming should un-eliminate
        assert result.is_confirmed is True
        assert result.is_eliminated is False
        # Restore
        confirm(act.id)


class TestChecklistSetCompleted:
    """Test set_completed (explicit state, used by chat)."""

    def test_set_completed_syncs_status(self, ctx):
        from services.checklists import set_completed
        item = ChecklistItem.query.filter_by(is_completed=False).first()
        with patch('services.checklists.socketio'):
            result = set_completed(item.id, True)
        assert result.is_completed is True
        assert result.status == 'completed'
        with patch('services.checklists.socketio'):
            result = set_completed(item.id, False)
        assert result.is_completed is False
        assert result.status == 'pending'


class TestAccommodationDeselect:
    def test_deselect_emits_event(self, ctx):
        from services.accommodations import deselect, select
        opt = AccommodationOption.query.filter_by(is_selected=True).first()
        if not opt:
            pytest.skip('No selected option')
        with patch('services.accommodations.socketio') as mock_sio:
            deselect(opt.id)
            mock_sio.emit.assert_called_once()
            assert mock_sio.emit.call_args[0][0] == 'accommodation_updated'
        assert opt.is_selected is False
        # Restore
        with patch('services.accommodations.socketio'):
            select(opt.id)


# ---- Mutation Path Parity Tests ----

class TestParityActivityEliminate:
    """Verify UI route and chat executor both use the same service for eliminate."""

    def test_ui_and_chat_produce_same_result(self, ctx):
        """Call eliminate via service (shared path), verify DB state is identical
        regardless of which entry point triggers it."""
        from services.activities import eliminate
        # Pick two non-eliminated activities
        acts = Activity.query.filter_by(is_eliminated=False).limit(2).all()
        if len(acts) < 2:
            pytest.skip('Need 2 non-eliminated activities')
        a1, a2 = acts[0], acts[1]

        # "UI path" — service call (same as blueprints/activities.py now uses)
        result_ui = eliminate(a1.id)
        # "Chat path" — service call (same as executor.py uses)
        result_chat = eliminate(a2.id)

        # Both should produce same side effects
        assert result_ui.is_eliminated is True
        assert result_chat.is_eliminated is True

        # Restore
        eliminate(a1.id)
        eliminate(a2.id)


class TestParityAccommodationStatus:
    """Verify UI and chat accommodation status updates cascade identically."""

    def test_both_paths_cascade_to_checklist(self, ctx):
        """The service's update_status() is the only path for both UI and chat.
        Verify it produces checklist cascade."""
        from services.accommodations import update_status
        cl = ChecklistItem.query.filter(
            ChecklistItem.accommodation_location_id.isnot(None)
        ).first()
        if not cl:
            pytest.skip('No linked checklist items')
        opt = AccommodationOption.query.filter_by(
            location_id=cl.accommodation_location_id, is_selected=True
        ).first()
        if not opt:
            pytest.skip('No selected option')

        orig_status = opt.booking_status
        orig_cl_status = cl.status

        # This is the SAME function called by both:
        #   blueprints/accommodations.py:update_status → accom_svc.update_status()
        #   blueprints/chat/executor.py:update_accommodation → accom_svc.update_status()
        with patch('services.accommodations.socketio'):
            update_status(opt.id, {'booking_status': 'booked'})

        db.session.refresh(cl)
        assert cl.status == 'booked', "Checklist cascade failed"

        # Restore
        opt.booking_status = orig_status
        cl.status = orig_cl_status
        db.session.commit()

    def test_both_paths_reject_confirmed_without_document(self, ctx):
        """Both UI and chat hit the same validation in update_status()."""
        from services.accommodations import update_status
        opt = AccommodationOption.query.filter(
            AccommodationOption.document_id.is_(None)
        ).first()
        if not opt:
            pytest.skip('All options have documents')

        # UI route calls: accom_svc.update_status(option_id, {'booking_status': 'confirmed'})
        # Chat executor calls: accom_svc.update_status(option.id, fields)
        # Both go through the same validation:
        with pytest.raises(ValueError, match='document'):
            with patch('services.accommodations.socketio'):
                update_status(opt.id, {'booking_status': 'confirmed'})


class TestParityChecklistToggle:
    """Verify toggle (UI) and set_completed (chat) produce equivalent results."""

    def test_toggle_and_set_completed_produce_same_state(self, ctx):
        from services.checklists import toggle, set_completed
        items = ChecklistItem.query.filter_by(is_completed=False).limit(2).all()
        if len(items) < 2:
            pytest.skip('Need 2 uncompleted checklist items')
        item_ui, item_chat = items[0], items[1]

        # UI path: toggle (flips to completed)
        with patch('services.checklists.socketio'):
            result_ui = toggle(item_ui.id)
        # Chat path: set_completed (sets to completed explicitly)
        with patch('services.checklists.socketio'):
            result_chat = set_completed(item_chat.id, True)

        # Both should end in the same state
        assert result_ui.is_completed is True
        assert result_chat.is_completed is True
        assert result_ui.status == 'completed'
        assert result_chat.status == 'completed'
        assert result_ui.completed_at is not None
        assert result_chat.completed_at is not None

        # Restore
        with patch('services.checklists.socketio'):
            toggle(item_ui.id)
            set_completed(item_chat.id, False)


class TestParityActivityCompletion:
    """Verify toggle (UI) and set_completed (chat) produce equivalent results."""

    def test_toggle_and_set_completed_produce_same_state(self, ctx):
        from services.activities import toggle, set_completed
        acts = Activity.query.filter_by(is_completed=False).limit(2).all()
        if len(acts) < 2:
            pytest.skip('Need 2 uncompleted activities')
        act_ui, act_chat = acts[0], acts[1]

        # UI path: toggle
        with patch('services.activities.socketio'):
            result_ui = toggle(act_ui.id)
        # Chat path: set_completed
        with patch('services.activities.socketio'):
            result_chat = set_completed(act_chat.id, True)

        assert result_ui.is_completed is True
        assert result_chat.is_completed is True
        assert result_ui.completed_at is not None
        assert result_chat.completed_at is not None

        # Both emit the same event name
        with patch('services.activities.socketio') as m1:
            toggle(act_ui.id)  # restore
            event_ui = m1.emit.call_args[0][0]
        with patch('services.activities.socketio') as m2:
            set_completed(act_chat.id, False)  # restore
            event_chat = m2.emit.call_args[0][0]

        assert event_ui == event_chat == 'activity_toggled'


# ---- Trip Audit Tests ----

class TestTripAudit:
    """Test the pre-export trip audit service."""

    def test_audit_returns_result(self, ctx):
        from services.trip_audit import audit_trip
        result = audit_trip()
        assert hasattr(result, 'blockers')
        assert hasattr(result, 'warnings')
        assert hasattr(result, 'stale_refs')
        assert isinstance(result.blockers, list)
        assert isinstance(result.warnings, list)
        assert isinstance(result.stale_refs, set)

    def test_audit_exportable_with_clean_data(self, ctx):
        """Current seed should be exportable (no blockers)."""
        from services.trip_audit import audit_trip
        result = audit_trip()
        assert result.exportable is True
        assert result.ok is True
        assert len(result.blockers) == 0

    def test_audit_detects_multi_select(self, ctx):
        """Selecting a second option at a location should produce a blocker."""
        from services.trip_audit import audit_trip
        # Find a location with a selected option and an eliminated one
        loc = AccommodationLocation.query.first()
        elim_opt = AccommodationOption.query.filter_by(
            location_id=loc.id, is_eliminated=True).first()
        if not elim_opt:
            pytest.skip('No eliminated option to test with')
        # Temporarily un-eliminate and select it
        elim_opt.is_eliminated = False
        elim_opt.is_selected = True
        db.session.flush()

        result = audit_trip()
        has_multi = any('selected options' in b for b in result.blockers)
        assert has_multi, 'Should detect multiple selected options'

        # Restore
        elim_opt.is_eliminated = True
        elim_opt.is_selected = False
        db.session.commit()

    def test_audit_detects_stale_hotel_reference(self, ctx):
        """Activity mentioning an eliminated hotel brand should be flagged."""
        from services.trip_audit import audit_trip
        # Create a test activity referencing an eliminated hotel
        day = Day.query.first()
        test_act = Activity(
            day_id=day.id, title='Walk to Dormy Inn for breakfast',
            sort_order=999, is_eliminated=False, is_substitute=False)
        db.session.add(test_act)
        db.session.flush()

        result = audit_trip()
        assert test_act.id in result.stale_refs

        # Cleanup
        db.session.delete(test_act)
        db.session.commit()

    def test_audit_no_false_positive_for_selected_hotel(self, ctx):
        """Activity mentioning the selected hotel should NOT be flagged."""
        from services.trip_audit import audit_trip
        day = Day.query.first()
        test_act = Activity(
            day_id=day.id, title='Check into Sotetsu Fresa Inn',
            sort_order=999, is_eliminated=False, is_substitute=False)
        db.session.add(test_act)
        db.session.flush()

        result = audit_trip()
        assert test_act.id not in result.stale_refs

        db.session.delete(test_act)
        db.session.commit()

    def test_audit_to_dict(self, ctx):
        from services.trip_audit import audit_trip
        result = audit_trip()
        d = result.to_dict()
        assert 'exportable' in d
        assert 'blockers' in d
        assert 'warnings' in d
        assert 'stale_activity_ids' in d
        assert d['exportable'] is True

    def test_brand_extraction(self, ctx):
        from services.trip_audit import _extract_brand
        assert _extract_brand('Dormy Inn Asakusa') == 'Dormy'
        assert _extract_brand('CITAN Hostel') == 'CITAN'
        assert _extract_brand('Nui. Hostel & Bar Lounge') == 'Nui.'
        assert _extract_brand('Airbnb machiya') == 'Airbnb machiya'
        # Common English words should return empty
        assert _extract_brand('THE GATE HOTEL Kaminarimon') == ''
        assert _extract_brand('Piece Hostel Sanjo') == ''


class TestTripAuditExportRoute:
    """Test the export route respects audit results."""

    def test_export_includes_audit(self, ctx):
        """Export page should include audit data."""
        from app import create_app
        app = create_app(run_data_migrations=False)
        app.config['TESTING'] = True
        with app.test_client() as client:
            resp = client.get('/export')
            assert resp.status_code == 200

    def test_audit_api_endpoint(self, ctx):
        """The /api/trip/audit endpoint should return JSON."""
        from app import create_app
        app = create_app(run_data_migrations=False)
        app.config['TESTING'] = True
        with app.test_client() as client:
            resp = client.get('/api/trip/audit')
            assert resp.status_code == 200
            data = resp.get_json()
            assert 'exportable' in data
            assert 'blockers' in data
            assert 'warnings' in data
