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

# --- FIX: Use v25.0 API endpoint ---
WHATSAPP_API_URL = f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages"
WHATSAPP_HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_TOKEN}",
    "Content-Type": "application/json"
}

print(f"📱 WhatsApp Mode: {'MOCK' if MOCK_MODE else 'LIVE'}")

# ==================== IMPROVED send_whatsapp_message ====================
def send_whatsapp_message(phone_number, message):
    """Send WhatsApp message using Meta Cloud API - with full response logging"""
    if MOCK_MODE:
        print(f"📱 [MOCK MODE] Would send to {phone_number}: {message}")
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
        print(f"📤 Payload: {json.dumps(payload, indent=2)}")
        
        response = requests.post(WHATSAPP_API_URL, headers=WHATSAPP_HEADERS, json=payload)
        
        # --- FIX: Log full API response for debugging ---
        print("STATUS:", response.status_code)
        print("BODY:", response.text)

        if response.status_code in [200, 201]:
            try:
                data = response.json()
                if "messages" in data:
                    print(f"✅ WhatsApp response: {response.text}")
                    return True
                print("❌ No 'messages' field returned")
                return False
            except Exception as e:
                print(f"❌ JSON parse error: {e}")
                return False

        print(f"❌ WhatsApp API error: {response.status_code}")
        print(response.text)
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
            ('+233550157210', 'Ghana Number 1', 'Ghana', True),
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

# Initialize database pool and tables
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
        """Try to acquire scheduler lock using PostgreSQL advisory lock"""
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
        """Release the advisory lock"""
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

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

# ==================== WHATSAPP SCHEDULER ====================

class NotificationScheduler:
    def __init__(self):
        self.last_sent_date = None
        self.running = True
        # Set test time (e.g., 13:35 UTC) - change this for your test
        self.target_hour_utc = 14  # 2 PM UTC
        self.target_minute = 0    # 0 minutes (2:00 PM UTC)
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
        """Check current time and send WhatsApp - won't miss the target minute"""
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
        message = "⏰ SafiCheck Reminder: It's time for daily check-in! Please submit your feedback at: https://safi-check.onrender.com"
        
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
        self.running = False
        if self.lock:
            self.lock.release()
        self.is_scheduler_active = False

# ==================== ROUTES AND ENDPOINTS ====================

# ... (keep all your existing route functions: index, admin, get_feedback, etc.) ...
# Please ensure all your existing @app.route functions are included here.
# I have omitted them for brevity, but they must be present in your final file.

# ==================== SCHEDULER INSTANCE (FIX: Starts unconditionally) ====================

# --- FIX: Start scheduler unconditionally so it runs with Gunicorn ---
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
print(f"⏰ Scheduler: Daily at {scheduler.target_hour_utc}:{scheduler.target_minute:02d} UTC")
print("=" * 60)

# The Flask app is now fully initialized and ready to be served by Gunicorn.
