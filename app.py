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
import sys

app = Flask(__name__)

# ==================== DATABASE CONNECTION POOL ====================
db_pool = None

def init_db_pool():
    """Initialize PostgreSQL connection pool"""
    global db_pool
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise Exception("DATABASE_URL environment variable is required. Please set it in Render.")
    
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

WHATSAPP_API_URL = f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages"
WHATSAPP_HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_TOKEN}",
    "Content-Type": "application/json"
}

print(f"📱 WhatsApp Mode: {'MOCK' if MOCK_MODE else 'LIVE'}")
print(f"📱 Phone Number ID: {PHONE_NUMBER_ID}")
print(f"📱 Token starts with: {WHATSAPP_TOKEN[:20]}...")
print(f"📱 API URL: {WHATSAPP_API_URL}")

# ==================== send_whatsapp_message ====================
def send_whatsapp_message(phone_number, message):
    """Send WhatsApp message using Meta Cloud API"""
    if MOCK_MODE:
        print(f"📱 [MOCK MODE] Would send to {phone_number}: {message}")
        sys.stdout.flush()
        return True
    
    try:
        phone_number = phone_number.strip()
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
        
        print(f"📤 Sending WhatsApp message to {phone_number}")
        sys.stdout.flush()
        
        response = requests.post(WHATSAPP_API_URL, headers=WHATSAPP_HEADERS, json=payload)
        
        print("=" * 60)
        print("🚨 WHATSAPP API RESPONSE 🚨")
        print("=" * 60)
        print(f"STATUS CODE: {response.status_code}")
        print(f"RESPONSE BODY: {response.text}")
        print("=" * 60)
        sys.stdout.flush()

        if response.status_code in [200, 201]:
            try:
                data = response.json()
                if "messages" in data:
                    print(f"✅ WhatsApp response: {response.text}")
                    sys.stdout.flush()
                    return True
                print("❌ No 'messages' field returned")
                sys.stdout.flush()
                return False
            except Exception as e:
                print(f"❌ JSON parse error: {e}")
                sys.stdout.flush()
                return False

        print(f"❌ WhatsApp API error: {response.status_code}")
        print(response.text)
        sys.stdout.flush()
        return False
            
    except Exception as e:
        print(f"❌ WhatsApp send error: {e}")
        sys.stdout.flush()
        return False

# ==================== DATABASE SETUP ====================

def init_db():
    """Initialize PostgreSQL database tables"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

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

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS checkin_issues (
                id SERIAL PRIMARY KEY,
                checkin_id INTEGER REFERENCES checkins(id) ON DELETE CASCADE,
                issue TEXT
            )
        """)

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

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE,
                password_hash TEXT,
                created_at TIMESTAMP
            )
        """)

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

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_checkins_date ON checkins(submission_date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_checkin ON alerts(checkin_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_checkins_location ON checkins(location)")

        cursor.execute("SELECT * FROM users WHERE username = 'admin'")
        if not cursor.fetchone():
            hashed_password = generate_password_hash('admin123')
            cursor.execute("""
                INSERT INTO users (username, password_hash, created_at)
                VALUES (%s, %s, %s)
            """, ('admin', hashed_password, datetime.now()))

        conn.commit()
        print("✅ PostgreSQL database initialized")
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
            ('+233550157210', 'Ghana Number 1', 'Ghana', True),
            ('+233506896041', 'Ghana Number 2', 'Ghana', True),
            ('+15556664486', 'Meta Test Number', 'USA', True),
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

# Initialize database
init_db_pool()
init_db()
insert_test_numbers()

# ==================== SCHEDULER LOCK ====================
class SchedulerLock:
    def __init__(self):
        self.lock_acquired = False
        self.conn = None
        self.cursor = None
        self._lock_held = False
    
    def acquire(self):
        try:
            self.conn = get_db_connection()
            self.cursor = self.conn.cursor()
            self.cursor.execute("SELECT pg_try_advisory_lock(12345)")
            self.lock_acquired = self.cursor.fetchone()[0]
            if self.lock_acquired:
                self._lock_held = True
                print("✅ Scheduler lock acquired")
            else:
                print("⚠️ Scheduler lock already held by another instance")
            return self.lock_acquired
        except Exception as e:
            print(f"❌ Failed to acquire scheduler lock: {e}")
            return False
    
    def release(self):
        if self._lock_held and self.cursor:
            try:
                self.cursor.execute("SELECT pg_advisory_unlock(12345)")
                self.conn.commit()
                self._lock_held = False
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
        self.target_hour_utc = 14
        self.target_minute = 0
        self.lock = None
        self.is_scheduler_active = False
    
    def get_active_numbers(self):
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
        today = now.date()

        print(f"⏰ Checking scheduler: {now.hour}:{now.minute:02d}:{now.second:02d} UTC")

        target_time = now.replace(
            hour=self.target_hour_utc,
            minute=self.target_minute,
            second=0,
            microsecond=0
        )

        if now >= target_time and self.last_sent_date != today:
            print("🔔 Time reached. Sending notifications...")
            self.send_notifications()
            self.last_sent_date = today
    
    def send_notifications(self):
        message = "⏰ SafiCheck Reminder: Please complete your check-in: https://safi-check.onrender.com"
        
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
        self.running = False
        if self.lock:
            self.lock.release()
        self.is_scheduler_active = False

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
            
            dt_obj = row['submission_date']
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
                'timestamp': dt_obj.isoformat() if dt_obj else '',
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

# ==================== WHATSAPP ENDPOINTS ====================

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
    next_run = datetime(now.year, now.month, now.day, 14, 0, 0)
    if now.hour >= 14 and now.minute >= 0:
        next_run = next_run.replace(day=next_run.day + 1)
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM notification_numbers WHERE is_active = TRUE")
        active_count = cursor.fetchone()[0]
        
        return jsonify({
            'scheduler_running': scheduler.is_scheduler_active,
            'target_hour_utc': 14,
            'target_minute_utc': 0,
            'next_run_utc': next_run.isoformat(),
            'active_recipients': active_count,
            'mode': 'MOCK' if MOCK_MODE else 'LIVE'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            return_db_connection(conn)

@app.route('/api/trigger-whatsapp')
def trigger_whatsapp():
    """Manually trigger WhatsApp notifications"""
    try:
        scheduler.send_notifications()
        return jsonify({
            'status': 'sent',
            'message': 'WhatsApp notifications triggered successfully',
            'mode': 'LIVE' if not MOCK_MODE else 'MOCK',
            'recipients': len(scheduler.get_active_numbers())
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500

@app.route('/api/test-whatsapp', methods=['GET'])
def test_whatsapp():
    phone = request.args.get('phone')

    if not phone:
        return jsonify({'error': 'Provide ?phone=+233XXXXXXXXX'}), 400

    phone = phone.strip()
    if not phone.startswith('+'):
        phone = '+' + phone

    print(f"🧪 Test endpoint called for phone: {phone}")
    sys.stdout.flush()

    result = send_whatsapp_message(
        phone,
        "🧪 Test message from SafiCheck! Your WhatsApp integration is working. 🎉"
    )

    return jsonify({
        'success': result,
        'phone': phone,
        'mode': 'LIVE' if not MOCK_MODE else 'MOCK'
    })

# ==================== GOOGLE SHEETS INTEGRATION ====================
@app.route('/api/send-from-sheet', methods=['POST'])
def send_from_sheet():
    """Endpoint for Google Sheets to trigger WhatsApp messages"""
    try:
        data = request.get_json()
        phone = data.get("phone")

        if not phone:
            return jsonify({"error": "No phone provided"}), 400

        # Format number safely
        phone = phone.strip()
        if phone.startswith("0"):
            phone = "233" + phone[1:]
        if not phone.startswith("+"):
            phone = "+" + phone

        message = "⏰ SafiCheck Reminder: Please complete your check-in: https://safi-check.onrender.com"

        print(f"📤 Sending from Google Sheets to: {phone}")
        sys.stdout.flush()

        success = send_whatsapp_message(phone, message)

        return jsonify({
            "success": success,
            "phone": phone
        })

    except Exception as e:
        print(f"❌ Error in send-from-sheet: {e}")
        sys.stdout.flush()
        return jsonify({"error": str(e)}), 500

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

# ==================== USER AUTHENTICATION ====================

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    user_agent = request.headers.get('User-Agent', 'Unknown')
    ip_address = request.remote_addr
    
    if not username or not password:
        return jsonify({'success': False, 'error': 'Username and password required'}), 400
    
    if not re.match(r'^[a-zA-Z0-9_]{3,30}$', username):
        return jsonify({'success': False, 'error': 'Invalid username format'}), 400
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        
        if user:
            password_hash = user[2] if len(user) > 2 else None
            is_valid = False
            
            if password_hash and password_hash.startswith('pbkdf2:sha256:'):
                is_valid = check_password_hash(password_hash, password)
            else:
                cursor.execute("SELECT * FROM users WHERE username = %s AND password_hash = %s", (username, password))
                old_user = cursor.fetchone()
                is_valid = old_user is not None
                
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
    
    if not username or not password:
        return jsonify({'success': False, 'error': 'Username and password required'}), 400
    
    if not re.match(r'^[a-zA-Z0-9_]{3,30}$', username):
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
    
    if not username or not new_password:
        return jsonify({'success': False, 'error': 'Username and new password required'}), 400
    
    if not re.match(r'^[a-zA-Z0-9_]{3,30}$', username):
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

# ==================== SCHEDULER INSTANCE ====================

scheduler = NotificationScheduler()

scheduler_thread = threading.Thread(
    target=scheduler.start,
    daemon=True
)
scheduler_thread.start()
print("✅ Scheduler thread started for Gunicorn environment.")

def cleanup():
    print("🛑 Shutting down scheduler...")
    scheduler.stop()

atexit.register(cleanup)

print("=" * 60)
print("🌍 Safi-Check System Running!")
print("=" * 60)
print(f"📱 WhatsApp Mode: {'MOCK' if MOCK_MODE else 'LIVE'}")
print(f"📱 Phone Number ID: {PHONE_NUMBER_ID}")
print(f"📱 Token (first 20 chars): {WHATSAPP_TOKEN[:20]}...")
print(f"⏰ Scheduler: Daily at {scheduler.target_hour_utc}:{scheduler.target_minute:02d} UTC")
print("=" * 60)
