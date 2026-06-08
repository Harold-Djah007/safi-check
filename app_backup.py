from flask import Flask, render_template, request, jsonify
import sqlite3
from datetime import datetime

app = Flask(__name__)

# Initialize database
def init_db():
    conn = sqlite3.connect('safi_check.db')
    conn.execute('''CREATE TABLE IF NOT EXISTS checkins
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     mood TEXT, comments TEXT, submission_date TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS checkin_issues
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     checkin_id INTEGER, issue TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS alerts
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     checkin_id INTEGER, keyword TEXT, severity TEXT,
                     details TEXT, acknowledged BOOLEAN DEFAULT 0)''')
    conn.close()

init_db()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/submit', methods=['POST'])
def submit():
    try:
        mood = request.form.get('mood')
        issues_list = request.form.getlist('issues')
        comments = request.form.get('comments', '').strip()
        
        if not mood:
            return jsonify({'success': False, 'error': 'Please select your mood'}), 400
        
        conn = sqlite3.connect('safi_check.db')
        cursor = conn.cursor()
        
        # Save to checkins table
        cursor.execute("INSERT INTO checkins (mood, comments, submission_date) VALUES (?, ?, ?)",
                      (mood, comments, datetime.now()))
        checkin_id = cursor.lastrowid
        
        # Save each issue to checkin_issues table
        for issue in issues_list:
            cursor.execute("INSERT INTO checkin_issues (checkin_id, issue) VALUES (?, ?)",
                          (checkin_id, issue))
        
        # Check for red flags in comments
        red_flags = ['harassment', 'bully', 'unsafe', 'dangerous', 'accident', 'broken']
        for keyword in red_flags:
            if keyword in comments.lower():
                cursor.execute("INSERT INTO alerts (checkin_id, keyword, severity, details) VALUES (?, ?, ?, ?)",
                              (checkin_id, keyword, 'high', comments[:200]))
        
        conn.commit()
        conn.close()
        
        # Print to terminal for debugging
        print(f"✅ Saved: Mood={mood}, Issues={issues_list}, Comments={comments[:50]}")
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    print("=" * 50)
    print("✅ Safi-Check (SQLite Version)")
    print("📝 Form: http://localhost:5000")
    print("💾 Data saved to: safi_check.db")
    print("=" * 50)
    app.run(debug=True, port=5000)