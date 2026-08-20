from flask import Flask, render_template, send_from_directory, request, jsonify
from datetime import datetime, timezone
import os
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Date, Time, Computed
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import logging
import urllib.parse

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

# Create engine and session
satisfaction_engine = get_satisfaction_engine()
SatisfactionSession = sessionmaker(bind=satisfaction_engine)

def get_satisfaction_session():
    return SatisfactionSession()

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

# ==================== SUBMIT ENDPOINT - WRITES TO org.daily_satisfaction ====================

@app.route('/submit', methods=['POST'])
def submit():
    """Submit satisfaction response - writes to org.daily_satisfaction"""
    db_session = None
    try:
        # Get form data
        mood = request.form.get('mood')  # 👍 or 👎 (emoji from UI)
        location = request.form.get('location')
        score = request.form.get('score')  # 1-10
        feedback_text = request.form.get('comments', '').strip()  # User's written comment
        
        # Validate required fields
        if not location:
            return jsonify({'success': False, 'error': 'Please select your location'}), 400
        
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
