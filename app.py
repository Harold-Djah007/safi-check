from flask import Flask, render_template, send_from_directory, request, jsonify
from datetime import datetime
import json
import os
import requests
import threading
import time
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool
from werkzeug.security import generate_password_hash, check_password_hash
import atexit
import re

app = Flask(__name__)

# ==================== DATABASE CONNECTION POOL ====================
# Connection pool for better performance
db_pool = None

def init_db_pool():
    """Initialize PostgreSQL connection pool"""
    global db_pool
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise Exception("DATABASE_URL environment variable is required. Please set it in Render.")
    
    # Parse connection string for pool
    # Simple pool with min 1, max 10 connections
    db_pool = SimpleConnectionPool(1, 10, database_url)
    print("✅ PostgreSQL connection pool initialized (min=1, max=10)")
    return db_pool

def get_db_connection():
    """Get a connection from the pool"""
    if db_pool is None:
        init_db_pool()
    return db_pool.getconn()

def return_db_connection(conn):
    """Return connection to the pool"""
    if db_pool and conn:
        db_pool.putconn(conn)

# ==================== WHATSAPP CONFIGURATION ====================
WHATSAPP_TOKEN = os.environ.get('WHATSAPP_TOKEN', '')
PHONE_NUMBER_ID = os.environ.get('PHONE_NUMBER_ID', '')
MOCK_MODE = os.environ.get('MOCK_MODE', 'True').lower() == 'true'

if not MOCK_MODE:
    if not WHATSAPP_TOKEN or WHATSAPP_TOKEN == '':
        print("⚠️ WARNING: WHATSAPP_TOKEN not set! Falling back to MOCK_MODE")
        MOCK_MODE = True
    if not PHONE_NUMBER_ID or PHONE_NUMBER_ID == '':
        print("⚠️ WARNING: PHONE_NUMBER_ID not set! Falling back to MOCK_MODE")
        MOCK_MODE = True

WHATSAPP_API_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
WHATSAPP_HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_TOKEN}",
    "Content-Type": "application/json"
}

print(f"📱 WhatsApp Mode: {'MOCK' if MOCK_MODE else 'LIVE'}")

def send_whatsapp_message(phone_number, message):
    """Send WhatsApp message using Meta Cloud API"""
    if MOCK_MODE:
        print(f"📱 [MOCK MODE] Would send to {phone_number}: {message}")
        return True
    
    try:
        if not phone_number.startswith('+'):
            phone_number = '+' + phone_number
        
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
        
        response = requests.post(WHATSAPP_API_URL, headers=WHATSAPP_HEADERS, json=payload)
        
        if response.status_code == 200:
            print(f"✅ WhatsApp message sent to {phone_number}")
            return True
        else:
            print(f"❌ WhatsApp API error: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ WhatsApp send error: {e}")
        return False

# ==================== DATABASE SETUP (PostgreSQL) ====================

def init_db():
    """Initialize PostgreSQL database tables"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Checkins table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS checkins (
                id SERIAL PRIMARY KEY,
                mood TEXT,
                comments TEXT,
                submission_date TIMESTAMP,
                ip_address TEXT,
                location TEXT
            )
        """)

        # Checkin issues table with FOREIGN KEY and ON DELETE CASCADE
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS checkin_issues (
                id SERIAL PRIMARY KEY,
                checkin_id INTEGER REFERENCES checkins(id) ON DELETE CASCADE,
                issue TEXT
            )
        """)

        # Alerts table with FOREIGN KEY and ON DELETE CASCADE
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id SERIAL PRIMARY KEY,
                checkin_id INTEGER REFERENCES checkins(id) ON DELETE CASCADE,
                keyword TEXT,
                severity TEXT,
                details TEXT,
                acknowledged BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Users table with hashed passwords
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE,
                password_hash TEXT,
                created_at TIMESTAMP
            )
        """)

        # Notification numbers table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notification_numbers (
                id SERIAL PRIMARY KEY,
                phone_number TEXT UNIQUE,
                name TEXT,
                country TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP
            )
        """)

        # SMS logs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sms_logs (
                id SERIAL PRIMARY KEY,
                sent_at TIMESTAMP,
                recipients INTEGER,
                successful INTEGER,
                status TEXT,
                message TEXT
            )
        """)

        # Login logs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS login_logs (
                id SERIAL PRIMARY KEY,
                username TEXT,
                login_time TIMESTAMP,
                ip_address TEXT,
                status TEXT,
                user_agent TEXT
            )
        """)

        # Create indexes for performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_checkins_date ON checkins(submission_date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_checkin ON alerts(checkin_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_checkins_location ON checkins(location)")

        # Check if admin exists, if not create default with hashed password
        cursor.execute("SELECT * FROM users WHERE username = 'admin'")
        if not cursor.fetchone():
            hashed_password = generate_password_hash('admin123')
            cursor.execute("""
                INSERT INTO users (username, password_hash, created_at)
                VALUES (%s, %s, %s)
            """, ('admin', hashed_password, datetime.now()))

        conn.commit()
        print("✅ PostgreSQL database initialized with indexes and foreign keys")
    except Exception as e:
        print(f"❌ Database initialization error: {e}")
        raise
    finally:
        if conn:
            return_db_connection(conn)

def insert_test_numbers():
    """Automatically add test phone numbers to database on startup"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        test_numbers = [
            ('+233550157210', 'Ghana Number 1', 'Ghana', 1),
            ('+233506896041', 'Ghana Number 2', 'Ghana', 1),
            ('+15556664486', 'Meta Test Number', 'USA', 1),
        ]
        
        for number, name, country, active in test_numbers:
            cursor.execute("""
                INSERT INTO notification_numbers (phone_number, name, country, is_active, created_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (phone_number) DO UPDATE SET
                    name = EXCLUDED.name,
                    country = EXCLUDED.country,
                    is_active = EXCLUDED.is_active
            """, (number, name, country, active, datetime.utcnow()))
        
        conn.commit()
        print(f"✅ Seeded {len(test_numbers)} test number(s)")
    except Exception as e:
        print(f"❌ Failed to seed database: {e}")
    finally:
        if conn:
            return_db_connection(conn)

# Initialize database pool and tables
init_db_pool()
init_db()
insert_test_numbers()

# ==================== SCHEDULER LOCK (Prevents duplicate schedulers) ====================
class SchedulerLock:
    def __init__(self):
        self.lock_acquired = False
        self.conn = None
        self.cursor = None
    
    def acquire(self):
        """Try to acquire scheduler lock using PostgreSQL advisory lock"""
        try:
            self.conn = get_db_connection()
            self.cursor = self.conn.cursor()
            # PostgreSQL advisory lock (unique per app)
            self.cursor.execute("SELECT pg_try_advisory_lock(12345)")
            self.lock_acquired = self.cursor.fetchone()[0]
            if self.lock_acquired:
                print("✅ Scheduler lock acquired")
            else:
                print("⚠️ Scheduler lock already held by another instance")
            return self.lock_acquired
        except Exception as e:
            print(f"❌ Failed to acquire scheduler lock: {e}")
            return False
    
    def release(self):
        """Release the advisory lock"""
        if self.lock_acquired and self.cursor:
            try:
                self.cursor.execute("SELECT pg_advisory_unlock(12345)")
                self.conn.commit()
                print("✅ Scheduler lock released")
            except Exception as e:
                print(f"❌ Failed to release scheduler lock: {e}")
            finally:
                if self.conn:
                    return_db_connection(self.conn)
                    self.conn = None
                    self.cursor = None
        self.lock_acquired = False

# ==================== WHATSAPP SCHEDULER ====================

class NotificationScheduler:
    def __init__(self):
        self.last_sent_date = None
        self.running = True
        self.target_hour_utc = 16  # 4 PM UTC
        self.target_minute = 30    # 4:30 PM UTC
        self.lock = None
        self.is_scheduler_active = False
    
    def get_active_numbers(self):
        """Get phone numbers from database"""
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT phone_number, name, country FROM notification_numbers WHERE is_active = TRUE")
            numbers = [{'number': row[0], 'name': row[1], 'country': row[2]} for row in cursor.fetchall()]
            return numbers
        except Exception as e:
            print(f"❌ Error getting active numbers: {e}")
            return []
        finally:
            if conn:
                return_db_connection(conn)
    
    def check_and_send(self):
        now = datetime.utcnow()
        current_hour = now.hour
        current_minute = now.minute
        today = now.date()
        
        if current_hour == self.target_hour_utc and current_minute == self.target_minute:
            if self.last_sent_date != today:
                self.send_notifications()
                self.last_sent_date = today
    
    def send_notifications(self):
        message = "⏰ SafiCheck Reminder: It's 4:30 PM UTC! Time for daily check-in. Please submit your feedback at: https://safi-check.onrender.com"
        
        recipients = self.get_active_numbers()
        
        if not recipients:
            print(f"⚠️ No active phone numbers found.")
            return
        
        print(f"\n🔔 Sending WhatsApp notifications at {datetime.utcnow().isoformat()} UTC")
        print(f"📱 Target recipients: {len(recipients)} people")
        
        success_count = 0
        for recipient in recipients:
            if send_whatsapp_message(recipient['number'], message):
                success_count += 1
        
        print(f"✅ WhatsApp sent to {success_count}/{len(recipients)} recipients\n")
        self.log_notification(success_count, len(recipients))
    
    def log_notification(self, success_count, total_count):
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sms_logs (sent_at, recipients, successful, status, message)
                VALUES (%s, %s, %s, %s, %s)
            """, (datetime.utcnow(), total_count, success_count, 
                  'success' if success_count > 0 else 'failed',
                  f"Sent {success_count}/{total_count} successfully via WhatsApp"))
            conn.commit()
        except Exception as e:
            print(f"❌ Failed to log notification: {e}")
        finally:
            if conn:
                return_db_connection(conn)
    
    def start(self):
        """Start the scheduler with lock acquisition"""
        self.lock = SchedulerLock()
        if not self.lock.acquire():
            print("⚠️ Scheduler already running on another instance. Skipping...")
            return False
        
        self.is_scheduler_active = True
        print(f"⏰ WhatsApp Scheduler started - Will send at {self.target_hour_utc}:{self.target_minute:02d} UTC daily")
        print(f"📱 Mode: {'MOCK' if MOCK_MODE else 'LIVE'}")
        
        while self.running:
            self.check_and_send()
            time.sleep(30)
        
        return True
    
    def stop(self):
        """Stop the scheduler and release lock"""
        self.running = False
        if self.lock:
            self.lock.release()
        self.is_scheduler_active = False

# ==================== HELPER FUNCTIONS ====================

def safe_parse_timestamp(timestamp_str):
    if not timestamp_str:
        return None, None
    try:
        if isinstance(timestamp_str, datetime):
            dt = timestamp_str
        elif isinstance(timestamp_str, str):
            dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        else:
            dt = datetime.now()
    except:
        dt = datetime.now()
    return dt, dt.isoformat()

def validate_username(username):
    """Validate username (alphanumeric and underscores only)"""
    if not username:
        return False
    return bool(re.match(r'^[a-zA-Z0-9_]{3,30}$', username))

# ==================== ROUTES ====================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/admin')
def admin_dashboard():
    return send_from_directory('static', 'admin.html')

@app.route('/api/feedback', methods=['GET'])
def get_feedback():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        cursor.execute("SELECT * FROM checkins ORDER BY submission_date DESC")
        rows = cursor.fetchall()
        
        feedback = []
        for row in rows:
            cursor.execute("SELECT issue FROM checkin_issues WHERE checkin_id = %s", (row['id'],))
            issues = [r[0] for r in cursor.fetchall()]
            
            mood = row['mood'] or ''
            if 'Thumbs Up' in mood or '👍' in mood:
                rating = 'good'
                mood_score = 8
            else:
                rating = 'bad'
                mood_score = 3
            
            dt_obj, iso_timestamp = safe_parse_timestamp(row['submission_date'])
            display_timestamp = dt_obj.strftime('%m/%d/%Y, %I:%M:%S %p') if dt_obj else ''
            day = dt_obj.strftime('%A') if dt_obj else ''
            
            feedback.append({
                'id': row['id'],
                'location': row['location'] or 'Ashaiman',
                'rating': rating,
                'moodScore': mood_score,
                'comment': row['comments'] or '',
                'ip': row['ip_address'] or '127.0.0.1',
                'redFlags': [],
                'timestamp': iso_timestamp,
                'timestampDisplay': display_timestamp,
                'day': day,
                'issues': issues,
                'hasRedFlag': False
            })
        
        print(f"📊 API returning {len(feedback)} feedback entries")
        return jsonify(feedback)
    except Exception as e:
        print(f"❌ Error in get_feedback: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            return_db_connection(conn)

@app.route('/api/feedback', methods=['DELETE'])
def delete_feedback():
    data = request.get_json()
    feedback_id = data.get('id')
    
    if not feedback_id:
        return jsonify({'success': False, 'error': 'No ID provided'}), 400
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # ON DELETE CASCADE will handle related tables
        cursor.execute("DELETE FROM checkins WHERE id = %s", (feedback_id,))
        
        conn.commit()
        print(f"🗑️ Deleted feedback ID: {feedback_id}")
        return jsonify({'success': True})
    except Exception as e:
        print(f"❌ Error deleting feedback: {e}")
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        if conn:
            return_db_connection(conn)

@app.route('/submit', methods=['POST'])
def submit():
    conn = None
    try:
        mood = request.form.get('mood')
        location = request.form.get('location') or 'Ashaiman'
        issues_list = request.form.getlist('issues')
        comments = request.form.get('comments', '').strip()
        
        if not mood:
            return jsonify({'success': False, 'error': 'Please select your mood'}), 400
        
        ip_address = request.remote_addr
        current_time = datetime.now().replace(microsecond=0)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO checkins (mood, comments, submission_date, ip_address, location)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (mood, comments, current_time, ip_address, location))
        
        row = cursor.fetchone()
        checkin_id = row[0]
        
        for issue in issues_list:
            cursor.execute("""
                INSERT INTO checkin_issues (checkin_id, issue)
                VALUES (%s, %s)
            """, (checkin_id, issue))
        
        conn.commit()
        
        print(f"✅ Saved: Mood={mood}, Location={location}, Issues={issues_list}, IP={ip_address}")
        return jsonify({'success': True, 'alerts': []})
        
    except Exception as e:
        print(f"❌ Error: {e}")
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        if conn:
            return_db_connection(conn)

# ==================== WHATSAPP API ENDPOINTS ====================

@app.route('/api/notification-numbers', methods=['GET'])
def get_notification_numbers():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM notification_numbers ORDER BY country, name")
        numbers = cursor.fetchall()
        return jsonify(numbers)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            return_db_connection(conn)

@app.route('/api/notification-numbers', methods=['POST'])
def add_notification_number():
    data = request.get_json()
    phone_number = data.get('phone_number')
    name = data.get('name', '')
    country = data.get('country', 'Other')
    
    if not phone_number:
        return jsonify({'success': False, 'error': 'Phone number required'}), 400
    
    if not phone_number.startswith('+'):
        phone_number = '+' + phone_number
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO notification_numbers (phone_number, name, country, created_at)
            VALUES (%s, %s, %s, %s)
        """, (phone_number, name, country, datetime.utcnow()))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    finally:
        if conn:
            return_db_connection(conn)

@app.route('/api/scheduler-status', methods=['GET'])
def get_scheduler_status():
    now = datetime.utcnow()
    next_run = datetime(now.year, now.month, now.day, 16, 30, 0)
    if now.hour >= 16 and now.minute >= 30:
        next_run = next_run.replace(day=next_run.day + 1)
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM notification_numbers WHERE is_active = TRUE")
        active_count = cursor.fetchone()[0]
        
        return jsonify({
            'scheduler_running': scheduler.is_scheduler_active,
            'target_hour_utc': 16,
            'target_minute_utc': 30,
            'next_run_utc': next_run.isoformat(),
            'active_recipients': active_count,
            'mode': 'MOCK' if MOCK_MODE else 'LIVE'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            return_db_connection(conn)

@app.route('/api/test-whatsapp', methods=['GET'])
def test_whatsapp():
    phone = request.args.get('phone')
    if not phone:
        return jsonify({'error': 'Provide ?phone=+233XXXXXXXXX'}), 400
    
    result = send_whatsapp_message(phone, "🧪 Test message from SafiCheck! Your WhatsApp integration is working. 🎉")
    
    return jsonify({
        'success': result,
        'phone': phone,
        'mode': 'LIVE' if not MOCK_MODE else 'MOCK'
    })

@app.route('/api/debug-db')
def debug_db():
    """Debug endpoint to check database contents"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM checkins")
        count = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT id, submission_date, location
            FROM checkins
            ORDER BY submission_date ASC
        """)
        
        rows = cursor.fetchall()
        
        records = [{'id': row[0], 'submission_date': str(row[1]), 'location': row[2]} for row in rows]
        
        return jsonify({
            "total_records": count,
            "records": records
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            return_db_connection(conn)

# ==================== USER AUTHENTICATION (MERGED WITH LOGGING) ====================

@app.route('/api/login', methods=['POST'])
def api_login():
    """Login with password hashing and logging"""
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    user_agent = request.headers.get('User-Agent', 'Unknown')
    ip_address = request.remote_addr
    
    # Validate input
    if not username or not password:
        return jsonify({'success': False, 'error': 'Username and password required'}), 400
    
    if not validate_username(username):
        return jsonify({'success': False, 'error': 'Invalid username format'}), 400
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        
        if user:
            # For backward compatibility: check plain password or hashed
            password_hash = user[2] if len(user) > 2 else None
            is_valid = False
            
            if password_hash and password_hash.startswith('pbkdf2:sha256:'):
                is_valid = check_password_hash(password_hash, password)
            else:
                # Fallback for old users (plain password)
                cursor.execute("SELECT * FROM users WHERE username = %s AND password_hash = %s", (username, password))
                old_user = cursor.fetchone()
                is_valid = old_user is not None
                
                # Upgrade to hashed password if valid
                if is_valid:
                    new_hash = generate_password_hash(password)
                    cursor.execute("UPDATE users SET password_hash = %s WHERE username = %s", (new_hash, username))
                    conn.commit()
            
            if is_valid:
                cursor.execute("""
                    INSERT INTO login_logs (username, login_time, ip_address, status, user_agent)
                    VALUES (%s, %s, %s, %s, %s)
                """, (username, datetime.utcnow(), ip_address, 'success', user_agent))
                conn.commit()
                return jsonify({'success': True, 'username': username})
        
        # Login failed - log it
        cursor.execute("""
            INSERT INTO login_logs (username, login_time, ip_address, status, user_agent)
            VALUES (%s, %s, %s, %s, %s)
        """, (username, datetime.utcnow(), ip_address, 'failed', user_agent))
        conn.commit()
        return jsonify({'success': False, 'error': 'Invalid credentials'}), 401
        
    except Exception as e:
        print(f"❌ Login error: {e}")
        return jsonify({'success': False, 'error': 'Server error'}), 500
    finally:
        if conn:
            return_db_connection(conn)

@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    # Validate input
    if not username or not password:
        return jsonify({'success': False, 'error': 'Username and password required'}), 400
    
    if not validate_username(username):
        return jsonify({'success': False, 'error': 'Username must be 3-30 characters (letters, numbers, underscore)'}), 400
    
    if len(password) < 6:
        return jsonify({'success': False, 'error': 'Password must be at least 6 characters'}), 400
    
    hashed_password = generate_password_hash(password)
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO users (username, password_hash, created_at)
            VALUES (%s, %s, %s)
        """, (username, hashed_password, datetime.now()))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    finally:
        if conn:
            return_db_connection(conn)

@app.route('/api/reset-password', methods=['POST'])
def api_reset_password():
    data = request.get_json()
    username = data.get('username')
    new_password = data.get('new_password')
    
    # Validate input
    if not username or not new_password:
        return jsonify({'success': False, 'error': 'Username and new password required'}), 400
    
    if not validate_username(username):
        return jsonify({'success': False, 'error': 'Invalid username format'}), 400
    
    if len(new_password) < 6:
        return jsonify({'success': False, 'error': 'Password must be at least 6 characters'}), 400
    
    hashed_password = generate_password_hash(new_password)
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET password_hash = %s WHERE username = %s", (hashed_password, username))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    finally:
        if conn:
            return_db_connection(conn)

@app.route('/api/users', methods=['GET'])
def api_get_users():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT id, username, created_at FROM users")
        users = cursor.fetchall()
        return jsonify(users)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            return_db_connection(conn)

@app.route('/api/delete-user', methods=['POST'])
def api_delete_user():
    data = request.get_json()
    username = data.get('username')
    
    if not username:
        return jsonify({'success': False, 'error': 'Username required'}), 400
    
    if username == 'admin':
        return jsonify({'success': False, 'error': 'Cannot delete default admin user'}), 400
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE username = %s", (username,))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    finally:
        if conn:
            return_db_connection(conn)

@app.route('/api/login-logs', methods=['GET'])
def api_get_login_logs():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM login_logs ORDER BY login_time DESC LIMIT 200")
        logs = cursor.fetchall()
        
        formatted_logs = []
        for log in logs:
            formatted_logs.append({
                'id': log['id'],
                'username': log['username'],
                'loginTimeDisplay': log['login_time'].strftime('%m/%d/%Y, %I:%M:%S %p') if log['login_time'] else '',
                'ipAddress': log['ip_address'],
                'status': log['status'],
                'userAgent': log.get('user_agent', 'Unknown')[:50] if log.get('user_agent') else 'Unknown'
            })
        
        return jsonify(formatted_logs)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            return_db_connection(conn)

# ==================== SCHEDULER INSTANCE (Only starts when running directly) ====================
scheduler = NotificationScheduler()

# Only start scheduler when running directly (not in Gunicorn worker)
# This prevents multiple schedulers in production
if __name__ == '__main__':
    # Start scheduler in background thread
    scheduler_thread = threading.Thread(target=scheduler.start, daemon=True)
    scheduler_thread.start()
    
    # Register cleanup on exit
    def cleanup():
        print("🛑 Shutting down scheduler...")
        scheduler.stop()
    atexit.register(cleanup)
    
    print("=" * 60)
    print("🌍 Safi-Check System Running!")
    print("=" * 60)
    print("📝 Employee Form: http://localhost:5000")
    print("🔐 Admin Dashboard: http://localhost:5000/admin")
    print("=" * 60)
    print("👤 Admin Login: admin / admin123")
    print("=" * 60)
    print(f"📱 WhatsApp Mode: {'MOCK' if MOCK_MODE else 'LIVE'}")
    print(f"⏰ Scheduler: Daily at 16:30 UTC")
    print(f"🗄️ Database: PostgreSQL with connection pool")
    print("=" * 60)
    app.run(debug=True, port=5000)
else:
    # When running under Gunicorn, only log that scheduler is disabled
    print("ℹ️ Running under Gunicorn - Scheduler disabled (will run in main process only)")
