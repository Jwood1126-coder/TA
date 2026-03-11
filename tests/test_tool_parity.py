"""Tool/backend contract parity tests.

These tests assert that chat tool schemas stay in sync with backend
canonical constants. If a test here fails, it means someone changed a
backend enum or constant without updating the tool layer (or vice versa).

Run: python -m pytest tests/test_tool_parity.py -v
"""
import pytest
from app import create_app
from blueprints.chat.tools import TOOLS
from blueprints.chat.executor import execute_tool
from guardrails import (
    VALID_BOOKING_STATUSES, VALID_TIME_SLOTS,
    VALID_CATEGORIES, VALID_TRANSPORT_TYPES,
)
from services.checklists import ADDABLE_CATEGORIES, VALID_PRIORITIES


@pytest.fixture(scope='module')
def app():
    app = create_app()
    with app.app_context():
        yield app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_tool(name):
    """Find a tool definition by name."""
    for t in TOOLS:
        if t['name'] == name:
            return t
    raise KeyError(f"Tool '{name}' not found in TOOLS")


def _get_enum(tool_name, prop_name):
    """Extract the enum list from a tool property."""
    tool = _get_tool(tool_name)
    prop = tool['input_schema']['properties'].get(prop_name)
    if not prop:
        raise KeyError(f"Property '{prop_name}' not found in tool '{tool_name}'")
    return prop.get('enum')


# ---------------------------------------------------------------------------
# A. Enum parity — tool enums must match canonical backend sets
# ---------------------------------------------------------------------------

class TestEnumParity:
    """Every tool enum must exactly match its canonical backend source."""

    def test_flight_booking_status(self):
        enum = _get_enum('update_flight', 'booking_status')
        assert set(enum) == VALID_BOOKING_STATUSES, \
            f"update_flight.booking_status {set(enum)} != guardrails.VALID_BOOKING_STATUSES {VALID_BOOKING_STATUSES}"

    def test_accommodation_booking_status(self):
        enum = _get_enum('update_accommodation', 'booking_status')
        assert set(enum) == VALID_BOOKING_STATUSES

    def test_activity_time_slot(self):
        enum = _get_enum('update_activity', 'time_slot')
        assert set(enum) == VALID_TIME_SLOTS

    def test_activity_category(self):
        enum = _get_enum('update_activity', 'category')
        assert set(enum) == VALID_CATEGORIES

    def test_checklist_category(self):
        enum = _get_enum('add_checklist_item', 'category')
        assert set(enum) == ADDABLE_CATEGORIES

    def test_checklist_priority(self):
        enum = _get_enum('add_checklist_item', 'priority')
        assert set(enum) == VALID_PRIORITIES

    def test_add_transport_type(self):
        enum = _get_enum('add_transport_route', 'transport_type')
        assert set(enum) == VALID_TRANSPORT_TYPES

    def test_update_transport_type(self):
        enum = _get_enum('update_transport_route', 'transport_type')
        assert set(enum) == VALID_TRANSPORT_TYPES


# ---------------------------------------------------------------------------
# B. Tool/executor coverage — every TOOLS entry has a handler in executor
# ---------------------------------------------------------------------------

class TestToolExecutorCoverage:
    """Every tool defined in TOOLS must have a handler in execute_tool."""

    def test_all_tools_have_handlers(self, app):
        """Call each tool with obviously wrong input — should get a structured
        error, not an unhandled exception or silent fallthrough."""
        for tool in TOOLS:
            name = tool['name']
            # Build minimal input with required fields as dummy values
            required = tool['input_schema'].get('required', [])
            dummy = {}
            for field in required:
                prop = tool['input_schema']['properties'][field]
                if prop['type'] == 'string':
                    dummy[field] = '__parity_test_nonexistent__'
                elif prop['type'] == 'integer':
                    dummy[field] = 99999
                elif prop['type'] == 'number':
                    dummy[field] = 0.0
                elif prop['type'] == 'boolean':
                    dummy[field] = False
            result = execute_tool(name, dummy)
            assert result is not None, f"Tool '{name}' returned None — missing handler?"
            assert isinstance(result, dict), f"Tool '{name}' returned non-dict: {type(result)}"
            # Should have either success or error key
            assert 'success' in result, f"Tool '{name}' result missing 'success' key: {result}"

    def test_no_orphan_tools(self):
        """Every tool in TOOLS must have a unique name."""
        names = [t['name'] for t in TOOLS]
        assert len(names) == len(set(names)), f"Duplicate tool names: {[n for n in names if names.count(n) > 1]}"


# ---------------------------------------------------------------------------
# C. Day number range — tool descriptions must match actual DB
# ---------------------------------------------------------------------------

class TestDayRange:
    """Day number descriptions must match the actual trip length."""

    def test_day_number_descriptions_match_db(self, app):
        from models import Day
        max_day = max(d.day_number for d in Day.query.all())
        # Check every tool that has a day_number property
        for tool in TOOLS:
            props = tool['input_schema'].get('properties', {})
            if 'day_number' not in props:
                continue
            desc = props['day_number'].get('description', '')
            # Description should contain the correct range
            expected = f"1-{max_day}"
            assert expected in desc, \
                f"Tool '{tool['name']}' day_number description says '{desc}' " \
                f"but DB has days 1-{max_day}. Update the description."


# ---------------------------------------------------------------------------
# D. Schema structure — tools must have valid JSON Schema structure
# ---------------------------------------------------------------------------

class TestSchemaStructure:
    """Basic structural checks on tool schemas."""

    def test_all_tools_have_required_fields(self):
        for tool in TOOLS:
            assert 'name' in tool, f"Tool missing 'name': {tool}"
            assert 'description' in tool, f"Tool '{tool.get('name')}' missing 'description'"
            assert 'input_schema' in tool, f"Tool '{tool['name']}' missing 'input_schema'"
            schema = tool['input_schema']
            assert schema.get('type') == 'object', f"Tool '{tool['name']}' schema type must be 'object'"
            assert 'properties' in schema, f"Tool '{tool['name']}' schema missing 'properties'"

    def test_required_fields_exist_in_properties(self):
        """Every field listed in 'required' must exist in 'properties'."""
        for tool in TOOLS:
            required = tool['input_schema'].get('required', [])
            properties = tool['input_schema']['properties']
            for field in required:
                assert field in properties, \
                    f"Tool '{tool['name']}' requires '{field}' but it's not in properties"

    def test_enum_values_are_nonempty_lists(self):
        """Any property with an 'enum' must have a non-empty list."""
        for tool in TOOLS:
            for prop_name, prop in tool['input_schema']['properties'].items():
                if 'enum' in prop:
                    assert isinstance(prop['enum'], list), \
                        f"Tool '{tool['name']}'.{prop_name} enum is not a list"
                    assert len(prop['enum']) > 0, \
                        f"Tool '{tool['name']}'.{prop_name} enum is empty"
