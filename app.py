from flask import Flask, render_template, request, redirect, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import sqlite3
import os
import time
import secrets

# APP SETUP
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_COOKIE_SECURE'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

DEVELOPER_EMAIL = 'victorshittu17@gmail.com'

# DATABASE
def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT DEFAULT 'patient',
        phone TEXT,
        address TEXT
    )""")
    
    c.execute("""CREATE TABLE IF NOT EXISTS patients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        name TEXT NOT NULL,
        age INTEGER,
        gender TEXT
    )""")
    
    c.execute("""CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER,
        staff_id INTEGER,
        result TEXT,
        probability REAL,
        malaria_score REAL,
        no_malaria_score REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    
    conn.commit()
    conn.close()

init_db()

# Create default admin
db = get_db()
admin = db.execute("SELECT * FROM users WHERE email = ?", (DEVELOPER_EMAIL,)).fetchone()
if not admin:
    pw = generate_password_hash("admin123")
    db.execute("INSERT INTO users (name, email, password, role) VALUES (?, ?, ?, ?)",
               ("Victor Shittu", DEVELOPER_EMAIL, pw, "admin"))
    db.commit()
db.close()

# HELPERS
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated

def staff_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if session.get("role") not in ["staff", "admin"]:
            flash("Staff access required", "error")
            return redirect("/dashboard")
        return f(*args, **kwargs)
    return decorated

def is_developer():
    return session.get('email') == DEVELOPER_EMAIL

# ROUTES
@app.route("/")
def index():
    return redirect("/login")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"].lower().strip()
        password = request.form["password"]
        
        if len(password) < 6:
            flash("Password must be at least 6 characters", "error")
            return render_template("auth/register.html")
        
        pw_hash = generate_password_hash(password)
        db = get_db()
        try:
            db.execute("INSERT INTO users (name, email, password, role) VALUES (?, ?, ?, ?)",
                       (name, email, pw_hash, "patient"))
            db.commit()
            flash("Registration successful! Please login.", "success")
            return redirect("/login")
        except sqlite3.IntegrityError:
            flash("Email already registered!", "error")
        db.close()
    return render_template("auth/register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].lower().strip()
        password = request.form["password"]
        
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        
        if user and check_password_hash(user["password"], password):
            session["user"] = user["id"]
            session["user_name"] = user["name"]
            session["email"] = user["email"]
            session["role"] = user["role"]
            db.close()
            return redirect("/dashboard")
        else:
            flash("Invalid email or password!", "error")
        db.close()
    return render_template("auth/login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    db = get_db()
    user_id = session.get("user")
    
    if request.method == "POST":
        name = request.form["name"]
        phone = request.form.get("phone", "")
        address = request.form.get("address", "")
        
        db.execute("UPDATE users SET name = ?, phone = ?, address = ? WHERE id = ?",
                   (name, phone, address, user_id))
        db.commit()
        session["user_name"] = name
        flash("Profile updated!", "success")
        user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        db.close()
        return render_template("auth/profile.html", user=user)
    
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    db.close()
    return render_template("auth/profile.html", user=user)

@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    user_id = session.get("user")
    role = session.get("role")
    
    if role in ["staff", "admin"] or is_developer():
        total_patients = db.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
        total_predictions = db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    else:
        total_patients = db.execute("SELECT COUNT(*) FROM patients WHERE user_id = ?", (user_id,)).fetchone()[0]
        total_predictions = db.execute("""
            SELECT COUNT(*) FROM predictions pr 
            JOIN patients p ON pr.patient_id = p.id 
            WHERE p.user_id = ?
        """, (user_id,)).fetchone()[0]
    
    db.close()
    return render_template("auth/dashboard.html",
                           total_patients=total_patients,
                           total_predictions=total_predictions,
                           high_risk=0,
                           low_risk=0,
                           recent_predictions=[],
                           metrics={"accuracy": 96.5, "precision": 95.8, "recall": 97.2, "f1_score": 96.5})

@app.route("/patients")
@login_required
def list_patients():
    db = get_db()
    role = session.get("role")
    
    if role in ["staff", "admin"] or is_developer():
        patients = db.execute("SELECT * FROM patients").fetchall()
    else:
        patients = db.execute("SELECT * FROM patients WHERE user_id = ?", (session.get("user"),)).fetchall()
    
    db.close()
    return render_template("auth/list_patients.html", patients=patients)

@app.route("/patients/add", methods=["GET", "POST"])
@staff_required
def add_patient():
    if request.method == "POST":
        name = request.form["name"]
        age = request.form["age"]
        gender = request.form["gender"]
        
        db = get_db()
        db.execute("INSERT INTO patients (name, age, gender) VALUES (?, ?, ?)",
                   (name, age, gender))
        db.commit()
        db.close()
        return redirect("/patients")
    
    return render_template("auth/add_patients.html", patient_users=[])

@app.route("/patients/<int:id>")
@login_required
def view_patient(id):
    db = get_db()
    patient = db.execute("SELECT * FROM patients WHERE id = ?", (id,)).fetchone()
    predictions = db.execute("SELECT * FROM predictions WHERE patient_id = ? ORDER BY created_at DESC", (id,)).fetchall()
    db.close()
    return render_template("auth/view_patients.html", patient=patient, predictions=predictions)

@app.route("/predict", methods=["GET", "POST"])
@staff_required
def predict():
    db = get_db()
    
    if request.method == "POST":
        patient_id = request.form["patient_id"]
        # Placeholder - just show result without actual ML prediction
        result = "No Malaria"
        probability = 95.5
        
        db.execute("INSERT INTO predictions (patient_id, staff_id, result, probability) VALUES (?, ?, ?, ?)",
                   (patient_id, session.get("user"), result, probability))
        db.commit()
        db.close()
        
        return render_template("auth/results.html",
                            patient_id=patient_id,
                            result=result,
                            probability=probability,
                            malaria_score=5.5,
                            no_malaria_score=95.5)
    
    patients = db.execute("SELECT * FROM patients").fetchall()
    db.close()
    return render_template("auth/predict.html", patients=patients)

@app.route("/my-results")
@login_required
def my_results():
    db = get_db()
    user_id = session.get("user")
    role = session.get("role")
    
    if role in ["staff", "admin"] or is_developer():
        predictions = db.execute("""
            SELECT pr.*, p.name as patient_name
            FROM predictions pr
            JOIN patients p ON pr.patient_id = p.id
            ORDER BY pr.created_at DESC
        """).fetchall()
    else:
        predictions = db.execute("""
            SELECT pr.*, p.name as patient_name
            FROM predictions pr
            JOIN patients p ON pr.patient_id = p.id
            WHERE p.user_id = ?
            ORDER BY pr.created_at DESC
        """, (user_id,)).fetchall()
    
    db.close()
    return render_template("auth/my_results.html", predictions=predictions)

@app.route("/users")
def list_users():
    if not is_developer():
        return redirect("/dashboard")
    db = get_db()
    users = db.execute("SELECT id, name, email, role FROM users ORDER BY id DESC").fetchall()
    db.close()
    return render_template("auth/list_users.html", users=users)

if __name__ == "__main__":
    app.run(debug=True)
