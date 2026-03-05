from flask import Blueprint, render_template
from models import Location, Day

activities_bp = Blueprint('activities', __name__)


@activities_bp.route('/activities')
def activities_view():
    locations = Location.query.order_by(Location.sort_order).all()
    days = Day.query.order_by(Day.day_number).all()

    location_activities = []
    for loc in locations:
        loc_days = [d for d in days if d.location_id == loc.id]
        activities = []
        for d in loc_days:
            for a in d.activities:
                if not a.is_substitute:
                    activities.append({'activity': a, 'day': d})
        if activities:
            location_activities.append({
                'location': loc,
                'activities': activities,
            })

    return render_template('activities.html',
                           location_activities=location_activities)
