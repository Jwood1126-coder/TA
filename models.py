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
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    notes = db.Column(db.Text)

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'time_slot': self.time_slot,
            'start_time': self.start_time,
            'cost_note': self.cost_note,
            'is_optional': self.is_optional,
            'is_substitute': self.is_substitute,
            'is_completed': self.is_completed,
            'jr_pass_covered': self.jr_pass_covered,
            'notes': self.notes,
        }


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
    user_notes = db.Column(db.Text)

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

    def to_dict(self):
        return {
            'id': self.id,
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
            'booking_status': self.booking_status,
            'confirmation_number': self.confirmation_number,
            'user_notes': self.user_notes,
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

    def to_dict(self):
        return {
            'id': self.id,
            'category': self.category,
            'title': self.title,
            'is_completed': self.is_completed,
            'priority': self.priority,
            'item_type': self.item_type,
            'status': self.status,
        }


class ChecklistOption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    checklist_item_id = db.Column(db.Integer,
                                  db.ForeignKey('checklist_item.id'),
                                  nullable=False)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    why = db.Column(db.Text)
    url = db.Column(db.String(500))
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
            'price_note': self.price_note,
            'is_eliminated': self.is_eliminated,
            'is_selected': self.is_selected,
            'user_notes': self.user_notes,
        }


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

    def to_dict(self):
        return {
            'id': self.id,
            'filename': self.filename,
            'thumbnail_filename': self.thumbnail_filename,
            'caption': self.caption,
            'taken_at': self.taken_at.isoformat() if self.taken_at else None,
            'day_id': self.day_id,
        }


class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    image_filename = db.Column(db.String(255))
    context_summary = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ReferenceContent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    section = db.Column(db.String(100), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    sort_order = db.Column(db.Integer)
