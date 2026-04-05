from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Trip(db.Model):
    id = db.Column(db.Integer, primary_key=True, default=1)
    name = db.Column(db.String(200), nullable=False, default='Japan 2026')
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    num_people = db.Column(db.Integer, default=2)
    budget_target_low = db.Column(db.Integer)
    budget_target_high = db.Column(db.Integer)
    notes = db.Column(db.Text)


class Location(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    region = db.Column(db.String(100))
    vibe = db.Column(db.Text)
    why = db.Column(db.Text)
    address = db.Column(db.String(500))
    guide_url = db.Column(db.String(500))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    arrival_date = db.Column(db.Date)
    departure_date = db.Column(db.Date)
    sort_order = db.Column(db.Integer, nullable=False)
    days = db.relationship('Day', backref='location', lazy=True)


class Day(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    day_number = db.Column(db.Integer, nullable=False)
    date = db.Column(db.Date, nullable=False, unique=True)
    title = db.Column(db.String(200), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey('location.id'))
    theme = db.Column(db.String(100))
    is_buffer_day = db.Column(db.Boolean, default=False)
    weather_note = db.Column(db.String(200))
    notes = db.Column(db.Text)
    activities = db.relationship('Activity', backref='day', lazy=True,
                                 order_by='Activity.sort_order')
    photos = db.relationship('Photo', backref='day', lazy=True)

    def completion_pct(self):
        main = [a for a in self.activities if not a.is_substitute]
        if not main:
            return 0
        done = sum(1 for a in main if a.is_completed)
        return int(done / len(main) * 100)


class Activity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    day_id = db.Column(db.Integer, db.ForeignKey('day.id'), nullable=False)
    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text)
    time_slot = db.Column(db.String(50))  # morning, afternoon, evening, night
    start_time = db.Column(db.String(20))
    cost_per_person = db.Column(db.Float)
    cost_note = db.Column(db.String(200))
    is_optional = db.Column(db.Boolean, default=False)
    is_substitute = db.Column(db.Boolean, default=False)
    substitute_for = db.Column(db.String(200))
    is_completed = db.Column(db.Boolean, default=False)
    completed_at = db.Column(db.DateTime)
    jr_pass_covered = db.Column(db.Boolean, default=False)
    address = db.Column(db.String(500))
    maps_url = db.Column(db.String(500))  # direct Google Maps link
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    notes = db.Column(db.Text)
    url = db.Column(db.String(500))
    is_eliminated = db.Column(db.Boolean, default=False)
    category = db.Column(db.String(50))  # temple, food, nightlife, shopping, nature, culture, transit, logistics
    why = db.Column(db.Text)  # reasoning for choosing this activity / comparison notes
    book_ahead = db.Column(db.Boolean, default=False)  # needs advance tickets/reservation
    book_ahead_note = db.Column(db.String(300))  # booking details: how far ahead, where to book
    getting_there = db.Column(db.String(500))  # transit tip: how to get here from previous activity
    is_confirmed = db.Column(db.Boolean, default=False)  # user confirmed this activity is in the schedule


class AccommodationLocation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    location_name = db.Column(db.String(200), nullable=False)
    check_in_date = db.Column(db.Date, nullable=False)
    check_out_date = db.Column(db.Date, nullable=False)
    num_nights = db.Column(db.Integer, nullable=False)
    quick_notes = db.Column(db.Text)
    sort_order = db.Column(db.Integer, nullable=False)
    options = db.relationship('AccommodationOption', backref='accom_location',
                              lazy=True, order_by='AccommodationOption.rank')

    @property
    def nights(self):
        """Derived night count from dates. Always use this for display/logic."""
        if self.check_in_date and self.check_out_date:
            return (self.check_out_date - self.check_in_date).days
        return self.num_nights


class AccommodationOption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    location_id = db.Column(db.Integer,
                            db.ForeignKey('accommodation_location.id'),
                            nullable=False)
    rank = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    property_type = db.Column(db.String(100))
    price_low = db.Column(db.Float)
    price_high = db.Column(db.Float)
    total_low = db.Column(db.Float)
    total_high = db.Column(db.Float)
    breakfast_included = db.Column(db.Boolean, default=False)
    has_onsen = db.Column(db.Boolean, default=False)
    standout = db.Column(db.Text)
    booking_url = db.Column(db.String(500))
    alt_booking_url = db.Column(db.String(500))
    is_selected = db.Column(db.Boolean, default=False)
    is_eliminated = db.Column(db.Boolean, default=False)
    booking_status = db.Column(db.String(50), default='not_booked')
    confirmation_number = db.Column(db.String(100))
    address = db.Column(db.String(500))
    maps_url = db.Column(db.String(500))  # direct Google Maps link
    booking_image = db.Column(db.String(255))  # filename of booking screenshot/confirmation
    check_in_info = db.Column(db.String(200))   # e.g. "3:00 PM" or "after 3pm, front desk"
    check_out_info = db.Column(db.String(200))  # e.g. "11:00 AM"
    phone = db.Column(db.String(50))
    user_notes = db.Column(db.Text)
    document_id = db.Column(db.Integer, db.ForeignKey('document.id'), nullable=True)

    @property
    def price_tier(self):
        if self.price_low is None:
            return ''
        if self.price_low < 60:
            return '$'
        elif self.price_low <= 120:
            return '$$'
        else:
            return '$$$'

    @property
    def is_document_backed(self):
        return self.document_id is not None

    def to_dict(self):
        return {
            'id': self.id,
            'location_id': self.location_id,
            'rank': self.rank,
            'name': self.name,
            'property_type': self.property_type,
            'price_low': self.price_low,
            'price_high': self.price_high,
            'total_low': self.total_low,
            'total_high': self.total_high,
            'breakfast_included': self.breakfast_included,
            'has_onsen': self.has_onsen,
            'standout': self.standout,
            'booking_url': self.booking_url,
            'alt_booking_url': self.alt_booking_url,
            'is_selected': self.is_selected,
            'is_eliminated': self.is_eliminated,
            'booking_status': self.booking_status,
            'confirmation_number': self.confirmation_number,
            'address': self.address,
            'maps_url': self.maps_url,
            'phone': self.phone,
            'check_in_info': self.check_in_info,
            'check_out_info': self.check_out_info,
            'user_notes': self.user_notes,
            'document_id': self.document_id,
            'is_document_backed': self.is_document_backed,
        }


class Flight(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    direction = db.Column(db.String(20), nullable=False)
    leg_number = db.Column(db.Integer, nullable=False)
    flight_number = db.Column(db.String(20), nullable=False)
    airline = db.Column(db.String(50), nullable=False)
    route_from = db.Column(db.String(10), nullable=False)
    route_to = db.Column(db.String(10), nullable=False)
    depart_date = db.Column(db.Date, nullable=False)
    depart_time = db.Column(db.String(30))
    arrive_date = db.Column(db.Date)
    arrive_time = db.Column(db.String(30))
    duration = db.Column(db.String(30))
    aircraft = db.Column(db.String(100))
    cost_type = db.Column(db.String(20))
    cost_amount = db.Column(db.String(100))
    notes = db.Column(db.Text)
    booking_status = db.Column(db.String(50), default='not_booked')
    confirmation_number = db.Column(db.String(100))
    document_id = db.Column(db.Integer, db.ForeignKey('document.id'), nullable=True)


class TransportRoute(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    route_from = db.Column(db.String(100), nullable=False)
    route_to = db.Column(db.String(100), nullable=False)
    transport_type = db.Column(db.String(50), nullable=False)
    train_name = db.Column(db.String(100))
    duration = db.Column(db.String(50))
    jr_pass_covered = db.Column(db.Boolean, default=False)
    cost_if_not_covered = db.Column(db.String(100))
    notes = db.Column(db.Text)
    day_id = db.Column(db.Integer, db.ForeignKey('day.id'))
    sort_order = db.Column(db.Integer)
    maps_url = db.Column(db.String(500))   # Google Maps route/station link
    url = db.Column(db.String(500))        # Operator website or booking link
    route_group = db.Column(db.String(50)) # Groups alternative routes for one movement


class BudgetItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(300), nullable=False)
    estimated_low = db.Column(db.Float)
    estimated_high = db.Column(db.Float)
    actual_amount = db.Column(db.Float)
    currency = db.Column(db.String(10), default='USD')
    notes = db.Column(db.Text)
    sort_order = db.Column(db.Integer)


class ChecklistItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text)
    is_completed = db.Column(db.Boolean, default=False)
    completed_at = db.Column(db.DateTime)
    priority = db.Column(db.String(50))
    sort_order = db.Column(db.Integer)
    url = db.Column(db.String(500))
    item_type = db.Column(db.String(20), default='task')  # 'task' or 'decision'
    status = db.Column(db.String(20), default='pending')   # pending/researching/decided/booked/completed
    accommodation_location_id = db.Column(db.Integer,
                                          db.ForeignKey('accommodation_location.id'),
                                          nullable=True)
    options = db.relationship('ChecklistOption', backref='checklist_item',
                              lazy=True, order_by='ChecklistOption.sort_order')
    accommodation_location = db.relationship('AccommodationLocation',
                                             backref='checklist_item')


class ChecklistOption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    checklist_item_id = db.Column(db.Integer,
                                  db.ForeignKey('checklist_item.id'),
                                  nullable=False)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    why = db.Column(db.Text)
    url = db.Column(db.String(500))
    maps_url = db.Column(db.String(500))
    price_note = db.Column(db.String(100))
    is_eliminated = db.Column(db.Boolean, default=False)
    is_selected = db.Column(db.Boolean, default=False)
    user_notes = db.Column(db.Text)
    sort_order = db.Column(db.Integer, default=0)

    def to_dict(self):
        return {
            'id': self.id,
            'checklist_item_id': self.checklist_item_id,
            'name': self.name,
            'description': self.description,
            'why': self.why,
            'url': self.url,
            'maps_url': self.maps_url,
            'price_note': self.price_note,
            'is_eliminated': self.is_eliminated,
            'is_selected': self.is_selected,
            'user_notes': self.user_notes,
        }


class Document(db.Model):
    """An uploaded file that proves a booking or reservation.
    This is the root of the trust chain — documents are the source of truth."""
    id = db.Column(db.Integer, primary_key=True)

    # File storage
    filename = db.Column(db.String(255), nullable=False)       # Stored filename (uuid__original)
    original_name = db.Column(db.String(255))                   # User's original filename
    file_type = db.Column(db.String(10))                        # pdf, png, jpg
    file_size = db.Column(db.Integer)                           # Bytes

    # Classification
    doc_type = db.Column(db.String(30), nullable=False)
    # Values: accommodation_booking, flight_receipt, transport_ticket,
    #         activity_ticket, insurance, passport, visa, other

    # Extracted data (parsed from the document)
    extracted_data = db.Column(db.Text)                         # JSON blob of parsed fields

    # Metadata
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text)

    # Relationships
    accommodations = db.relationship('AccommodationOption', backref='document')
    flights = db.relationship('Flight', backref='document')


class Photo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    day_id = db.Column(db.Integer, db.ForeignKey('day.id'))
    filename = db.Column(db.String(255), nullable=False)
    original_filename = db.Column(db.String(255))
    thumbnail_filename = db.Column(db.String(255))
    caption = db.Column(db.Text)
    taken_at = db.Column(db.DateTime)
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    file_size = db.Column(db.Integer)
    width = db.Column(db.Integer)
    height = db.Column(db.Integer)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)


class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    image_filename = db.Column(db.String(255))
    context_summary = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class GmailSyncLog(db.Model):
    """Log of Gmail sync runs."""
    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)
    status = db.Column(db.String(20), default='running')  # running, completed, failed
    emails_found = db.Column(db.Integer, default=0)
    changes_detected = db.Column(db.Integer, default=0)
    errors = db.Column(db.Text)


class PendingGmailChange(db.Model):
    """A proposed change detected from a Gmail email, awaiting user review."""
    id = db.Column(db.Integer, primary_key=True)
    gmail_message_id = db.Column(db.String(100), nullable=False)
    email_subject = db.Column(db.String(500))
    email_from = db.Column(db.String(200))
    email_date = db.Column(db.String(100))
    change_type = db.Column(db.String(20), nullable=False)  # create, update, cancel, none
    entity_type = db.Column(db.String(30), nullable=False)  # accommodation, flight, activity, transport, other
    entity_id = db.Column(db.Integer)  # FK to the record being updated (null for creates)
    description = db.Column(db.String(500), nullable=False)
    proposed_data = db.Column(db.Text)  # JSON of proposed field values
    current_data = db.Column(db.Text)   # JSON of current field values (for comparison)
    consequence = db.Column(db.Text)     # what exactly happens when approved (shown to user)
    confidence = db.Column(db.String(20))  # high, medium, low — from Opus analysis
    opus_reasoning = db.Column(db.Text)  # why Opus recommended this change
    status = db.Column(db.String(20), default='pending')  # pending, approved, rejected, skipped, failed
    detected_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at = db.Column(db.DateTime)
    errors = db.Column(db.Text)


class ReferenceContent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    section = db.Column(db.String(100), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    sort_order = db.Column(db.Integer)
