import sqlite3

conn = sqlite3.connect('safi_check.db')
cursor = conn.cursor()

# Add missing columns if they don't exist
try:
    cursor.execute("ALTER TABLE checkins ADD COLUMN ip_address TEXT")
    print("✅ Added ip_address column")
except:
    print("⚠️ ip_address column already exists")

try:
    cursor.execute("ALTER TABLE checkins ADD COLUMN location TEXT DEFAULT 'Ashaiman'")
    print("✅ Added location column")
except:
    print("⚠️ location column already exists")

conn.commit()
conn.close()

print("Database updated successfully!")