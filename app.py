from flask import Flask, render_template, request, redirect, session, url_for, flash
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import sqlite3
import os
import time
import secrets
import numpy as np

# ------------------- APP SETUP -------------------
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# ------------------- AUTO DATABASE INITIALIZATION -------------------
def init_db():
    import sqlite3
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL
    )
    """)
    
    c.execute("""
    CREATE TABLE IF NOT EXISTS patients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        name TEXT NOT NULL,
        age INTEGER,
        gender TEXT,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)
    
    try:
        c.execute("SELECT user_id FROM patients LIMIT 1")
    except:
        c.execute("ALTER TABLE patients ADD COLUMN user_id INTEGER")
    
    c.execute("""
    CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER,
        result TEXT,
        probability REAL,
        malaria_score REAL,
        no_malaria_score REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (patient_id) REFERENCES patients(id)
    )
    """)
    
    c.execute("""
    CREATE TABLE IF NOT EXISTS staff (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        phone TEXT,
        role TEXT NOT NULL,
        department TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    conn.commit()
    conn.close()
    print("Database initialized!")

init_db()

# ------------------- LOAD TENSORFLOW MODEL -------------------
model = None

# Limit TensorFlow memory usage
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'

def preload_model():
    """Preload model at startup"""
    global model
    # Use the model from the malaria_model folder
    model_dir = "C:/Users/VICTOR SHITTU/malaria_model"
    model_file = os.path.join(model_dir, "malaria_cnn_model.keras")
    
    print(f"[DEBUG] Model dir: {model_dir}")
    print(f"[DEBUG] Model dir exists: {os.path.exists(model_dir)}")
    print(f"[DEBUG] Model file exists: {os.path.exists(model_file)}")
    
    if os.path.exists(model_file):
        try:
            import tensorflow as tf
            tf.config.threading.set_intra_op_parallelism_threads(1)
            tf.config.threading.set_inter_op_parallelism_threads(1)
            
            print(f"[DEBUG] Loading model from {model_file}...")
            model = tf.keras.models.load_model(model_file)
            print("[DEBUG] Model loaded at startup!")
        except Exception as e:
            print(f"[DEBUG] Error loading model: {e}")
            import traceback
            traceback.print_exc()
            model = None
    else:
        print(f"[DEBUG] Model file not found at: {model_file}")
    return model

print("[DEBUG] Starting model preload...")
preload_model()

def load_model():
    return model

# ------------------- ROOT ROUTE -------------------
@app.route("/")
def index():
    return redirect("/login")

UPLOAD_FOLDER = "uploads"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg"}
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# ------------------- DB CONNECTION -------------------
def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn

# ------------------- LOGIN DECORATOR -------------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user" not in session:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated_function

# ------------------- AUTH ROUTES -------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = generate_password_hash(request.form["password"])
        db = get_db()
        try:
            db.execute("INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
                       (name, email, password))
            db.commit()
            db.close()
            return redirect("/login")
        except sqlite3.IntegrityError:
            db.close()
            return "Email already registered!"
    return render_template("auth/register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        db.close()
        if user and check_password_hash(user["password"], password):
            session["user"] = user["id"]
            session["user_name"] = user["name"]
            return redirect("/dashboard")
        else:
            flash("Invalid email or password!")
            return render_template("auth/login.html")
    return render_template("auth/login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ------------------- DASHBOARD -------------------
@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    total_patients = db.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
    total_predictions = db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    high_risk = db.execute("SELECT COUNT(*) FROM predictions WHERE COALESCE(malaria_score, 0) > 50").fetchone()[0]
    low_risk = db.execute("SELECT COUNT(*) FROM predictions WHERE COALESCE(malaria_score, 0) <= 50").fetchone()[0]
    recent = db.execute(
        "SELECT p.name, pr.result, pr.probability, pr.created_at "
        "FROM predictions pr JOIN patients p ON pr.patient_id=p.id "
        "ORDER BY pr.created_at DESC LIMIT 5"
    ).fetchall()
    recent_predictions = [tuple(row) for row in recent]
    db.close()
    return render_template("auth/dashboard.html",
                           total_patients=total_patients,
                           total_predictions=total_predictions,
                           high_risk=high_risk,
                           low_risk=low_risk,
                           recent_predictions=recent_predictions,
                           metrics={"accuracy": 96.5, "precision": 95.8, "recall": 97.2, "f1_score": 96.5})

# ------------------- PATIENTS -------------------
@app.route("/patients")
@login_required
def list_patients():
    db = get_db()
    patients = db.execute("SELECT * FROM patients").fetchall()
    db.close()
    return render_template("auth/list_patients.html", patients=patients)

@app.route("/patients/add", methods=["GET", "POST"])
@login_required
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
    return render_template("auth/add_patients.html")

@app.route("/patients/<int:id>")
@login_required
def view_patient(id):
    db = get_db()
    patient = db.execute("SELECT * FROM patients WHERE id=?", (id,)).fetchone()
    predictions = db.execute("SELECT * FROM predictions WHERE patient_id=? ORDER BY created_at DESC", (id,)).fetchall()
    db.close()
    return render_template("auth/view_patients.html", patient=patient, predictions=predictions)

# ------------------- PREDICTION -------------------
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route("/predict", methods=["GET", "POST"])
@login_required
def predict():
    db = get_db()

    if request.method == "POST":
        patient_id = request.form["patient_id"]
        file = request.files["file"]

        if file and allowed_file(file.filename):
            timestamp = int(time.time() * 1000)
            ext = file.filename.rsplit(".", 1)[1].lower()
            filename = f"cell_{timestamp}_{patient_id}.{ext}"
            file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            file.save(file_path)

            model_dir = "C:/Users/VICTOR SHITTU/malaria_model"
            model_file = os.path.join(model_dir, "malaria_cnn_model.keras")
            
            if not os.path.exists(model_dir):
                return "Error: malaria_model directory not found."
            if not os.path.exists(model_file):
                return f"Error: Model file not found at {model_file}"
            if not os.path.exists(file_path):
                return f"Error: Uploaded file not found at {file_path}"
            
            try:
                tf_model = load_model()
                if tf_model is None:
                    return "Error: Could not load TensorFlow model"
                
                from tensorflow.keras.preprocessing import image
                img = image.load_img(file_path, target_size=(128, 128))
                img_array = image.img_to_array(img)
                img_array = np.expand_dims(img_array, axis=0)
                
                pred = tf_model.predict(img_array, verbose=0)[0][0]
                
                malaria_score = float(pred * 100)
                no_malaria_score = float((1 - pred) * 100)
                
                if pred > 0.5:
                    result = "Malaria Detected"
                    probability = malaria_score
                else:
                    result = "No Malaria"
                    probability = no_malaria_score
                
                print(f"Prediction: {result}, Probability: {probability}")
                
            except Exception as e:
                print(f"Prediction error: {str(e)}")
                return f"Error running prediction: {str(e)}"

            db.execute(
                "INSERT INTO predictions (patient_id, result, probability, malaria_score, no_malaria_score) VALUES (?, ?, ?, ?, ?)",
                (patient_id, result, probability, malaria_score, no_malaria_score)
            )
            db.commit()
            db.close()

            return render_template(
                "auth/results.html",
                patient_id=patient_id,
                result=result,
                probability=probability,
                malaria_score=malaria_score,
                no_malaria_score=no_malaria_score
            )

    patients = db.execute("SELECT * FROM patients").fetchall()
    db.close()
    return render_template("auth/predict.html", patients=patients)

# ------------------- STAFF -------------------
@app.route("/staff")
@login_required
def list_staff():
    db = get_db()
    staff_members = db.execute("SELECT * FROM staff ORDER BY created_at DESC").fetchall()
    department_counts = {}
    counts = db.execute("SELECT department, COUNT(*) as count FROM staff GROUP BY department").fetchall()
    for row in counts:
        department_counts[row["department"]] = row["count"]
    db.close()
    return render_template("staff/list_staff.html", staff_members=staff_members, department_counts=department_counts)

@app.route("/staff/add", methods=["GET", "POST"])
@login_required
def add_staff():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        phone = request.form["phone"]
        role = request.form["role"]
        department = request.form["department"]
        db = get_db()
        try:
            db.execute("INSERT INTO staff (name, email, phone, role, department) VALUES (?, ?, ?, ?, ?)",
                       (name, email, phone, role, department))
            db.commit()
            db.close()
            return redirect("/staff")
        except sqlite3.IntegrityError:
            db.close()
            return "Email already registered as staff!"
    return render_template("staff/add_staff.html")

@app.route("/staff/<int:id>/delete")
@login_required
def delete_staff(id):
    db = get_db()
    db.execute("DELETE FROM staff WHERE id=?", (id,))
    db.commit()
    db.close()
    return redirect("/staff")

# ------------------- USERS -------------------
@app.route("/users")
@login_required
def list_users():
    db = get_db()
    users = db.execute("SELECT id, name, email FROM users ORDER BY id DESC").fetchall()
    db.close()
    return render_template("auth/list_users.html", users=users)

@app.route("/users/<int:id>/delete")
@login_required
def delete_user(id):
    if session.get("user") == id:
        return "You cannot delete your own account!"
    db = get_db()
    db.execute("DELETE FROM predictions WHERE patient_id IN (SELECT id FROM patients WHERE user_id=?)", (id,))
    db.execute("DELETE FROM patients WHERE user_id=?", (id,))
    db.execute("DELETE FROM users WHERE id=?", (id,))
    db.commit()
    db.close()
    return redirect("/users")

# ------------------- RUN -------------------
if __name__ == "__main__":
    app.run(debug=True)
