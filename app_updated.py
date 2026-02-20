from flask import Flask, render_template, request, redirect, session, url_for, flash
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import sqlite3
import os
import subprocess
import time
import secrets

# ------------------- APP SETUP -------------------
app = Flask(__name__)
# Generate a strong 32-byte (256-bit) secret key for production
app.secret_key = secrets.token_hex(32)

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

# ------------------- LOGIN REQUIRED DECORATOR -------------------
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
            return "Invalid email or password!"
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
    
    # High-risk: patients with malaria_score > 50% (using the actual score from the model)
    # Use COALESCE to handle NULL values from older predictions
    high_risk = db.execute("SELECT COUNT(*) FROM predictions WHERE COALESCE(malaria_score, 0) > 50").fetchone()[0]
    low_risk = db.execute("SELECT COUNT(*) FROM predictions WHERE COALESCE(malaria_score, 0) <= 50").fetchone()[0]
    
    recent_predictions_raw = db.execute(
        "SELECT p.name, pr.result, pr.probability, pr.created_at "
        "FROM predictions pr JOIN patients p ON pr.patient_id=p.id "
        "ORDER BY pr.created_at DESC LIMIT 5"
    ).fetchall()
    # Convert Row objects to tuples for template index access
    recent_predictions = [tuple(row) for row in recent_predictions_raw]
    db.close()
    
    # Model evaluation metrics (placeholder values - in production these would be calculated from model evaluation)
    metrics = {
        "accuracy": 96.5,
        "precision": 95.8,
        "recall": 97.2,
        "f1_score": 96.5
    }

    return render_template("auth/dashboard.html",
                           total_patients=total_patients,
                           total_predictions=total_predictions,
                           high_risk=high_risk,
                           low_risk=low_risk,
                           recent_predictions=recent_predictions,
                           metrics=metrics)

# ------------------- PATIENT ROUTES -------------------
@app.route("/patients", methods=["GET"])
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

# ------------------- PREDICTION ROUTE -------------------
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
            # Save uploaded image with unique filename using timestamp
            timestamp = int(time.time() * 1000)
            ext = file.filename.rsplit(".", 1)[1].lower()
            filename = f"cell_{timestamp}_{patient_id}.{ext}"
            file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            file.save(file_path)

# Get the absolute path - the malaria_model directory is two levels up from the app
            base_dir = os.path.dirname(os.path.abspath(__file__))
            # Go up two levels to find malaria_model (from Malaria detection system to Documents to Victor SHITTU)
            model_dir = os.path.join(base_dir, "..", "..", "malaria_model")
            predict_script = os.path.join(model_dir, "malaria_predict.py")
            model_path = os.path.join(model_dir, "malaria_cnn_model.keras")
            
            # Call malaria_predict.py with both image and model paths
            try:
                output = subprocess.check_output(
                    ["python", predict_script, file_path, model_path],
                    cwd=base_dir
                )
                output = output.decode("utf-8").strip()
                # Split all values: label|probability|malaria_score|no_malaria_score
                parts = output.split("|")
                result = parts[0]
                probability = float(parts[1])
                malaria_score = float(parts[2])
                no_malaria_score = float(parts[3])
            except Exception as e:
                return f"Error running prediction: {e}"

            # Save prediction to DB with both scores
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

    # GET method: show form
    patients = db.execute("SELECT * FROM patients").fetchall()
    db.close()
    return render_template("auth/predict.html", patients=patients)

    db = get_db()
    patients = db.execute("SELECT * FROM patients").fetchall()
    db.close()
    return render_template("auth/predict.html", patients=patients)

# ------------------- RUN APP -------------------
if __name__ == "__main__":
    app.run(debug=True)
