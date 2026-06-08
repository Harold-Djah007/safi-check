from flask import Flask, render_template, send_from_directory, request, jsonify
import sqlite3
from datetime import datetime
import json

app = Flask(__name__)

# ==================== DATABASE SETUP ====================

def init_db():
    conn = sqlite3.connect('safi_check.db')
    conn.execute('''CREATE TABLE IF NOT EXISTS checkins
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     mood TEXT, 
                     comments TEXT, 
                     submission_date TIMESTAMP,
                     ip_address TEXT,
                     location TEXT DEFAULT 'Ashaiman')''')
    
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
    conn.close()
    print("✅ Database initialized")

init_db()

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
        red_flags = [a['keyword'] for a in alerts if a['severity'] == 'high']
        
        # Also check comments directly for additional keywords
        if row['comments']:
            comments_lower = row['comments'].lower()
            misconduct_keywords = ['harassment', 'bully', 'unsafe', 'dangerous', 'accident', 
                                   'broken', 'threat', 'abuse', 'discrimination', 'toxic',
                                   'harassed', 'assault', 'violence', 'illegal', 'corruption']
            for kw in misconduct_keywords:
                if kw in comments_lower and kw not in red_flags:
                    red_flags.append(kw)
        
        # Get day of week
        day = ""
        if row['submission_date']:
            try:
                day = datetime.strptime(row['submission_date'], '%Y-%m-%d %H:%M:%S').strftime('%A')
            except:
                day = "Unknown"
        
        feedback.append({
            'id': row['id'],
            'location': row['location'] or 'Ashaiman',
            'rating': rating,
            'moodScore': mood_score,
            'comment': row['comments'] or '',
            'ip': row['ip_address'] or '127.0.0.1',
            'redFlags': red_flags,
            'timestamp': row['submission_date'],
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
        issues_list = request.form.getlist('issues')
        comments = request.form.get('comments', '').strip()
        
        if not mood:
            return jsonify({'success': False, 'error': 'Please select your mood'}), 400
        
        ip_address = request.remote_addr
        
        conn = sqlite3.connect('safi_check.db')
        cursor = conn.cursor()
        
        cursor.execute("""INSERT INTO checkins (mood, comments, submission_date, ip_address, location) 
                          VALUES (?, ?, ?, ?, ?)""",
                      (mood, comments, datetime.now(), ip_address, 'Ashaiman'))
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
        
        print(f"✅ Saved: Mood={mood}, Issues={issues_list}, IP={ip_address}")
        if alerts_created:
            print(f"🚨 RED FLAGS DETECTED: {', '.join(alerts_created)}")
        
        return jsonify({'success': True, 'alerts': alerts_created})
    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

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