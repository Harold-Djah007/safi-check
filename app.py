from flask import Flask, render_template, send_from_directory, request, jsonify
from datetime import datetime, timezone
import json
import os
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Date, Time, Computed
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import logging
import urllib.parse
import urllib.request

app = Flask(__name__)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== DATABASE SETUP ====================

Base = declarative_base()

# ==================== MODEL FOR SATISFACTION (org.daily_satisfaction) ====================
class DailySatisfaction(Base):
    __tablename__ = 'daily_satisfaction'
    __table_args__ = {'schema': 'org'}
    
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, nullable=False)
    score = Column(Integer)
    how = Column(String(100))  # Stores: "good" or "bad" (normalized)
    where = Column("where", String(100))  # 'where' is a reserved word in SQL
    
    # COMPUTED columns - SQL Server calculates these from timestamp
    date = Column(Date, Computed("CONVERT(date, [timestamp])"))
    time = Column(Time, Computed("CONVERT(time, [timestamp])"))
    satisfaction_perc = Column(Integer, Computed("CASE WHEN how = 'good' THEN 1 ELSE 0 END"))
    
    feedback = Column(Text, nullable=True)  # User's written comment

# ==================== DATABASE CONNECTION ====================

def get_satisfaction_engine():
    """Create engine for satisfaction database (org.daily_satisfaction) using satisfaction_writer"""
    server = os.environ.get('DB_SERVER', 'safisanadb.database.windows.net')
    database = os.environ.get('DB_NAME', 'safidb')
    username = os.environ.get('SATISFACTION_USERNAME', '')
    password = os.environ.get('SATISFACTION_PASSWORD', '')
    
    if not username or not password:
        raise Exception("Satisfaction database credentials not configured")
    
    encoded_password = urllib.parse.quote_plus(password)
    
    connection_string = (
        f"mssql+pyodbc://{username}:{encoded_password}@{server}:1433/{database}"
        "?driver=ODBC+Driver+18+for+SQL+Server"
        "&Encrypt=yes&TrustServerCertificate=no"
    )
    
    logger.info(f"Connecting to Satisfaction DB: Server={server}, Database={database}")
    
    engine = create_engine(
        connection_string,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        echo=False,
        connect_args={"timeout": 30}
    )
    return engine

# Lazy engine so location helpers can run without DB credentials (e.g. local tests)
_satisfaction_engine = None
SatisfactionSession = None

def get_satisfaction_session():
    global _satisfaction_engine, SatisfactionSession
    if SatisfactionSession is None:
        _satisfaction_engine = get_satisfaction_engine()
        SatisfactionSession = sessionmaker(bind=_satisfaction_engine)
    return SatisfactionSession()


# ==================== LOCATION NORMALIZATION ====================
# Check-ins are stored under the three site names only:
#   Accra metro (and nearby Greater Accra) → Ashaiman
#   Kumasi metro → Kumasi
#   Weesp / Amsterdam-area NL → Weesp

DEFAULT_LOCATION = "Ashaiman"

# (min_lat, max_lat, min_lon, max_lon)
_REGION_BOXES = (
    ("Kumasi", (6.50, 6.95, -1.85, -1.35)),
    ("Weesp", (52.20, 52.50, 4.70, 5.30)),
    ("Ashaiman", (5.28, 5.92, -0.55, 0.22)),  # Greater Accra incl. Accra, Tema, Ashaiman
)

_ACCRA_ALIASES = (
    "accra", "greater accra", "tema", "ashaiman", "ashiaman", "madina",
    "teshie", "nungua", "spintex", "legon", "adenta", "kasoa", "weija",
    "dansoman", "labadi", "osu", "cantonments", "achimota", "dome",
    "haatso", "kwabenya", "sakumono", "prampram", "ablekuma", "kaneshie",
    "circle", "airport residential", "east legon", "lakeside",
)

_KUMASI_ALIASES = (
    "kumasi", "ashanti", "kwadaso", "ejisu", "knust", "bantama",
    "asuoyeboah", "asuofua", "suame", "tafo", "ayeduase", "kentinkrono",
)

_WEESP_ALIASES = (
    "weesp", "amsterdam", "netherlands", "nederland", "holland",
    "noord-holland", "north holland", "diemen", "muiden", "naarden",
    "bussum", "almere", "hilversum", "amstelveen",
)


def location_from_coords(lat, lon):
    """Map GPS coordinates to a store location, or None if outside known areas."""
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None

    for name, (min_lat, max_lat, min_lon, max_lon) in _REGION_BOXES:
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            return name
    return None


def match_location_from_text(*parts):
    """Return a store name only when the text clearly matches a site. Never guess."""
    blob = " ".join(str(p) for p in parts if p).strip().lower()
    if not blob:
        return None

    if any(alias in blob for alias in _KUMASI_ALIASES):
        return "Kumasi"
    if any(alias in blob for alias in _WEESP_ALIASES):
        return "Weesp"
    if any(alias in blob for alias in _ACCRA_ALIASES):
        return "Ashaiman"
    return None


def normalize_location(*parts):
    """Map free-text place names onto Ashaiman, Kumasi, or Weesp."""
    return match_location_from_text(*parts) or DEFAULT_LOCATION


def _nominatim_reverse(lat, lon):
    url = (
        "https://nominatim.openstreetmap.org/reverse"
        f"?lat={lat}&lon={lon}&format=json&zoom=10&addressdetails=1"
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Safi-Check/1.0 (check-in location resolver)"},
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


def resolve_store_location(lat=None, lon=None, hint=None):
    """Resolve coords and/or a text hint to a canonical store location."""
    lat = None if lat in (None, "") else lat
    lon = None if lon in (None, "") else lon

    by_coords = location_from_coords(lat, lon)
    if by_coords:
        return by_coords

    if lat is not None and lon is not None:
        try:
            data = _nominatim_reverse(lat, lon)
            addr = data.get("address") or {}
            return match_location_from_text(
                addr.get("city"),
                addr.get("town"),
                addr.get("village"),
                addr.get("suburb"),
                addr.get("municipality"),
                addr.get("county"),
                addr.get("state"),
                addr.get("country"),
                data.get("display_name"),
            )
        except Exception as e:
            logger.warning(f"Nominatim reverse geocode failed: {e}")

    return match_location_from_text(hint)

# ==================== ROUTES ====================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/health')
def health():
    """Health check endpoint"""
    try:
        db_session = get_satisfaction_session()
        db_session.query(DailySatisfaction).first()
        db_session.close()
        return jsonify({'status': 'healthy', 'database': 'connected'})
    except Exception as e:
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 500


@app.route('/resolve-location', methods=['POST'])
def resolve_location():
    """Resolve browser GPS (or a text hint) to Ashaiman, Kumasi, or Weesp."""
    payload = request.get_json(silent=True) or {}
    lat = payload.get('latitude', payload.get('lat'))
    lon = payload.get('longitude', payload.get('lon'))
    hint = payload.get('hint') or payload.get('location')
    location = resolve_store_location(lat, lon, hint)
    return jsonify({'success': True, 'location': location})

@app.route('/submit', methods=['POST'])
def submit():
    """Submit satisfaction response - writes to org.daily_satisfaction"""
    db_session = None
    try:
        # Get form data
        mood = request.form.get('mood')  # 👍 or 👎 (emoji from UI)
        lat = request.form.get('latitude') or request.form.get('lat')
        lon = request.form.get('longitude') or request.form.get('lon')
        score = request.form.get('score')  # 1-10
        feedback_text = request.form.get('comments', '').strip()  # User's written comment

        # Only accept a site that GPS coordinates actually map to.
        # Never default to Ashaiman when coords are missing or the lookup failed.
        location = location_from_coords(lat, lon)
        if not location:
            return jsonify({
                'success': False,
                'error': 'Please wait until your location is detected'
            }), 400
        
        if not mood:
            return jsonify({'success': False, 'error': 'Please select your mood'}), 400
        
        # Normalize the mood: 👍 → "good", 👎 → "bad"
        positive = ('👍' in mood) or (mood.lower() == 'good') or ('thumbs up' in mood.lower())
        
        if positive:
            how_text = "good"
            score_value = 8
        else:
            how_text = "bad"
            score_value = 3
        
        # Override score if provided
        if score:
            try:
                score_value = int(score)
            except:
                pass
        
        current_time = datetime.now(timezone.utc)
        
        # Insert into org.daily_satisfaction using satisfaction_writer
        # Note: date, time, and satisfaction_perc are COMPUTED columns
        # SQL Server calculates them automatically - DO NOT INSERT INTO THEM
        db_session = get_satisfaction_session()
        
        satisfaction = DailySatisfaction(
            timestamp=current_time,
            score=score_value,
            how=how_text,  # "good" or "bad" (normalized)
            where=location,
            feedback=feedback_text if feedback_text else None  # User's written comment
            # date is COMPUTED - do not include
            # time is COMPUTED - do not include
            # satisfaction_perc is COMPUTED - do not include
        )
        
        db_session.add(satisfaction)
        db_session.commit()
        db_session.close()
        
        logger.info(f"✅ Saved satisfaction: How={how_text}, Location={location}, Score={score_value}, Feedback={feedback_text[:50] if feedback_text else 'None'}")
        return jsonify({'success': True, 'alerts': []})
        
    except Exception as e:
        if db_session:
            db_session.rollback()
            db_session.close()
        logger.error(f"❌ Error in submit: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== STARTUP ====================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
