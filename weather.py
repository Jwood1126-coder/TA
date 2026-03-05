"""Weather and currency data with file-based caching."""
import json
import os
import time
import urllib.request

CACHE_DIR = os.path.join(os.path.dirname(__file__), 'data')
WEATHER_CACHE = os.path.join(CACHE_DIR, 'weather_cache.json')
CURRENCY_CACHE = os.path.join(CACHE_DIR, 'currency_cache.json')
WEATHER_TTL = 6 * 3600   # 6 hours
CURRENCY_TTL = 4 * 3600  # 4 hours


def _read_cache(filepath, ttl):
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
        if time.time() - data.get('timestamp', 0) < ttl:
            return data.get('payload')
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    return None


def _write_cache(filepath, payload):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w') as f:
        json.dump({'timestamp': time.time(), 'payload': payload}, f)


def get_weather_data(days, location_groups):
    """Fetch 16-day forecast from Open-Meteo for each unique location.

    Returns dict keyed by day_number -> {icon, temp_high, temp_low, rain_pct}.
    """
    cached = _read_cache(WEATHER_CACHE, WEATHER_TTL)
    if cached:
        return cached

    # Collect unique locations with coordinates
    seen = set()
    locations = []
    for group in location_groups:
        loc = group.get('location_obj')
        if loc and loc.latitude and loc.name not in seen:
            seen.add(loc.name)
            locations.append({
                'name': loc.name,
                'lat': loc.latitude,
                'lon': loc.longitude,
            })

    weather_by_day = {}

    for loc_info in locations:
        try:
            url = (
                f"https://api.open-meteo.com/v1/forecast?"
                f"latitude={loc_info['lat']}&longitude={loc_info['lon']}"
                f"&daily=temperature_2m_max,temperature_2m_min,"
                f"precipitation_probability_max,weather_code"
                f"&temperature_unit=fahrenheit"
                f"&timezone=Asia/Tokyo&forecast_days=16"
            )
            resp = urllib.request.urlopen(url, timeout=5)
            data = json.loads(resp.read())
            daily = data.get('daily', {})
            dates = daily.get('time', [])
            for i, date_str in enumerate(dates):
                for day in days:
                    if (day.date.isoformat() == date_str
                            and day.location
                            and day.location.name == loc_info['name']):
                        weather_by_day[str(day.day_number)] = {
                            'temp_high': daily['temperature_2m_max'][i],
                            'temp_low': daily['temperature_2m_min'][i],
                            'rain_pct': daily['precipitation_probability_max'][i],
                            'code': daily['weather_code'][i],
                            'icon': _weather_icon(daily['weather_code'][i]),
                        }
        except Exception:
            continue

    _write_cache(WEATHER_CACHE, weather_by_day)
    return weather_by_day


def _weather_icon(code):
    """Map WMO weather code to emoji."""
    if code <= 1:
        return '\u2600\ufe0f'       # sunny
    elif code <= 3:
        return '\u26c5'             # partly cloudy
    elif code <= 48:
        return '\u2601\ufe0f'       # cloudy/fog
    elif code <= 67:
        return '\U0001f327\ufe0f'   # rain
    elif code <= 77:
        return '\u2744\ufe0f'       # snow
    elif code <= 82:
        return '\U0001f327\ufe0f'   # rain showers
    elif code <= 86:
        return '\u2744\ufe0f'       # snow showers
    else:
        return '\u26a1'             # thunderstorm


def get_exchange_rate():
    """Fetch USD->JPY rate from Frankfurter API with caching."""
    cached = _read_cache(CURRENCY_CACHE, CURRENCY_TTL)
    if cached:
        return cached

    try:
        url = "https://api.frankfurter.app/latest?from=USD&to=JPY"
        resp = urllib.request.urlopen(url, timeout=5)
        data = json.loads(resp.read())
        rate = data['rates']['JPY']
        result = {'rate': rate, 'updated': data.get('date', '')}
        _write_cache(CURRENCY_CACHE, result)
        return result
    except Exception:
        return {'rate': None, 'updated': ''}
