from werkzeug.security import generate_password_hash
import sqlite3

# Generate new password hash
new_hash = generate_password_hash('password123')

# Update database
conn = sqlite3.connect('database.db')
conn.execute("UPDATE users SET password=? WHERE email='vinoleskeey@gmail.com'", (new_hash,))
conn.commit()
conn.close()

print("Password reset successfully!")
print(f"New hash: {new_hash}")
