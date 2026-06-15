from flask import Flask, render_template, send_from_directory, request, jsonify
import sqlite3
from datetime import datetime
import json

app = Flask(__name__)

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
    
    # Check if admin exists, if not create default
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = 'admin'")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (username, password, created_at) VALUES (?, ?, ?)",
                       ('admin', 'admin123', datetime.now()))
    
    conn.commit()
    conn.close()
    print("✅ Database initialized")

init_db()

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
        
        # FIX 1 & 2: Safely parse timestamp to ISO format
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
            # FIX 1: Send ISO format timestamp that JS can parse reliably
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
    app.run(debug=True, port=5000)
