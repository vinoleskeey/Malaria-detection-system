from flask import Flask, render_template, request, redirect, session, flash, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
import os
import secrets
import hashlib
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import sys

# Add malaria_model to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'malaria_model'))
try:
    from malaria_predict import predict_malaria
    HAS_MODEL = True
except ImportError:
    HAS_MODEL = False

# ==================== APP SETUP ====================
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

DEVELOPER_EMAIL = 'victorshittu17@gmail.com'

# Email Configuration
MAIL_USERNAME = os.environ.get('MAIL_USERNAME', 'victorshittu17@gmail.com')
MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', 'ifygknbpoxbvizpk')
MAIL_HOST = 'smtp.gmail.com'
MAIL_PORT = 587

# Debug: Print mail config (without exposing password)
print(f"[DEBUG] MAIL_USERNAME: {MAIL_USERNAME}")
print(f"[DEBUG] MAIL_PASSWORD length: {len(MAIL_PASSWORD) if MAIL_PASSWORD else 0}")

# ==================== SQLALCHEMY SETUP ====================
db = SQLAlchemy(app)

# ==================== MODELS ====================

class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='patient')
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    patient = db.relationship('Patient', backref='user', uselist=False)
    staff_profile = db.relationship('StaffProfile', backref='user', uselist=False)
    predictions = db.relationship('Prediction', backref='staff', lazy=True)

class Patient(db.Model):
    __tablename__ = 'patients'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    name = db.Column(db.String(100), nullable=False)
    age = db.Column(db.Integer)
    gender = db.Column(db.String(10))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    predictions = db.relationship('Prediction', backref='patient', lazy=True)

class Prediction(db.Model):
    __tablename__ = 'predictions'
    
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'))
    staff_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    staff_name = db.Column(db.String(100))
    result = db.Column(db.String(50))
    probability = db.Column(db.Float)
    malaria_score = db.Column(db.Float)
    no_malaria_score = db.Column(db.Float)
    symptoms = db.Column(db.Text)
    image_path = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class StaffProfile(db.Model):
    __tablename__ = 'staff_profiles'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True)
    department = db.Column(db.String(50))
    position = db.Column(db.String(50))
    employee_id = db.Column(db.String(50))

class PasswordReset(db.Model):
    __tablename__ = 'password_resets'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    token = db.Column(db.String(100), unique=True)
    expires = db.Column(db.DateTime)
    used = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Create all tables and default admin account
with app.app_context():
    db.create_all()
    
    # Add image_path column if it doesn't exist (migration)
    try:
        from sqlalchemy import text
        result = db.session.execute(text("PRAGMA table_info(predictions)"))
        columns = [row[1] for row in result]
        if 'image_path' not in columns:
            db.session.execute(text("ALTER TABLE predictions ADD COLUMN image_path VARCHAR(200)"))
            db.session.commit()
            print("Added image_path column to predictions table")
    except Exception as e:
        print(f"Migration check: {e}")
    
    # Create default admin account
    admin = User.query.filter_by(email=DEVELOPER_EMAIL).first()
    if not admin:
        pw_hash = generate_password_hash("admin123")
        admin = User(name="Victor Shittu", email=DEVELOPER_EMAIL, password=pw_hash, role="admin")
        db.session.add(admin)
        db.session.commit()
        print("Admin account created!")

# ==================== DECORATORS ====================
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
            flash("Staff access required for this action", "error")
            return redirect("/dashboard")
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if session.get("role") != "admin":
            flash("Admin access required", "error")
            return redirect("/dashboard")
        return f(*args, **kwargs)
    return decorated

def is_developer():
    return session.get('email') == DEVELOPER_EMAIL

# ==================== EMAIL HELPERS ====================

def send_password_reset_email(email, token):
    """Send password reset email to user"""
    try:
        print(f"[EMAIL] Starting password reset email for: {email}")
        
        # Get base URL - try multiple sources
        base_url = os.environ.get('WEB_URL')
        if not base_url:
            # Try to get from Railway env vars
            base_url = os.environ.get('RAILWAY_PUBLIC_DOMAIN')
            if base_url:
                base_url = f"https://{base_url}"
            else:
                base_url = 'https://web-production-e60045.up.railway.app'
        
        reset_link = f"{base_url}/reset_password/{token}"
        print(f"[EMAIL] Reset link: {reset_link}")
        
        # Create email message
        msg = MIMEMultipart()
        msg['From'] = MAIL_USERNAME
        msg['To'] = email
        msg['Subject'] = 'Password Reset - Malaria Detection System'
        
        # Email body
        body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <div style="max-width: 600px; margin: 0 auto; background: #f5f5f5; padding: 30px; border-radius: 10px;">
                <h2 style="color: #1a237e;">Password Reset Request</h2>
                <p>You requested a password reset for your Malaria Detection System account.</p>
                <p>Click the button below to reset your password:</p>
                <div style="text-align: center; margin: 30px 0;">
                    <a href="{reset_link}" style="background: linear-gradient(135deg, #1a237e 0%, #0d47a1 100%); color: white; padding: 15px 30px; text-decoration: none; border-radius: 8px; display: inline-block;">Reset Password</a>
                </div>
                <p>Or copy and paste this link in your browser:</p>
                <p style="word-break: break-all; color: #0d47a1;">{reset_link}</p>
                <p style="color: #666; font-size: 14px;">This link will expire in 24 hours.</p>
                <p style="color: #666; font-size: 14px;">If you didn't request this password reset, please ignore this email.</p>
            </div>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(body, 'html'))
        
        # Connect to Gmail SMTP server
        server = smtplib.SMTP(MAIL_HOST, MAIL_PORT)
        server.starttls()
        server.login(MAIL_USERNAME, MAIL_PASSWORD)
        
        # Send email
        server.sendmail(MAIL_USERNAME, email, msg.as_string())
        server.quit()
        
        return True
    except Exception as e:
        import traceback
        print(f"[EMAIL ERROR] Failed to send email: {e}")
        print(f"[EMAIL ERROR] Traceback: {traceback.format_exc()}")
        return False

# ==================== ROUTES ====================

@app.route("/")
def index():
    return redirect("/login")

# ------------------- AUTH -------------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"].lower().strip()
        password = request.form["password"]
        confirm = request.form.get("confirm_password", "")
        
        if password != confirm:
            flash("Passwords do not match!", "error")
            return render_template("auth/register.html")
        
        if len(password) < 6:
            flash("Password must be at least 6 characters", "error")
            return render_template("auth/register.html")
        
        pw_hash = generate_password_hash(password)
        
        # Check if email exists
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash("Email already registered!", "error")
            return render_template("auth/register.html")
        
        user = User(name=name, email=email, password=pw_hash, role="patient")
        db.session.add(user)
        db.session.commit()
        
        flash("Registration successful! Please login.", "success")
        return redirect("/login")
    
    return render_template("auth/register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].lower().strip()
        password = request.form["password"]
        
        user = User.query.filter_by(email=email).first()
        
        if user and check_password_hash(user.password, password):
            # Store session
            session["user"] = user.id
            session["user_name"] = user.name
            session["email"] = user.email
            session["role"] = user.role
            return redirect("/dashboard")
        else:
            flash("Invalid email or password!", "error")
    
    return render_template("auth/login.html")

@app.route("/logout")
def logout():
    # Store original admin info before clearing
    original_email = session.get("original_email")
    original_role = session.get("original_role")
    
    session.clear()
    
    # If switching back from patient view, restore admin
    if original_email and original_email == DEVELOPER_EMAIL:
        session["user"] = session.get("original_user_id")
        session["user_name"] = session.get("original_user_name")
        session["email"] = original_email
        session["role"] = original_role
    
    return redirect("/login")

# ------------------- PROFILE -------------------

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user_id = session.get("user")
    user = db.session.get(User, user_id)
    
    if request.method == "POST":
        name = request.form["name"]
        phone = request.form.get("phone", "")
        address = request.form.get("address", "")
        
        user.name = name
        user.phone = phone
        user.address = address
        db.session.commit()
        
        session["user_name"] = name
        flash("Profile updated successfully!", "success")
        return render_template("auth/profile.html", user=user)
    
    return render_template("auth/profile.html", user=user)

# ------------------- DASHBOARD -------------------

@app.route("/dashboard")
@login_required
def dashboard():
    """Main dashboard - redirects based on role"""
    role = session.get("role")
    viewing_as_patient = session.get("viewing_as_patient", False)
    
    if role in ["admin", "staff"] and not viewing_as_patient:
        return redirect("/admin/dashboard")
    else:
        return redirect("/patient/dashboard")

@app.route("/admin/dashboard")
@login_required
def admin_dashboard():
    """Admin/Staff Dashboard"""
    role = session.get("role")
    viewing_as = session.get("viewing_as_patient", False)

    if role not in ["admin", "staff"] or viewing_as:
        return redirect("/patient/dashboard")
    
    # Staff/Admin Dashboard - full access
    total_patients = Patient.query.count()
    total_predictions = Prediction.query.count()
    high_risk = Prediction.query.filter_by(result='Malaria Detected').count()
    low_risk = Prediction.query.filter_by(result='No Malaria').count()
    
    # Get recent predictions with patient names
    recent = db.session.query(
        Prediction, Patient.name
    ).join(Patient).order_by(Prediction.created_at.desc()).limit(5).all()
    
    # Format predictions for template
    recent_predictions = []
    for pred, patient_name in recent:
        recent_predictions.append({
            'id': pred.id,
            'patient_name': patient_name,
            'result': pred.result,
            'probability': pred.probability,
            'staff_name': pred.staff_name,
            'created_at': pred.created_at
        })
    
    # Get all patients for switching
    all_patients = User.query.filter_by(role='patient').all()
    
    # Get all staff for switching
    all_staff = User.query.filter(User.role.in_(['staff', 'admin'])).all()
    
    return render_template("admin/dashboard.html",
                           total_patients=total_patients,
                           total_predictions=total_predictions,
                           high_risk=high_risk,
                           low_risk=low_risk,
                           recent_predictions=recent_predictions,
                           metrics={"accuracy": 96.5, "precision": 95.8, "recall": 97.2, "f1_score": 96.5},
                           all_patients=all_patients,
                           all_staff=all_staff,
                           is_admin=True)

@app.route("/patient/dashboard")
@login_required
def patient_dashboard():
    """Patient Dashboard"""
    user_id = session.get("user")
    role = session.get("role")
    
    # Only patients can access this dashboard (unless admin is viewing as patient)
    if role not in ["patient"] and not session.get("viewing_as_patient"):
        return redirect("/admin/dashboard")
    
    # Get patient's own predictions
    patient = Patient.query.filter_by(user_id=user_id).first()
    
    if patient:
        predictions = Prediction.query.filter_by(patient_id=patient.id).order_by(Prediction.created_at.desc()).all()
    else:
        predictions = []
    
    return render_template("auth/dashboard.html",
                           predictions=predictions,
                           is_admin=False,
                           patient=patient)

# ------------------- ACCOUNT SWITCHING (For Admin) -------------------

@app.route("/switch-to-patient/<int:patient_id>")
@login_required
def switch_to_patient(patient_id):
    # Only admin/developer can switch accounts
    if not is_developer() and session.get("role") != "admin":
        flash("Access denied", "error")
        return redirect("/dashboard")
    
    patient_user = User.query.filter_by(id=patient_id, role='patient').first()
    
    if patient_user:
        # Store current admin info
        session["original_user_id"] = session.get("user")
        session["original_user_name"] = session.get("user_name")
        session["original_email"] = session.get("email")
        session["original_role"] = session.get("role")
        
        # Switch to patient view
        session["user"] = patient_user.id
        session["user_name"] = patient_user.name
        session["email"] = patient_user.email
        session["role"] = "patient"
        session["viewing_as_patient"] = True
        
        flash(f"Switched to {patient_user.name}'s view", "success")
        return redirect("/dashboard")
    
    flash("Patient not found", "error")
    return redirect("/dashboard")

@app.route("/switch-back-admin")
@login_required
def switch_back_admin():
    if session.get("original_email"):
        session["user"] = session.get("original_user_id")
        session["user_name"] = session.get("original_user_name")
        session["email"] = session.get("original_email")
        session["role"] = session.get("original_role")
        session.pop("viewing_as_patient", None)
        flash("Returned to admin view", "success")
    return redirect("/dashboard")

# ------------------- PATIENTS (Staff Only) -------------------

@app.route("/patients")
@login_required
def list_patients():
    role = session.get("role")
    viewing_as = session.get("viewing_as_patient", False)

    if role in ["staff", "admin"] and not viewing_as:
        # Show all patients
        patients = Patient.query.join(User, Patient.user_id == User.id, isouter=True).all()
        return render_template("auth/list_patients.html", patients=patients)

    return redirect("/dashboard")

# ------------------- MY RESULTS (Patient) -------------------

@app.route("/my-results")
@login_required
def my_results():
    """Show patient's own test results"""
    user_id = session.get("user")
    
    # Get patient's own predictions
    patient = Patient.query.filter_by(user_id=user_id).first()
    
    if patient:
        predictions = Prediction.query.filter_by(patient_id=patient.id).order_by(Prediction.created_at.desc()).all()
    else:
        predictions = []
    
    return render_template("auth/my_results.html", predictions=predictions, patient=patient)

# ------------------- MEDICAL RECORDS (Patient) -------------------

@app.route("/medical-records")
@login_required
def medical_records():
    """Show patient's medical records"""
    user_id = session.get("user")
    
    # Get patient info
    patient = Patient.query.filter_by(user_id=user_id).first()
    
    # Get all predictions for this patient
    if patient:
        predictions = Prediction.query.filter_by(patient_id=patient.id).order_by(Prediction.created_at.desc()).all()
    else:
        predictions = []
    
    return render_template("auth/medical_records.html", patient=patient, predictions=predictions)

# ------------------- ADMIN LOGIN -------------------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """Separate admin login portal"""
    if request.method == "POST":
        email = request.form["email"].lower().strip()
        password = request.form["password"]
        
        user = User.query.filter_by(email=email).first()
        
        if user and check_password_hash(user.password, password):
            # Check if user is admin or staff
            if user.role not in ["admin", "staff"]:
                flash("Admin/Staff access only!", "error")
                return render_template("admin/login.html")
            
            # Store session
            session["user"] = user.id
            session["user_name"] = user.name
            session["email"] = user.email
            session["role"] = user.role
            flash(f"Welcome {user.name}!", "success")
            return redirect("/dashboard")
        else:
            flash("Invalid email or password!", "error")
    
    return render_template("admin/login.html")

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")

# ------------------- FORGOT PASSWORD -------------------

@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    """Handle password reset requests"""
    if request.method == "POST":
        email = request.form["email"].lower().strip()
        
        user = User.query.filter_by(email=email).first()
        
        if user:
            # Generate reset token
            token = hashlib.sha256(f"{email}{datetime.now()}".encode()).hexdigest()[:32]
            
            # Delete any existing tokens for this user
            PasswordReset.query.filter_by(user_id=user.id).delete()
            
            # Insert new token (expires in 24 hours)
            expires = datetime.now() + timedelta(hours=24)
            reset = PasswordReset(user_id=user.id, token=token, expires=expires)
            db.session.add(reset)
            db.session.commit()
            
            # Try to send the email
            email_sent = send_password_reset_email(email, token)
            
            if email_sent:
                flash(f"Password reset link sent to {email}! Please check your inbox.", "success")
            else:
                # Still show success to not reveal if email exists
                flash("If this email exists in our system, reset instructions will be sent.", "info")
        else:
            # Don't reveal if email exists
            flash("If this email exists in our system, reset instructions will be sent.", "info")
        
        return redirect("/login")
    
    return render_template("auth/forgot_password.html")

@app.route("/reset_password/<token>", methods=["GET", "POST"])
def reset_password(token):
    """Handle password reset with token"""
    
    # Find valid token
    reset = PasswordReset.query.filter_by(token=token, used=0).first()
    
    if not reset or reset.expires < datetime.now():
        flash("Invalid or expired reset link!", "error")
        return redirect("/forgot_password")
    
    if request.method == "POST":
        password = request.form["password"]
        confirm = request.form["confirm_password"]
        
        if password != confirm:
            flash("Passwords do not match!", "error")
            return render_template("auth/reset_password.html")
        
        if len(password) < 6:
            flash("Password must be at least 6 characters", "error")
            return render_template("auth/reset_password.html")
        
        # Update password
        user = db.session.get(User, reset.user_id)
        user.password = generate_password_hash(password)
        
        # Mark token as used
        reset.used = 1
        db.session.commit()
        
        flash("Password reset successful! Please login with your new password.", "success")
        return redirect("/login")
    
    return render_template("auth/reset_password.html")

# ------------------- ADMIN ROUTES -------------------

@app.route("/admin/patients")
@staff_required
def admin_list_patients():
    """List all patients (admin/staff only)"""
    patients = Patient.query.join(User, Patient.user_id == User.id, isouter=True).order_by(Patient.created_at.desc()).all()
    return render_template("admin/list_patients.html", patients=patients)

@app.route("/admin/patients/add", methods=["GET", "POST"])
@staff_required
def admin_add_patient():
    """Add a new patient (admin/staff only)"""
    if request.method == "POST":
        name = request.form["name"]
        age = request.form.get("age")
        gender = request.form.get("gender")

        patient = Patient(name=name, age=age, gender=gender)
        db.session.add(patient)
        db.session.commit()
        # Save the uploaded image
        filename = secure_filename(f"{patient_id}_{int(datetime.now().timestamp())}_{image.filename}")
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        image.save(image_path)
        
        try:
            # Get model path
            model_path = os.path.join(os.path.dirname(__file__), 'malaria_model', 'malaria_cnn_model.keras')
            
            # Make prediction using the ML model
            label, probability, malaria_score, no_malaria_score = predict_malaria(image_path, model_path)
            
            # Save prediction
            staff_id = session.get("user")
            staff_name = session.get("user_name")
            
            prediction = Prediction(
                patient_id=patient_id,
                staff_id=staff_id,
                staff_name=staff_name,
                result=label,
                probability=probability,
                malaria_score=malaria_score,
                no_malaria_score=no_malaria_score,
                symptoms=symptoms,
                image_path=image_path
            )
            db.session.add(prediction)
            db.session.commit()
            
            flash(f"Prediction complete: {label} ({probability:.1f}%)", "success" if label == "No Malaria" else "warning")
            
            prediction = Prediction.query.order_by(Prediction.id.desc()).first()
            patient = db.session.get(Patient, patient_id)
            
            return render_template("admin/results.html", prediction=prediction, patient=patient)
            
        except Exception as e:
            flash(f"Error making prediction: {str(e)}", "error")
            patients = Patient.query.order_by(Patient.name).all()
            return render_template("admin/predict.html", patients=patients)

    patients = Patient.query.order_by(Patient.name).all()
    return render_template("admin/predict.html", patients=patients)

@app.route("/admin/results")
@app.route("/admin/results/<int:prediction_id>")
@staff_required
def admin_results(prediction_id=None):
    """View prediction results (admin/staff only)"""
    if prediction_id:
        prediction = db.session.get(Prediction, prediction_id)
        patient = db.session.get(Patient, prediction.patient_id) if prediction else None
        return render_template("admin/results.html", prediction=prediction, patient=patient)
    
    predictions = Prediction.query.join(Patient).order_by(Prediction.created_at.desc()).all()
    return render_template("admin/results.html", predictions=predictions)

# ------------------- RUN APP -------------------

if __name__ == "__main__":
    app.run(debug=True)
