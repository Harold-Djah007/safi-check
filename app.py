from flask import Flask, render_template, send_from_directory, request, jsonify
from datetime import datetime
import json
import os
import requests
import threading
import time
import pyodbc
from werkzeug.security import generate_password_hash, check_password_hash
import atexit
import re
import sys

app = Flask(__name__)

# ==================== AZURE SQL DATABASE CONNECTION ====================

def get_db_connection():
    """Get connection to Azure SQL Database"""
    # Get connection details from environment variables
    server = os.environ.get('DB_SERVER', 'safisanadb.database.windows.net')
    database = os.environ.get('DB_NAME', 'safidb')
    username = os.environ.get('DB_USERNAME', '')
    password = os.environ.get('DB_PASSWORD', '')
    
    # Validate required environment variables
    if not username or not password:
        print("❌ ERROR: DB_USERNAME and DB_PASSWORD environment variables are required!")
        print("Please set them in Render Dashboard -> Environment Variables")
        raise Exception("Database credentials not configured")
    
    # Build connection string
    connection_string = (
        f'DRIVER={{ODBC Driver 17 for SQL Server}};'
        f'SERVER={server};'
        f'DATABASE={database};'
        f'UID={username};'
        f'PWD={password};'
        f'Encrypt=yes;'
        f'TrustServerCertificate=no;'
        f'Connection Timeout=30;'
    )
    
    try:
        conn = pyodbc.connect(connection_string)
        print(f"✅ Connected to Azure SQL Database: {database}")
        return conn
    except Exception as e:
        print(f"❌ Database connection error: {e}")
        print(f"Server: {server}")
        print(f"Database: {database}")
        print(f"Username: {username}")
        raise

def init_db():
    """Initialize Azure SQL Database tables"""
    conn = None
    try:
        print("🔄 Initializing database tables...")
        conn = get_db_connection()
        cursor = conn.cursor()

        # Create users table
        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='users' AND xtype='U')
            CREATE TABLE users (
                id INT IDENTITY(1,1) PRIMARY KEY,
                username NVARCHAR(100) UNIQUE,
                password_hash NVARCHAR(MAX),
                created_at DATETIME
            )
        """)
        print("✅ Users table ready")

        # Create checkins table
        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='checkins' AND xtype='U')
            CREATE TABLE checkins (
                id INT IDENTITY(1,1) PRIMARY KEY,
                mood NVARCHAR(MAX),
                comments NVARCHAR(MAX),
                submission_date DATETIME,
                ip_address NVARCHAR(100),
                location NVARCHAR(100)
            )
        """)
        print("✅ Checkins table ready")

        # Create checkin_issues table
        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='checkin_issues' AND xtype='U')
            CREATE TABLE checkin_issues (
                id INT IDENTITY(1,1) PRIMARY KEY,
                checkin_id INT,
                issue NVARCHAR(MAX),
                FOREIGN KEY (checkin_id) REFERENCES checkins(id) ON DELETE CASCADE
            )
        """)
        print("✅ Checkin issues table ready")

        # Create login_logs table
        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='login_logs' AND xtype='U')
            CREATE TABLE login_logs (
                id INT IDENTITY(1,1) PRIMARY KEY,
                username NVARCHAR(100),
                login_time DATETIME,
                ip_address NVARCHAR(100),
                status NVARCHAR(50),
                user_agent NVARCHAR(MAX)
            )
        """)
        print("✅ Login logs table ready")

        # Create notification_numbers table
        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='notification_numbers' AND xtype='U')
            CREATE TABLE notification_numbers (
                id INT IDENTITY(1,1) PRIMARY KEY,
                phone_number NVARCHAR(50) UNIQUE,
                name NVARCHAR(100),
                country NVARCHAR(50),
                is_active BIT DEFAULT 1,
                created_at DATETIME
            )
        """)
        print("✅ Notification numbers table ready")

        # Create sms_logs table
        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='sms_logs' AND xtype='U')
            CREATE TABLE sms_logs (
                id INT IDENTITY(1,1) PRIMARY KEY,
                sent_at DATETIME,
                recipients INT,
                successful INT,
                status NVARCHAR(50),
                message NVARCHAR(MAX)
            )
        """)
        print("✅ SMS logs table ready")

        # Create indexes
        try:
            cursor.execute("""
                IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='idx_checkins_date')
                CREATE INDEX idx_checkins_date ON checkins(submission_date)
            """)
        except:
            pass  # Index might already exist
        
        try:
            cursor.execute("""
                IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='idx_checkins_location')
                CREATE INDEX idx_checkins_location ON checkins(location)
            """)
        except:
            pass

        # Check if admin exists, if not create
        cursor.execute("SELECT * FROM users WHERE username = 'admin'")
        if not cursor.fetchone():
            hashed_password = generate_password_hash('admin123')
            cursor.execute("""
                INSERT INTO users (username, password_hash, created_at)
                VALUES (?, ?, ?)
            """, ('admin', hashed_password, datetime.now()))
            print("✅ Created admin user with hashed password")
        else:
            # Update admin password to ensure it's properly hashed
            hashed_password = generate_password_hash('admin123')
            cursor.execute("""
                UPDATE users SET password_hash = ? WHERE username = 'admin'
            """, (hashed_password,))
            print("✅ Updated admin password hash")

        conn.commit()
        print("✅ Azure SQL Database initialized successfully")
    except Exception as e:
        print(f"❌ Database initialization error: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        if conn:
            conn.close()

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

# ==================== send_whatsapp_message ====================
def send_whatsapp_message(phone_number, message):
    """Send WhatsApp message using Meta Cloud API - digits ONLY, NO + sign"""
    phone_number = re.sub(r'[^0-9]', '', str(phone_number))
    
    if MOCK_MODE:
        print(f"📱 [MOCK MODE] Would send to {phone_number}: {message}")
        sys.stdout.flush()
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
        
        print(f"📤 Sending WhatsApp message to: {phone_number}")
        sys.stdout.flush()
        
        response = requests.post(WHATSAPP_API_URL, headers=WHATSAPP_HEADERS, json=payload)
        
        if response.status_code in [200, 201]:
            print(f"✅ WhatsApp message sent successfully to {phone_number}")
            sys.stdout.flush()
            return True
        else:
            print(f"❌ FAILED WHATSAPP: {response.text}")
            sys.stdout.flush()
            return False
            
    except Exception as e:
        print(f"❌ WhatsApp send error: {e}")
        sys.stdout.flush()
        return False

# ==================== ROUTES ====================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/admin')
def admin_dashboard():
    return send_from_directory('static', 'admin.html')

@app.route('/api/feedback', methods=['GET'])
def get_feedback():
    """API endpoint for admin dashboard - returns all feedback"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM checkins ORDER BY submission_date DESC")
        rows = cursor.fetchall()
        
        feedback = []
        for row in rows:
            # Get issues for this checkin
            cursor.execute("SELECT issue FROM checkin_issues WHERE checkin_id = ?", (row[0],))
            issues = [r[0] for r in cursor.fetchall()]
            
            mood = row[1] or ''
            if 'Thumbs Up' in mood or '👍' in mood:
                rating = 'good'
                mood_score = 8
            else:
                rating = 'bad'
                mood_score = 3
            
            dt_obj = row[3]
            display_timestamp = dt_obj.strftime('%m/%d/%Y, %I:%M:%S %p') if dt_obj else ''
            day = dt_obj.strftime('%A') if dt_obj else ''
            
            feedback.append({
                'id': row[0],
                'location': row[5] or 'Ashaiman',
                'rating': rating,
                'moodScore': mood_score,
                'comment': row[2] or '',
                'ip': row[4] or '127.0.0.1',
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
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            conn.close()

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
        cursor.execute("DELETE FROM checkins WHERE id = ?", (feedback_id,))
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
            conn.close()

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
            VALUES (?, ?, ?, ?, ?)
        """, (mood, comments, current_time, ip_address, location))
        
        # Get the inserted ID
        cursor.execute("SELECT SCOPE_IDENTITY()")
        checkin_id = cursor.fetchone()[0]
        
        for issue in issues_list:
            cursor.execute("""
                INSERT INTO checkin_issues (checkin_id, issue)
                VALUES (?, ?)
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
            conn.close()

# ==================== WHATSAPP ENDPOINTS ====================

@app.route('/api/notification-numbers', methods=['GET'])
def get_notification_numbers():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM notification_numbers ORDER BY country, name")
        rows = cursor.fetchall()
        
        numbers = []
        for row in rows:
            numbers.append({
                'id': row[0],
                'phone_number': row[1],
                'name': row[2],
                'country': row[3],
                'is_active': bool(row[4]),
                'created_at': row[5].isoformat() if row[5] else None
            })
        return jsonify(numbers)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/notification-numbers', methods=['POST'])
def add_notification_number():
    data = request.get_json()
    phone_number = data.get('phone_number')
    name = data.get('name', '')
    country = data.get('country', 'Other')
    
    if not phone_number:
        return jsonify({'success': False, 'error': 'Phone number required'}), 400
    
    phone_number = re.sub(r'[^0-9]', '', str(phone_number))
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO notification_numbers (phone_number, name, country, created_at)
            VALUES (?, ?, ?, ?)
        """, (phone_number, name, country, datetime.utcnow()))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    finally:
        if conn:
            conn.close()

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
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        
        if user:
            password_hash = user[2]
            is_valid = False
            
            try:
                if password_hash:
                    is_valid = check_password_hash(password_hash, password)
                    print(f"🔐 Password verification for {username}: {'SUCCESS' if is_valid else 'FAILED'}")
            except Exception as e:
                print(f"❌ Password check error: {e}")
                is_valid = False
            
            if is_valid:
                cursor.execute("""
                    INSERT INTO login_logs (username, login_time, ip_address, status, user_agent)
                    VALUES (?, ?, ?, ?, ?)
                """, (username, datetime.utcnow(), ip_address, 'success', user_agent))
                conn.commit()
                return jsonify({'success': True, 'username': username})
        
        cursor.execute("""
            INSERT INTO login_logs (username, login_time, ip_address, status, user_agent)
            VALUES (?, ?, ?, ?, ?)
        """, (username, datetime.utcnow(), ip_address, 'failed', user_agent))
        conn.commit()
        return jsonify({'success': False, 'error': 'Invalid credentials'}), 401
        
    except Exception as e:
        print(f"❌ Login error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': 'Server error'}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
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
            VALUES (?, ?, ?)
        """, (username, hashed_password, datetime.now()))
        conn.commit()
        print(f"✅ User registered: {username}")
        return jsonify({'success': True})
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ Registration error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 400
    finally:
        if conn:
            conn.close()

@app.route('/api/reset-password', methods=['POST'])
def api_reset_password():
    data = request.get_json()
    username = data.get('username', '').strip()
    new_password = data.get('new_password', '')
    
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
        cursor.execute("UPDATE users SET password_hash = ? WHERE username = ?", (hashed_password, username))
        conn.commit()
        
        if cursor.rowcount == 0:
            return jsonify({'success': False, 'error': 'User not found'}), 404
        
        print(f"✅ Password reset for: {username}")
        return jsonify({'success': True})
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ Password reset error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 400
    finally:
        if conn:
            conn.close()

@app.route('/api/users', methods=['GET'])
def api_get_users():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, created_at FROM users ORDER BY id")
        rows = cursor.fetchall()
        
        users = []
        for row in rows:
            users.append({
                'id': row[0],
                'username': row[1],
                'created_at': row[2].isoformat() if row[2] else None
            })
        return jsonify(users)
    except Exception as e:
        print(f"❌ Error fetching users: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/delete-user', methods=['POST'])
def api_delete_user():
    data = request.get_json()
    username = data.get('username', '').strip()
    
    if not username:
        return jsonify({'success': False, 'error': 'Username required'}), 400
    
    if username == 'admin':
        return jsonify({'success': False, 'error': 'Cannot delete default admin user'}), 400
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()
        
        if cursor.rowcount == 0:
            return jsonify({'success': False, 'error': 'User not found'}), 404
        
        print(f"🗑️ Deleted user: {username}")
        return jsonify({'success': True})
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ Delete user error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 400
    finally:
        if conn:
            conn.close()

@app.route('/api/login-logs', methods=['GET'])
def api_get_login_logs():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT TOP 200 * FROM login_logs ORDER BY login_time DESC")
        rows = cursor.fetchall()
        
        formatted_logs = []
        for row in rows:
            formatted_logs.append({
                'id': row[0],
                'username': row[1],
                'loginTimeDisplay': row[2].strftime('%m/%d/%Y, %I:%M:%S %p') if row[2] else '',
                'ipAddress': row[3],
                'status': row[4],
                'userAgent': row[5][:50] if row[5] else 'Unknown'
            })
        
        return jsonify(formatted_logs)
    except Exception as e:
        print(f"❌ Error fetching login logs: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            conn.close()

# ==================== SCHEDULER (Simplified for Azure) ====================

class NotificationScheduler:
    def __init__(self):
        self.last_sent_date = None
        self.running = True
        self.target_hour_utc = 14
        self.target_minute = 0
        self.is_scheduler_active = False
    
    def get_active_numbers(self):
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT phone_number, name, country FROM notification_numbers WHERE is_active = 1")
            rows = cursor.fetchall()
            numbers = [{'number': row[0], 'name': row[1], 'country': row[2]} for row in rows]
            return numbers
        except Exception as e:
            print(f"❌ Error getting active numbers: {e}")
            return []
        finally:
            if conn:
                conn.close()
    
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
                VALUES (?, ?, ?, ?, ?)
            """, (datetime.utcnow(), total_count, success_count, 
                  'success' if success_count > 0 else 'failed',
                  f"Sent {success_count}/{total_count} successfully via WhatsApp"))
            conn.commit()
        except Exception as e:
            print(f"❌ Failed to log notification: {e}")
        finally:
            if conn:
                conn.close()
    
    def start(self):
        self.is_scheduler_active = True
        print(f"⏰ WhatsApp Scheduler started - Will send at {self.target_hour_utc}:{self.target_minute:02d} UTC daily")
        print(f"📱 Mode: {'MOCK' if MOCK_MODE else 'LIVE'}")
        
        while self.running:
            self.check_and_send()
            time.sleep(60)
        
        return True
    
    def stop(self):
        self.running = False
        self.is_scheduler_active = False

# ==================== STARTUP ====================

# Initialize database
try:
    init_db()
except Exception as e:
    print(f"❌ Failed to initialize database: {e}")
    print("⚠️ Continuing startup...")

# Start scheduler
scheduler = NotificationScheduler()
scheduler_thread = threading.Thread(
    target=scheduler.start,
    daemon=True
)
scheduler_thread.start()
print("✅ Scheduler thread started")

def cleanup():
    print("🛑 Shutting down scheduler...")
    scheduler.stop()

atexit.register(cleanup)

print("=" * 60)
print("🌍 Safi-Check System Running with Azure SQL Database!")
print("=" * 60)
print(f"📱 WhatsApp Mode: {'MOCK' if MOCK_MODE else 'LIVE'}")
print("=" * 60)
