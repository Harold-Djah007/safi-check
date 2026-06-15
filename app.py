from flask import Flask, render_template, send_from_directory, request, jsonify
import sqlite3
from datetime import datetime
import json
import os
import requests
import threading
import time

app = Flask(__name__)

# ==================== WHATSAPP CONFIGURATION ====================
# Get from environment variables (set these in Render dashboard)
WHATSAPP_TOKEN = os.environ.get('WHATSAPP_TOKEN', '')
PHONE_NUMBER_ID = os.environ.get('PHONE_NUMBER_ID', '')
MOCK_MODE = os.environ.get('MOCK_MODE', 'True').lower() == 'true'

# Validate credentials
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
        
        response = requests.post(
            WHATSAPP_API_URL,
            headers=WHATSAPP_HEADERS,
            json=payload
        )
        
        if response.status_code == 200:
            print(f"✅ WhatsApp message sent to {phone_number}")
            return True
        else:
            print(f"❌ WhatsApp API error: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ WhatsApp send error: {e}")
        return False

# ==================== DATABASE SETUP ====================

def init_db():
    conn = sqlite3.connect('safi_check.db')
    # Removed DEFAULT 'Ashaiman' - location comes from form
    conn.execute('''CREATE TABLE IF NOT EXISTS checkins
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     mood TEXT, 
                     comments TEXT, 
                     submission_date TIMESTAMP,
                     ip_address TEXT,
                     location TEXT)''')
    
    conn.execute('''CREATE TABLE IF NOT EXISTS checkin_issues
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     checkin_id INTEGER, 
                     issue TEXT)''')
    
    conn.execute('''CREATE TABLE IF NOT EXISTS alerts
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     checkin_id INTEGER, 
                     keyword TEXT, 
                     severity TEXT,
                     details TEXT, 
                     acknowledged BOOLEAN DEFAULT 0,
                     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Create users table for admin authentication
    conn.execute('''CREATE TABLE IF NOT EXISTS users
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     username TEXT UNIQUE,
                     password TEXT,
                     created_at TIMESTAMP)''')
    
    # Create notification numbers table for WhatsApp recipients
    conn.execute('''CREATE TABLE IF NOT EXISTS notification_numbers
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     phone_number TEXT UNIQUE,
                     name TEXT,
                     country TEXT,
                     is_active BOOLEAN DEFAULT 1,
                     created_at TIMESTAMP)''')
    
    # Create SMS logs table
    conn.execute('''CREATE TABLE IF NOT EXISTS sms_logs
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     sent_at TIMESTAMP,
                     recipients INTEGER,
                     successful INTEGER,
                     status TEXT,
                     message TEXT)''')
    
    # Check if admin exists, if not create default
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = 'admin'")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (username, password, created_at) VALUES (?, ?, ?)",
                       ('admin', 'admin123', datetime.now()))
    
    conn.commit()
    conn.close()
    print("✅ Database initialized")

def insert_test_numbers():
    """Automatically add test phone numbers to database on startup"""
    try:
        conn = sqlite3.connect('safi_check.db')
        cursor = conn.cursor()
        
        # Your actual phone numbers (converted to international format)
        # Ghana numbers: Add +233 and remove the leading 0
        # 0550157210 → +233550157210
        # 0506896041 → +233506896041
        # Test number from Meta: +1 555 664 4486
        
        test_numbers = [
            ('+233550157210', 'Ghana Number 1', 'Ghana', 1),
            ('+233506896041', 'Ghana Number 2', 'Ghana', 1),
            ('+15556644486', 'Meta Test Number', 'USA', 1),  # Meta's test number
        ]
        
        for number, name, country, active in test_numbers:
            cursor.execute('''
                INSERT OR REPLACE INTO notification_numbers (phone_number, name, country, is_active, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (number, name, country, active, datetime.utcnow()))
        
        conn.commit()
        conn.close()
        print(f"✅ Seeded {len(test_numbers)} test number(s) to notification_numbers table")
        print(f"   Numbers: {', '.join([n[0] for n in test_numbers])}")
        return True
    except Exception as e:
        print(f"❌ Failed to seed database: {e}")
        return False
# Initialize database and seed test numbers
init_db()
insert_test_numbers()

# ==================== WHATSAPP SCHEDULER ====================

class NotificationScheduler:
    def __init__(self):
        self.last_sent_date = None
        self.running = True
        self.target_hour_utc = 16  # 4:00 PM UTC
        self.target_minute = 40 
    
    def get_active_numbers(self):
        """Get phone numbers from database"""
        try:
            conn = sqlite3.connect('safi_check.db')
            cursor = conn.cursor()
            cursor.execute("SELECT phone_number, name, country FROM notification_numbers WHERE is_active = 1")
            numbers = [{'number': row[0], 'name': row[1], 'country': row[2]} for row in cursor.fetchall()]
            conn.close()
            return numbers
        except Exception as e:
            print(f"❌ Error getting active numbers: {e}")
            return []
    
    def check_and_send(self):
        """Check current time and send WhatsApp if it's 4:00 PM UTC"""
        now = datetime.utcnow()
        current_hour = now.hour
        current_minute = now.minute
        today = now.date()
        
        if current_hour == self.target_hour_utc and current_minute == self.target_minute:
            if self.last_sent_date != today:
                self.send_notifications()
                self.last_sent_date = today
    
    def send_notifications(self):
        """Send WhatsApp notifications to all active numbers"""
        message = "⏰ SafiCheck Reminder: It's 4:00 PM UTC! Time for daily check-in. Please submit your feedback at: https://safi-check.onrender.com"
        
        recipients = self.get_active_numbers()
        
        if not recipients:
            print(f"⚠️ No active phone numbers found. WhatsApp not sent at {datetime.utcnow().isoformat()}")
            return
        
        print(f"\n🔔 Sending WhatsApp notifications at {datetime.utcnow().isoformat()} UTC")
        print(f"📱 Target recipients: {len(recipients)} people")
        
        success_count = 0
        for recipient in recipients:
            phone_number = recipient['number']
            name = recipient['name']
            
            if send_whatsapp_message(phone_number, message):
                success_count += 1
        
        print(f"✅ WhatsApp sent to {success_count}/{len(recipients)} recipients\n")
        
        # Log to database
        self.log_notification(success_count, len(recipients))
    
    def log_notification(self, success_count, total_count):
        """Log notification to database"""
        try:
            conn = sqlite3.connect('safi_check.db')
            cursor = conn.cursor()
            cursor.execute("""INSERT INTO sms_logs (sent_at, recipients, successful, status, message) 
                              VALUES (?, ?, ?, ?, ?)""",
                          (datetime.utcnow(), total_count, success_count, 
                           'success' if success_count > 0 else 'failed',
                           f"Sent {success_count}/{total_count} successfully via WhatsApp"))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"❌ Failed to log notification: {e}")
    
    def run(self):
        """Background thread runner"""
        print(f"⏰ WhatsApp Scheduler started - Will send at {self.target_hour_utc}:{self.target_minute:02d} UTC daily")
        print(f"🌍 Timezone: UTC (Ghana = UTC+0, Netherlands = UTC+1/UTC+2 during DST)")
        print(f"📱 Mode: {'MOCK' if MOCK_MODE else 'LIVE'} - WhatsApp messages will {'NOT ' if MOCK_MODE else ''}be sent")
        
        while self.running:
            self.check_and_send()
            time.sleep(30)
    
    def stop(self):
        self.running = False

# Start the scheduler in background thread
scheduler = NotificationScheduler()
scheduler_thread = threading.Thread(target=scheduler.run, daemon=True)
scheduler_thread.start()
print("✅ WhatsApp Scheduler thread started")

# ==================== HELPER FUNCTION FOR SAFE TIMESTAMP PARSING ====================
def safe_parse_timestamp(timestamp_str):
    """Safely parse timestamp from SQLite to ISO format"""
    if not timestamp_str:
        return None, None
    
    try:
        # Try standard format first: 'YYYY-MM-DD HH:MM:SS'
        dt = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        try:
            # Try ISO format with microseconds: 'YYYY-MM-DD HH:MM:SS.ffffff'
            dt = datetime.fromisoformat(timestamp_str.replace(' ', 'T'))
        except:
            try:
                # Try direct ISO format
                dt = datetime.fromisoformat(timestamp_str)
            except:
                # Last resort: use current time
                print(f"⚠️ Could not parse timestamp: {timestamp_str}")
                dt = datetime.now()
    
    return dt, dt.isoformat()

# ==================== ROUTES ====================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/admin')
def admin_dashboard():
    return send_from_directory('static', 'admin.html')

@app.route('/api/feedback', methods=['GET'])
def get_feedback():
    """API endpoint for admin dashboard - returns all feedback with red flags"""
    conn = sqlite3.connect('safi_check.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM checkins ORDER BY submission_date DESC")
    rows = cursor.fetchall()
    
    feedback = []
    for row in rows:
        # Get issues for this check-in
        cursor.execute("SELECT issue FROM checkin_issues WHERE checkin_id = ?", (row['id'],))
        issues = [r[0] for r in cursor.fetchall()]
        
        # Get alerts for this check-in
        cursor.execute("SELECT keyword, severity FROM alerts WHERE checkin_id = ?", (row['id'],))
        alerts = [{'keyword': r[0], 'severity': r[1]} for r in cursor.fetchall()]
        
        # Convert mood to rating and score
        mood = row['mood'] or ''
        if 'Thumbs Up' in mood or '👍' in mood:
            rating = 'good'
            mood_score = 8
        else:
            rating = 'bad'
            mood_score = 3
        
        # Check for red flags in comments (also stored in alerts)
        red_flags = [a['keyword'] for a in alerts if a['severity'] in ['critical', 'high']]
        
        # Also check comments directly for additional keywords
        if row['comments']:
            comments_lower = row['comments'].lower()
            misconduct_keywords = ['harassment', 'bully', 'unsafe', 'dangerous', 'accident', 
                                   'broken', 'threat', 'abuse', 'discrimination', 'toxic',
                                   'harassed', 'assault', 'violence', 'illegal', 'corruption']
            for kw in misconduct_keywords:
                if kw in comments_lower and kw not in red_flags:
                    red_flags.append(kw)
        
        # Safely parse timestamp to ISO format
        dt_obj, iso_timestamp = safe_parse_timestamp(row['submission_date'])
        
        # Get day of week from parsed datetime
        day = ""
        if dt_obj:
            day = dt_obj.strftime('%A')
        
        # Create display timestamp for UI
        display_timestamp = ""
        if dt_obj:
            display_timestamp = dt_obj.strftime('%m/%d/%Y, %I:%M:%S %p')
        
        # Get location - could be NULL from old entries, so provide fallback
        location = row['location'] if row['location'] else 'Ashaiman'
        
        feedback.append({
            'id': row['id'],
            'location': location,
            'rating': rating,
            'moodScore': mood_score,
            'comment': row['comments'] or '',
            'ip': row['ip_address'] or '127.0.0.1',
            'redFlags': red_flags,
            'timestamp': iso_timestamp,
            'timestampDisplay': display_timestamp,
            'day': day,
            'issues': issues,
            'hasRedFlag': len(red_flags) > 0
        })
    
    conn.close()
    print(f"📊 API returning {len(feedback)} feedback entries ({sum(1 for f in feedback if f['hasRedFlag'])} with red flags)")
    return jsonify(feedback)

@app.route('/api/feedback', methods=['DELETE'])
def delete_feedback():
    data = request.get_json()
    feedback_id = data.get('id')
    
    if not feedback_id:
        return jsonify({'error': 'No ID provided'}), 400
    
    conn = sqlite3.connect('safi_check.db')
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM checkin_issues WHERE checkin_id = ?", (feedback_id,))
    cursor.execute("DELETE FROM alerts WHERE checkin_id = ?", (feedback_id,))
    cursor.execute("DELETE FROM checkins WHERE id = ?", (feedback_id,))
    
    conn.commit()
    conn.close()
    
    print(f"🗑️ Deleted feedback ID: {feedback_id}")
    return jsonify({'success': True})

@app.route('/submit', methods=['POST'])
def submit():
    try:
        mood = request.form.get('mood')
        # Get location from form, fallback to 'Ashaiman' if not provided
        location = request.form.get('location') or 'Ashaiman'
        issues_list = request.form.getlist('issues')
        comments = request.form.get('comments', '').strip()
        
        if not mood:
            return jsonify({'success': False, 'error': 'Please select your mood'}), 400
        
        ip_address = request.remote_addr
        
        print(f"📝 Received submission - Mood: {mood}, Location: {location}, Issues: {issues_list}")
        
        conn = sqlite3.connect('safi_check.db')
        cursor = conn.cursor()
        
        # Store timestamp in standard format (no microseconds for consistency)
        current_time = datetime.now().replace(microsecond=0)
        
        cursor.execute("""INSERT INTO checkins (mood, comments, submission_date, ip_address, location) 
                          VALUES (?, ?, ?, ?, ?)""",
                      (mood, comments, current_time, ip_address, location))
        checkin_id = cursor.lastrowid
        
        for issue in issues_list:
            cursor.execute("INSERT INTO checkin_issues (checkin_id, issue) VALUES (?, ?)",
                          (checkin_id, issue))
        
        # Check for misconduct/red flags
        misconduct_keywords = {
            'harassment': 'critical',
            'bullying': 'critical',
            'unsafe': 'high',
            'dangerous': 'high',
            'accident': 'high',
            'broken': 'medium',
            'threat': 'critical',
            'abuse': 'critical',
            'discrimination': 'critical',
            'toxic': 'medium',
            'harassed': 'critical',
            'assault': 'critical',
            'violence': 'critical'
        }
        
        comments_lower = comments.lower()
        alerts_created = []
        
        for keyword, severity in misconduct_keywords.items():
            if keyword in comments_lower:
                cursor.execute("""INSERT INTO alerts (checkin_id, keyword, severity, details) 
                                  VALUES (?, ?, ?, ?)""",
                              (checkin_id, keyword, severity, comments[:200]))
                alerts_created.append(keyword)
        
        conn.commit()
        conn.close()
        
        print(f"✅ Saved: Mood={mood}, Location={location}, Issues={issues_list}, IP={ip_address}")
        if alerts_created:
            print(f"🚨 RED FLAGS DETECTED: {', '.join(alerts_created)}")
        
        return jsonify({'success': True, 'alerts': alerts_created})
    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== WHATSAPP API ENDPOINTS ====================

@app.route('/api/notification-numbers', methods=['GET'])
def get_notification_numbers():
    """Get all notification phone numbers"""
    conn = sqlite3.connect('safi_check.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM notification_numbers ORDER BY country, name")
    numbers = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(numbers)

@app.route('/api/notification-numbers', methods=['POST'])
def add_notification_number():
    """Add a new phone number to notify"""
    data = request.get_json()
    phone_number = data.get('phone_number')
    name = data.get('name', '')
    country = data.get('country', 'Other')
    
    if not phone_number:
        return jsonify({'success': False, 'error': 'Phone number required'}), 400
    
    if not phone_number.startswith('+'):
        phone_number = '+' + phone_number
    
    conn = sqlite3.connect('safi_check.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute("INSERT INTO notification_numbers (phone_number, name, country, created_at) VALUES (?, ?, ?, ?)",
                      (phone_number, name, country, datetime.utcnow()))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'success': False, 'error': 'Phone number already exists'}), 400

@app.route('/api/scheduler-status', methods=['GET'])
def get_scheduler_status():
    """Get scheduler status"""
    now = datetime.utcnow()
    next_run = datetime(now.year, now.month, now.day, 16, 0, 0)
    if now.hour >= 16:
        next_run = next_run.replace(day=next_run.day + 1)
    
    conn = sqlite3.connect('safi_check.db')
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM notification_numbers WHERE is_active = 1")
    active_count = cursor.fetchone()[0]
    conn.close()
    
    return jsonify({
        'scheduler_running': True,
        'target_hour_utc': 16,
        'next_run_utc': next_run.isoformat(),
        'active_recipients': active_count,
        'mode': 'MOCK' if MOCK_MODE else 'LIVE',
        'timezone_note': 'UTC (Ghana = UTC+0, Netherlands = UTC+1/UTC+2 during DST)'
    })

@app.route('/api/test-whatsapp', methods=['GET'])
def test_whatsapp():
    """Test endpoint - send immediate WhatsApp message (remove after testing)"""
    phone = request.args.get('phone')
    if not phone:
        return jsonify({'error': 'Provide ?phone=+233XXXXXXXXX'}), 400
    
    result = send_whatsapp_message(phone, "🧪 Test message from SafiCheck! Your WhatsApp integration is working. 🎉")
    
    return jsonify({
        'success': result,
        'phone': phone,
        'mode': 'LIVE' if not MOCK_MODE else 'MOCK'
    })

# ==================== USER AUTHENTICATION API ====================

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    conn = sqlite3.connect('safi_check.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ? AND password = ?", (username, password))
    user = cursor.fetchone()
    conn.close()
    
    if user:
        return jsonify({'success': True, 'username': username})
    else:
        return jsonify({'success': False, 'error': 'Invalid credentials'})

@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    if len(password) < 6:
        return jsonify({'success': False, 'error': 'Password must be at least 6 characters'})
    
    conn = sqlite3.connect('safi_check.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute("INSERT INTO users (username, password, created_at) VALUES (?, ?, ?)",
                       (username, password, datetime.now()))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'success': False, 'error': 'Username already exists'})

@app.route('/api/reset-password', methods=['POST'])
def api_reset_password():
    data = request.get_json()
    username = data.get('username')
    new_password = data.get('new_password')
    
    if len(new_password) < 6:
        return jsonify({'success': False, 'error': 'Password must be at least 6 characters'})
    
    conn = sqlite3.connect('safi_check.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET password = ? WHERE username = ?", (new_password, username))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/users', methods=['GET'])
def api_get_users():
    conn = sqlite3.connect('safi_check.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, created_at FROM users")
    users = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(users)

@app.route('/api/delete-user', methods=['POST'])
def api_delete_user():
    data = request.get_json()
    username = data.get('username')
    
    if username == 'admin':
        return jsonify({'success': False, 'error': 'Cannot delete default admin user'})
    
    conn = sqlite3.connect('safi_check.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/login-logs', methods=['GET'])
def api_get_login_logs():
    return jsonify([])

if __name__ == '__main__':
    print("=" * 60)
    print("🌍 Safi-Check System Running!")
    print("=" * 60)
    print("📝 Employee Form: http://localhost:5000")
    print("🔐 Admin Dashboard: http://localhost:5000/admin")
    print("=" * 60)
    print("👤 Admin Login: admin / admin123")
    print("=" * 60)
    print(f"📱 WhatsApp Mode: {'MOCK (test mode)' if MOCK_MODE else 'LIVE'}")
    print(f"⏰ Scheduler: Daily at 16:00 UTC")
    print("=" * 60)
    app.run(debug=True, port=5000)
