from flask import Flask, render_template, send_from_directory, request, jsonify
from datetime import datetime
import json
import os
import requests
import threading
import time
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, Text, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from werkzeug.security import generate_password_hash, check_password_hash
import re
import sys
import logging

app = Flask(__name__)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== DATABASE SETUP WITH SQLALCHEMY ====================

Base = declarative_base()

# Define models
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String(100), unique=True)
    password_hash = Column(String(500))
    created_at = Column(DateTime, default=datetime.now)

class Checkin(Base):
    __tablename__ = 'checkins'
    id = Column(Integer, primary_key=True)
    mood = Column(Text)
    comments = Column(Text)
    submission_date = Column(DateTime)
    ip_address = Column(String(100))
    location = Column(String(100))

class CheckinIssue(Base):
    __tablename__ = 'checkin_issues'
    id = Column(Integer, primary_key=True)
    checkin_id = Column(Integer)
    issue = Column(Text)

class LoginLog(Base):
    __tablename__ = 'login_logs'
    id = Column(Integer, primary_key=True)
    username = Column(String(100))
    login_time = Column(DateTime)
    ip_address = Column(String(100))
    status = Column(String(50))
    user_agent = Column(Text)

class NotificationNumber(Base):
    __tablename__ = 'notification_numbers'
    id = Column(Integer, primary_key=True)
    phone_number = Column(String(50), unique=True)
    name = Column(String(100))
    country = Column(String(50))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)

class SmsLog(Base):
    __tablename__ = 'sms_logs'
    id = Column(Integer, primary_key=True)
    sent_at = Column(DateTime)
    recipients = Column(Integer)
    successful = Column(Integer)
    status = Column(String(50))
    message = Column(Text)

def get_engine():
    """Create database engine for Azure SQL"""
    server = os.environ.get('DB_SERVER', 'safisanadb.database.windows.net')
    database = os.environ.get('DB_NAME', 'safidb')
    username = os.environ.get('DB_USERNAME', '')
    password = os.environ.get('DB_PASSWORD', '')
    
    if not username or not password:
        raise Exception("Database credentials not configured")
    
    # Use ODBC Driver 17 for better compatibility
    connection_string = (
        f"mssql+pyodbc://{username}:{password}@{server}/{database}"
        f"?driver=ODBC+Driver+18+for+SQL+Server"
        f"&Encrypt=yes&TrustServerCertificate=no&Connection+Timeout=30"
    )
    
    logger.info(f"Connecting to Azure SQL: Server={server}, Database={database}")
    
    engine = create_engine(connection_string, echo=False)
    return engine

def init_db():
    """Initialize database tables"""
    try:
        engine = get_engine()
        Base.metadata.create_all(engine)
        logger.info("✅ Database tables created/verified")
        
        # Create admin user if not exists
        Session = sessionmaker(bind=engine)
        session = Session()
        
        admin = session.query(User).filter_by(username='admin').first()
        if not admin:
            hashed_password = generate_password_hash('admin123')
            admin = User(username='admin', password_hash=hashed_password)
            session.add(admin)
            session.commit()
            logger.info("✅ Created admin user")
        else:
            # Update admin password
            admin.password_hash = generate_password_hash('admin123')
            session.commit()
            logger.info("✅ Updated admin password")
        
        session.close()
        return True
    except Exception as e:
        logger.error(f"❌ Database initialization error: {e}")
        import traceback
        traceback.print_exc()
        return False

def get_session():
    """Get a database session"""
    engine = get_engine()
    Session = sessionmaker(bind=engine)
    return Session()

# ==================== WHATSAPP CONFIGURATION ====================
WHATSAPP_TOKEN = os.environ.get('WHATSAPP_TOKEN', '')
PHONE_NUMBER_ID = os.environ.get('PHONE_NUMBER_ID', '')
MOCK_MODE = os.environ.get('MOCK_MODE', 'True').lower() == 'true'

if not MOCK_MODE:
    if not WHATSAPP_TOKEN or WHATSAPP_TOKEN == '':
        logger.warning("⚠️ WHATSAPP_TOKEN not set! Falling back to MOCK_MODE")
        MOCK_MODE = True
    if not PHONE_NUMBER_ID or PHONE_NUMBER_ID == '':
        logger.warning("⚠️ PHONE_NUMBER_ID not set! Falling back to MOCK_MODE")
        MOCK_MODE = True

WHATSAPP_API_URL = f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages"
WHATSAPP_HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_TOKEN}",
    "Content-Type": "application/json"
}

logger.info(f"📱 WhatsApp Mode: {'MOCK' if MOCK_MODE else 'LIVE'}")

# ==================== send_whatsapp_message ====================
def send_whatsapp_message(phone_number, message):
    """Send WhatsApp message using Meta Cloud API"""
    phone_number = re.sub(r'[^0-9]', '', str(phone_number))
    
    if MOCK_MODE:
        logger.info(f"📱 [MOCK] Would send to {phone_number}: {message[:50]}...")
        return True
    
    try:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone_number,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": message
            }
        }
        
        response = requests.post(WHATSAPP_API_URL, headers=WHATSAPP_HEADERS, json=payload, timeout=10)
        
        if response.status_code in [200, 201]:
            logger.info(f"✅ WhatsApp sent to {phone_number}")
            return True
        else:
            logger.error(f"❌ WhatsApp failed: {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"❌ WhatsApp error: {e}")
        return False

# ==================== ROUTES ====================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/admin')
def admin_dashboard():
    return send_from_directory('static', 'admin.html')

@app.route('/health')
def health():
    """Health check endpoint"""
    try:
        session = get_session()
        session.query(User).first()
        session.close()
        return jsonify({'status': 'healthy', 'database': 'connected'})
    except Exception as e:
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 500

@app.route('/api/feedback', methods=['GET'])
def get_feedback():
    """API endpoint for admin dashboard - returns all feedback"""
    try:
        session = get_session()
        
        # Get all checkins
        checkins = session.query(Checkin).order_by(Checkin.submission_date.desc()).all()
        
        feedback = []
        for checkin in checkins:
            # Get issues for this checkin
            issues = session.query(CheckinIssue).filter_by(checkin_id=checkin.id).all()
            issue_texts = [issue.issue for issue in issues]
            
            mood = checkin.mood or ''
            if 'Thumbs Up' in mood or '👍' in mood:
                rating = 'good'
                mood_score = 8
            else:
                rating = 'bad'
                mood_score = 3
            
            dt_obj = checkin.submission_date
            display_timestamp = dt_obj.strftime('%m/%d/%Y, %I:%M:%S %p') if dt_obj else ''
            day = dt_obj.strftime('%A') if dt_obj else ''
            
            feedback.append({
                'id': checkin.id,
                'location': checkin.location or 'Ashaiman',
                'rating': rating,
                'moodScore': mood_score,
                'comment': checkin.comments or '',
                'ip': checkin.ip_address or '127.0.0.1',
                'redFlags': [],
                'timestamp': dt_obj.isoformat() if dt_obj else '',
                'timestampDisplay': display_timestamp,
                'day': day,
                'issues': issue_texts,
                'hasRedFlag': False
            })
        
        session.close()
        logger.info(f"📊 Returning {len(feedback)} feedback entries")
        return jsonify(feedback)
    except Exception as e:
        logger.error(f"❌ Error in get_feedback: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/feedback', methods=['DELETE'])
def delete_feedback():
    data = request.get_json()
    feedback_id = data.get('id')
    
    if not feedback_id:
        return jsonify({'success': False, 'error': 'No ID provided'}), 400
    
    try:
        session = get_session()
        
        # Delete checkin (cascade will delete issues)
        checkin = session.query(Checkin).filter_by(id=feedback_id).first()
        if checkin:
            session.delete(checkin)
            session.commit()
            logger.info(f"🗑️ Deleted feedback ID: {feedback_id}")
            session.close()
            return jsonify({'success': True})
        else:
            session.close()
            return jsonify({'success': False, 'error': 'Feedback not found'}), 404
    except Exception as e:
        logger.error(f"❌ Error deleting feedback: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/submit', methods=['POST'])
def submit():
    try:
        mood = request.form.get('mood')
        location = request.form.get('location') or 'Ashaiman'
        issues_list = request.form.getlist('issues')
        comments = request.form.get('comments', '').strip()
        
        if not mood:
            return jsonify({'success': False, 'error': 'Please select your mood'}), 400
        
        ip_address = request.remote_addr
        current_time = datetime.now().replace(microsecond=0)
        
        session = get_session()
        
        # Create checkin
        checkin = Checkin(
            mood=mood,
            comments=comments,
            submission_date=current_time,
            ip_address=ip_address,
            location=location
        )
        session.add(checkin)
        session.flush()  # Get the ID
        
        # Add issues
        for issue in issues_list:
            checkin_issue = CheckinIssue(
                checkin_id=checkin.id,
                issue=issue
            )
            session.add(checkin_issue)
        
        session.commit()
        session.close()
        
        logger.info(f"✅ Saved: Mood={mood}, Location={location}, Issues={issues_list}, IP={ip_address}")
        return jsonify({'success': True, 'alerts': []})
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== USER AUTHENTICATION ====================

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    user_agent = request.headers.get('User-Agent', 'Unknown')
    ip_address = request.remote_addr
    
    if not username or not password:
        return jsonify({'success': False, 'error': 'Username and password required'}), 400
    
    if not re.match(r'^[a-zA-Z0-9_]{3,30}$', username):
        return jsonify({'success': False, 'error': 'Invalid username format'}), 400
    
    try:
        session = get_session()
        
        user = session.query(User).filter_by(username=username).first()
        
        if user:
            is_valid = False
            try:
                if user.password_hash:
                    is_valid = check_password_hash(user.password_hash, password)
                    logger.info(f"🔐 Password verification for {username}: {'SUCCESS' if is_valid else 'FAILED'}")
            except Exception as e:
                logger.error(f"❌ Password check error: {e}")
                is_valid = False
            
            if is_valid:
                # Log successful login
                login_log = LoginLog(
                    username=username,
                    login_time=datetime.now(),
                    ip_address=ip_address,
                    status='success',
                    user_agent=user_agent
                )
                session.add(login_log)
                session.commit()
                session.close()
                return jsonify({'success': True, 'username': username})
        
        # Log failed login
        login_log = LoginLog(
            username=username,
            login_time=datetime.now(),
            ip_address=ip_address,
            status='failed',
            user_agent=user_agent
        )
        session.add(login_log)
        session.commit()
        session.close()
        return jsonify({'success': False, 'error': 'Invalid credentials'}), 401
        
    except Exception as e:
        logger.error(f"❌ Login error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': 'Server error'}), 500

@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    if not username or not password:
        return jsonify({'success': False, 'error': 'Username and password required'}), 400
    
    if not re.match(r'^[a-zA-Z0-9_]{3,30}$', username):
        return jsonify({'success': False, 'error': 'Username must be 3-30 characters'}), 400
    
    if len(password) < 6:
        return jsonify({'success': False, 'error': 'Password must be at least 6 characters'}), 400
    
    try:
        session = get_session()
        
        # Check if username exists
        existing = session.query(User).filter_by(username=username).first()
        if existing:
            session.close()
            return jsonify({'success': False, 'error': 'Username already taken'}), 400
        
        # Create user
        hashed_password = generate_password_hash(password)
        user = User(
            username=username,
            password_hash=hashed_password,
            created_at=datetime.now()
        )
        session.add(user)
        session.commit()
        session.close()
        
        logger.info(f"✅ User registered: {username}")
        return jsonify({'success': True})
        
    except Exception as e:
        logger.error(f"❌ Registration error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/reset-password', methods=['POST'])
def api_reset_password():
    data = request.get_json()
    username = data.get('username', '').strip()
    new_password = data.get('new_password', '')
    
    if not username or not new_password:
        return jsonify({'success': False, 'error': 'Username and new password required'}), 400
    
    if len(new_password) < 6:
        return jsonify({'success': False, 'error': 'Password must be at least 6 characters'}), 400
    
    try:
        session = get_session()
        
        user = session.query(User).filter_by(username=username).first()
        if not user:
            session.close()
            return jsonify({'success': False, 'error': 'User not found'}), 404
        
        user.password_hash = generate_password_hash(new_password)
        session.commit()
        session.close()
        
        logger.info(f"✅ Password reset for: {username}")
        return jsonify({'success': True})
        
    except Exception as e:
        logger.error(f"❌ Password reset error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/users', methods=['GET'])
def api_get_users():
    try:
        session = get_session()
        users = session.query(User).order_by(User.id).all()
        
        result = []
        for user in users:
            result.append({
                'id': user.id,
                'username': user.username,
                'created_at': user.created_at.isoformat() if user.created_at else None
            })
        
        session.close()
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"❌ Error fetching users: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/delete-user', methods=['POST'])
def api_delete_user():
    data = request.get_json()
    username = data.get('username', '').strip()
    
    if not username:
        return jsonify({'success': False, 'error': 'Username required'}), 400
    
    if username == 'admin':
        return jsonify({'success': False, 'error': 'Cannot delete default admin user'}), 400
    
    try:
        session = get_session()
        
        user = session.query(User).filter_by(username=username).first()
        if not user:
            session.close()
            return jsonify({'success': False, 'error': 'User not found'}), 404
        
        session.delete(user)
        session.commit()
        session.close()
        
        logger.info(f"🗑️ Deleted user: {username}")
        return jsonify({'success': True})
        
    except Exception as e:
        logger.error(f"❌ Delete user error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/login-logs', methods=['GET'])
def api_get_login_logs():
    try:
        session = get_session()
        logs = session.query(LoginLog).order_by(LoginLog.login_time.desc()).limit(200).all()
        
        result = []
        for log in logs:
            result.append({
                'id': log.id,
                'username': log.username,
                'loginTimeDisplay': log.login_time.strftime('%m/%d/%Y, %I:%M:%S %p') if log.login_time else '',
                'ipAddress': log.ip_address,
                'status': log.status,
                'userAgent': log.user_agent[:50] if log.user_agent else 'Unknown'
            })
        
        session.close()
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"❌ Error fetching login logs: {e}")
        return jsonify({'error': str(e)}), 500

# ==================== HEALTH CHECK ====================

@app.route('/api/azure-firewall-fix', methods=['GET'])
def azure_firewall_fix():
    """Get instructions for fixing Azure firewall"""
    return jsonify({
        'message': 'To fix the Azure SQL firewall issue, please add your Render IP to the Azure SQL Server firewall rules.',
        'steps': [
            '1. Go to Azure Portal (portal.azure.com)',
            '2. Navigate to your SQL Server (safisanadb)',
            '3. Click on "Firewalls and virtual networks" in the left menu',
            '4. Click "Add current client IP" or add your IP manually',
            '5. Click "Save"',
            '6. Wait 5 minutes for changes to propagate'
        ],
        'current_ip': request.remote_addr
    })

# ==================== STARTUP ====================

if __name__ == '__main__':
    # Initialize database
    db_initialized = init_db()
    if not db_initialized:
        logger.warning("⚠️ Database initialization failed, but continuing...")
    
    logger.info("=" * 60)
    logger.info("🌍 Safi-Check System Running with Azure SQL Database!")
    logger.info("=" * 60)
    logger.info(f"📱 WhatsApp Mode: {'MOCK' if MOCK_MODE else 'LIVE'}")
    logger.info("=" * 60)
    
    # Start the app - bind to PORT environment variable
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
