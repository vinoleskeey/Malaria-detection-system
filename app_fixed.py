from flask import Flask, render_template, request, redirect, session, flash, url_for, send_from_directory, Response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from functools import wraps
import os
import secrets
import hashlib
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from models import db
from pdf_utils import generate_prediction_pdf
from datetime import datetime, timedelta
import sys
import importlib.util

# Ollama chat blueprint (POST /chat, POST /chat/stream, GET /history, POST /clear).
# Imported at module level (mirrors app.py) so it registers below, after
# db.init_app(app) and the default-admin bootstrap.
from routes.chat import chat_bp

# Load environment variables from .env for local development (mirrors app.py).
# In production (Railway) real env vars are already set, so this is a no-op there.
if importlib.util.find_spec('dotenv') is not None:
    dotenv = importlib.import_module('dotenv')
    dotenv.load_dotenv()

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

# Railway terminates TLS at a proxy and forwards the original scheme/host in
# X-Forwarded-* headers. Trust one proxy hop so url_for(..., _external=True)
# builds correct https:// links (used in the password-reset email). No-op locally.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
if os.environ.get('RAILWAY_ENVIRONMENT') or os.environ.get('RAILWAY_PUBLIC_DOMAIN'):
    app.config['PREFERRED_URL_SCHEME'] = 'https'
# Use DATABASE_URL from Railway environment, fallback to SQLite for local
database_url = os.environ.get('DATABASE_URL')
if database_url:
    # Railway provides postgres:// but SQLAlchemy needs postgresql://
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    print(f"[DEBUG] Using Railway PostgreSQL database")
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
    print(f"[DEBUG] Using local SQLite database")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

DEVELOPER_EMAIL = 'victorshittu17@gmail.com'

# Email / SMTP configuration - read entirely from the environment, no secrets in
# source. Set SMTP_USERNAME and SMTP_PASSWORD (Railway dashboard / local .env).
# MAIL_USERNAME / MAIL_PASSWORD are accepted as fallbacks for backward compat.
SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USERNAME = os.environ.get('SMTP_USERNAME') or os.environ.get('MAIL_USERNAME', '')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD') or os.environ.get('MAIL_PASSWORD', '')
SMTP_FROM_EMAIL = os.environ.get('SMTP_FROM_EMAIL') or SMTP_USERNAME

print(f"[DEBUG] SMTP_USERNAME set: {bool(SMTP_USERNAME)}")
print(f"[DEBUG] SMTP_PASSWORD set: {bool(SMTP_PASSWORD)}")

# ==================== SQLALCHEMY SETUP ====================
# `db` is the shared SQLAlchemy() instance from models/__init__.py, not a private
# one - routes/chat.py's ChatMessage/ChatSession models are bound to that same
# instance, so this app must call init_app() on it rather than constructing its
# own SQLAlchemy(app) (which would leave chat's tables never created here).
db.init_app(app)

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

class Appointment(db.Model):
    __tablename__ = 'appointments'

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    preferred_date = db.Column(db.Date, nullable=False)
    preferred_time = db.Column(db.String(20), nullable=False)  # Morning/Afternoon/Evening
    reason = db.Column(db.String(200), default="Malaria test")
    notes = db.Column(db.Text)
    status = db.Column(db.String(20), default="pending")  # pending/confirmed/cancelled/completed
    reminder_sent = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('Patient', backref='appointments')
    booked_by = db.relationship('User', backref='booked_appointments')

# Create all tables and default admin account
with app.app_context():
    db.create_all()
    
    # Add image_path column if it doesn't exist (migration) - PostgreSQL compatible
    try:
        from sqlalchemy import text
        # Check if column exists using PostgreSQL-compatible query
        try:
            result = db.session.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='predictions' AND column_name='image_path'"))
            if result.fetchone() is None:
                db.session.execute(text("ALTER TABLE predictions ADD COLUMN image_path VARCHAR(200)"))
                db.session.commit()
                print("Added image_path column to predictions table")
        except:
            # For SQLite (local dev)
            result = db.session.execute(text("PRAGMA table_info(predictions)"))
            columns = [row[1] for row in result]
            if 'image_path' not in columns:
                db.session.execute(text("ALTER TABLE predictions ADD COLUMN image_path VARCHAR(200)"))
                db.session.commit()
                print("Added image_path column to predictions table")
    except Exception as e:
        print(f"Migration check: {e}")

    # Add reminder_sent column if it doesn't exist (migration) - PostgreSQL compatible
    try:
        from sqlalchemy import text
        try:
            result = db.session.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='appointments' AND column_name='reminder_sent'"))
            if result.fetchone() is None:
                db.session.execute(text("ALTER TABLE appointments ADD COLUMN reminder_sent BOOLEAN DEFAULT FALSE"))
                db.session.commit()
                print("Added reminder_sent column to appointments table")
        except:
            # For SQLite (local dev)
            result = db.session.execute(text("PRAGMA table_info(appointments)"))
            columns = [row[1] for row in result]
            if 'reminder_sent' not in columns:
                db.session.execute(text("ALTER TABLE appointments ADD COLUMN reminder_sent BOOLEAN DEFAULT 0"))
                db.session.commit()
                print("Added reminder_sent column to appointments table")
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

# Register Ollama chat blueprint (POST /chat, POST /chat/stream, GET /history, POST /clear)
app.register_blueprint(chat_bp)

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

# ==================== TEMPLATE CONTEXT ====================

@app.context_processor
def inject_pending_appointments():
    """Expose the number of pending appointment requests to templates.

    Only runs the query for staff/admin sessions (and not while an admin is
    viewing as a patient). Recomputed on every request, so the badge/count
    never goes stale after a status change.
    """
    role = session.get("role")
    if role in ("staff", "admin") and not session.get("viewing_as_patient"):
        try:
            count = Appointment.query.filter_by(status="pending").count()
        except Exception:
            count = 0
        return {"pending_appointments_count": count}
    return {"pending_appointments_count": 0}

# ==================== EMAIL HELPERS ====================

def send_password_reset_email(email, reset_link):
    """Send password reset email to user.

    `reset_link` must be a fully-qualified URL - the route builds it with
    url_for("reset_password", token=token, _external=True) so it uses the real
    deployed host and (via ProxyFix) the https scheme.
    """
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        print("[EMAIL ERROR] SMTP_USERNAME / SMTP_PASSWORD not set in environment")
        return False
    try:
        print(f"[EMAIL] Starting password reset email for: {email}")
        print(f"[EMAIL] Reset link: {reset_link}")

        # Create email message
        msg = MIMEMultipart()
        msg['From'] = SMTP_FROM_EMAIL
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

        # Connect to SMTP server
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)

        # Send email
        server.sendmail(SMTP_FROM_EMAIL, email, msg.as_string())
        server.quit()

        return True
    except Exception as e:
        import traceback
        print(f"[EMAIL ERROR] Failed to send email: {e}")
        print(f"[EMAIL ERROR] Traceback: {traceback.format_exc()}")
        return False

def send_prediction_result_email(email, patient_name, prediction, pdf_bytes):
    """Email a patient their diagnostic report PDF as soon as a prediction is saved.

    Mirrors send_password_reset_email above (same SMTP_* config, same
    fail-soft-and-log behavior) but attaches the generated PDF instead of
    linking out. Never raises - returns False and logs on any failure, since
    a failed notification email must not break the prediction flow itself.
    """
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        print("[EMAIL ERROR] SMTP_USERNAME / SMTP_PASSWORD not set in environment")
        return False
    try:
        is_malaria = (prediction.result == "Malaria Detected")
        result_color = "#c62828" if is_malaria else "#2e7d32"

        msg = MIMEMultipart()
        msg['From'] = SMTP_FROM_EMAIL
        msg['To'] = email
        msg['Subject'] = f"Your Malaria Test Result - {prediction.result}"

        body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <div style="max-width: 600px; margin: 0 auto; background: #f5f5f5; padding: 30px; border-radius: 10px;">
                <h2 style="color: #1a237e;">Your Test Result Is Ready</h2>
                <p>Hello {patient_name},</p>
                <p>Your malaria blood smear test has been analyzed. Result:</p>
                <p style="font-size: 20px; font-weight: bold; color: {result_color};">{prediction.result}</p>
                <p>Confidence: {prediction.probability:.1f}%</p>
                <p>The full diagnostic report is attached to this email as a PDF.</p>
                <p style="color: #666; font-size: 14px;">This is an AI-assisted screening result. Please consult a licensed medical professional to confirm this result and discuss next steps.</p>
            </div>
        </body>
        </html>
        """
        msg.attach(MIMEText(body, 'html'))

        attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
        attachment.add_header(
            'Content-Disposition', 'attachment',
            filename=f"malaria-report-{prediction.id}.pdf"
        )
        msg.attach(attachment)

        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM_EMAIL, email, msg.as_string())
        server.quit()

        return True
    except Exception as e:
        import traceback
        print(f"[EMAIL ERROR] Failed to send prediction result email: {e}")
        print(f"[EMAIL ERROR] Traceback: {traceback.format_exc()}")
        return False

def send_appointment_reminder_email(email, patient_name, appointment):
    """Email a patient a reminder ~24h before their booked appointment.

    Same fail-soft SMTP pattern as the other email helpers above - a failed
    reminder must never crash the background scheduler job.
    """
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        print("[EMAIL ERROR] SMTP_USERNAME / SMTP_PASSWORD not set in environment")
        return False
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_FROM_EMAIL
        msg['To'] = email
        msg['Subject'] = "Reminder: Your Malaria Test Appointment Is Tomorrow"

        body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <div style="max-width: 600px; margin: 0 auto; background: #f5f5f5; padding: 30px; border-radius: 10px;">
                <h2 style="color: #1a237e;">Appointment Reminder</h2>
                <p>Hello {patient_name},</p>
                <p>This is a reminder that you have a malaria test appointment scheduled for:</p>
                <p style="font-size: 18px; font-weight: bold; color: #1a237e;">
                    {appointment.preferred_date.strftime('%A, %B %d, %Y')} ({appointment.preferred_time})
                </p>
                <p><strong>Reason:</strong> {appointment.reason or 'Malaria test'}</p>
                <p style="color: #666; font-size: 14px;">If you need to reschedule or cancel, please contact the hospital as soon as possible.</p>
            </div>
        </body>
        </html>
        """
        msg.attach(MIMEText(body, 'html'))

        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM_EMAIL, email, msg.as_string())
        server.quit()

        return True
    except Exception as e:
        import traceback
        print(f"[EMAIL ERROR] Failed to send appointment reminder email: {e}")
        print(f"[EMAIL ERROR] Traceback: {traceback.format_exc()}")
        return False

def send_appointment_reminders():
    """Background job: email every patient whose confirmed/pending appointment
    is tomorrow and hasn't been reminded yet. Runs inside its own app context
    since APScheduler calls this outside of any Flask request.
    """
    with app.app_context():
        tomorrow = (datetime.now() + timedelta(days=1)).date()
        due = Appointment.query.filter(
            Appointment.preferred_date == tomorrow,
            Appointment.status.in_(["pending", "confirmed"]),
            db.or_(Appointment.reminder_sent.is_(False), Appointment.reminder_sent.is_(None)),
        ).all()

        for appointment in due:
            user = db.session.get(User, appointment.user_id)
            if not user or not user.email:
                continue
            sent = send_appointment_reminder_email(user.email, user.name, appointment)
            if sent:
                appointment.reminder_sent = True
                db.session.commit()

def start_appointment_reminder_scheduler():
    """Start the APScheduler background thread that checks for due reminders.

    Guarded so it starts exactly once per running process: under gunicorn
    (single worker, no reloader) app.debug is False so it always starts here;
    under the Werkzeug debug reloader, only the real child process (which
    sets WERKZEUG_RUN_MAIN) starts it, so the reloader's parent watcher
    process doesn't spin up a duplicate scheduler.
    """
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(send_appointment_reminders, "interval", minutes=30, next_run_time=datetime.now())
    scheduler.start()

start_appointment_reminder_scheduler()

# ==================== ROUTES ====================

@app.route("/")
def index():
    return redirect("/login")

@app.route("/chatbot", methods=["GET"])
@login_required
def chatbot():
    # Patient portal chatbot (education only)
    # Ensure staff/admin cannot open it via URL
    if session.get("role") not in ["patient"] and not session.get("viewing_as_patient"):
        flash("Access denied", "error")
        return redirect("/dashboard")

    return render_template("patient/chatbot.html")

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

    pending_appointments_count = Appointment.query.filter_by(status="pending").count()

    return render_template("admin/dashboard.html",
                           total_patients=total_patients,
                           total_predictions=total_predictions,
                           high_risk=high_risk,
                           low_risk=low_risk,
                           recent_predictions=recent_predictions,
                           metrics={"accuracy": 96.5, "precision": 95.8, "recall": 97.2, "f1_score": 96.5},
                           all_patients=all_patients,
                           all_staff=all_staff,
                           pending_appointments_count=pending_appointments_count,
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

# ------------------- APPOINTMENTS (Patient) -------------------

@app.route("/book-appointment", methods=["GET", "POST"])
@login_required
def book_appointment():
    user_id = session.get("user")
    role = session.get("role")

    if role != "patient" and not session.get("viewing_as_patient"):
        flash("Only patients can book appointments", "error")
        return redirect("/dashboard")

    patient = Patient.query.filter_by(user_id=user_id).first()
    default_name = (session.get("user_name") or "").strip()

    def render_form():
        # Self-registered patients have no Patient row yet; the template shows
        # extra name/age/gender fields when has_patient_profile is False.
        return render_template(
            "patient/book_appointment.html",
            has_patient_profile=patient is not None,
            default_name=default_name,
        )

    if request.method == "POST":
        date_str = request.form.get("preferred_date", "")
        time_slot = request.form.get("preferred_time", "")
        reason = request.form.get("reason", "Malaria test").strip() or "Malaria test"
        notes = request.form.get("notes", "").strip()

        try:
            preferred_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Please choose a valid date.", "error")
            return render_form()

        if preferred_date < datetime.now().date():
            flash("Please choose a date in the future.", "error")
            return render_form()

        if time_slot not in ("Morning", "Afternoon", "Evening"):
            flash("Please choose a valid time of day.", "error")
            return render_form()

        # No linked Patient profile yet (e.g. self-registered via /register) -
        # collect basic info inline and create the Patient row first.
        if not patient:
            new_name = (request.form.get("patient_name", "") or "").strip() or default_name
            age_raw = (request.form.get("patient_age", "") or "").strip()
            gender = (request.form.get("patient_gender", "") or "").strip()

            if not new_name:
                flash("Please enter your name to set up your patient profile.", "error")
                return render_form()

            age = None
            if age_raw:
                try:
                    age = int(age_raw)
                except ValueError:
                    flash("Age must be a whole number.", "error")
                    return render_form()
                if age <= 0:
                    flash("Age must be a positive number.", "error")
                    return render_form()

            patient = Patient(user_id=user_id, name=new_name, age=age, gender=gender or None)
            db.session.add(patient)
            db.session.commit()

        appointment = Appointment(
            patient_id=patient.id,
            user_id=user_id,
            preferred_date=preferred_date,
            preferred_time=time_slot,
            reason=reason,
            notes=notes,
        )
        db.session.add(appointment)
        db.session.commit()

        flash("Appointment request submitted! We'll confirm it shortly.", "success")
        return redirect("/my-appointments")

    return render_form()

@app.route("/my-appointments")
@login_required
def my_appointments():
    user_id = session.get("user")
    patient = Patient.query.filter_by(user_id=user_id).first()

    appointments = (
        Appointment.query.filter_by(patient_id=patient.id)
        .order_by(Appointment.preferred_date.desc())
        .all()
        if patient
        else []
    )

    return render_template("patient/my_appointments.html", appointments=appointments)

# ------------------- APPOINTMENTS (Staff/Admin) -------------------

@app.route("/admin/appointments")
@staff_required
def admin_appointments():
    status_filter = request.args.get("status")

    query = Appointment.query.join(Patient)
    if status_filter:
        query = query.filter(Appointment.status == status_filter)

    appointments = query.order_by(Appointment.preferred_date.asc()).all()
    return render_template("admin/appointments.html", appointments=appointments, status_filter=status_filter)

@app.route("/admin/appointments/<int:appointment_id>/update", methods=["POST"])
@staff_required
def admin_update_appointment(appointment_id):
    appointment = db.session.get(Appointment, appointment_id)
    new_status = request.form.get("status")

    if appointment and new_status in ("pending", "confirmed", "cancelled", "completed"):
        appointment.status = new_status
        db.session.commit()
        flash(f"Appointment status updated to {new_status}.", "success")
    else:
        flash("Could not update appointment.", "error")

    return redirect("/admin/appointments")

# ------------------- ADMIN LOGIN -------------------

@app.route("/admin/register", methods=["GET", "POST"])
def admin_register():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"].lower().strip()
        password = request.form["password"]
        confirm = request.form.get("confirm_password", "")

        if password != confirm:
            flash("Passwords do not match!", "error")
            return render_template("admin/register.html")

        if len(password) < 6:
            flash("Password must be at least 6 characters", "error")
            return render_template("admin/register.html")

        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash("Email already registered!", "error")
            return render_template("admin/register.html")

        user = User(name=name, email=email, password=generate_password_hash(password), role="admin")
        db.session.add(user)
        db.session.commit()

        flash("Admin account created! Please login.", "success")
        return redirect("/admin/login")

    return render_template("admin/register.html")

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
            token = hashlib.sha256(f"{email}{datetime.utcnow()}".encode()).hexdigest()[:32]

            # Delete any existing tokens for this user
            PasswordReset.query.filter_by(user_id=user.id).delete()

            # Insert new token (expires in 24 hours, UTC)
            expires = datetime.utcnow() + timedelta(hours=24)
            reset = PasswordReset(user_id=user.id, token=token, expires=expires)
            db.session.add(reset)
            db.session.commit()

            # Build a fully-qualified reset URL from the current request
            # (ProxyFix -> correct https:// + host on Railway).
            reset_link = url_for("reset_password", token=token, _external=True)

            # Try to send the email
            email_sent = send_password_reset_email(email, reset_link)
            
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

    if not reset or reset.expires < datetime.utcnow():
        flash("Invalid or expired reset link!", "error")
        return redirect("/forgot_password")

    if request.method == "POST":
        password = request.form.get("password") or request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        
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

        flash("Patient added successfully!", "success")
        return redirect("/admin/patients")

    return render_template("admin/add_patients.html")

@app.route("/admin/staff")
@admin_required
def admin_list_staff():
    """List all staff/admin accounts (admin only)"""
    staff = User.query.filter(User.role.in_(['staff', 'admin'])).order_by(User.created_at.desc()).all()
    return render_template("admin/list_staff.html", staff=staff)

@app.route("/admin/staff/add", methods=["GET", "POST"])
@admin_required
def admin_add_staff():
    """Add a new staff/admin account (admin only)"""
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"].lower().strip()
        password = request.form["password"]
        department = request.form.get("department", "")
        position = request.form.get("position", "")
        employee_id = request.form.get("employee_id", "")

        pw_hash = generate_password_hash(password)

        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash("Email already registered!", "error")
            return render_template("admin/add_staff.html")

        user = User(name=name, email=email, password=pw_hash, role="staff")
        db.session.add(user)
        db.session.commit()

        staff_profile = StaffProfile(user_id=user.id, department=department, position=position, employee_id=employee_id)
        db.session.add(staff_profile)
        db.session.commit()

        flash("Staff added successfully!", "success")
        return redirect("/admin/staff")

    return render_template("admin/add_staff.html")

@app.route("/admin/predict", methods=["GET", "POST"])
@staff_required
def admin_predict():
    """Run malaria detection on an uploaded cell image (admin/staff only)"""
    if request.method == "POST":
        patient_id = request.form.get("patient_id")
        symptoms = request.form.get("symptoms", "")

        image = request.files.get('file')

        if not image:
            flash("Please upload a cell image for analysis!", "error")
            patients = Patient.query.order_by(Patient.name).all()
            return render_template("admin/predict.html", patients=patients)

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

            prediction_row = Prediction.query.order_by(Prediction.id.desc()).first()
            patient = db.session.get(Patient, patient_id)

            # Email the patient their PDF report. Best-effort: a failed/slow
            # email must never block staff from seeing the result they just ran.
            if patient and patient.user and patient.user.email:
                try:
                    pdf_bytes = generate_prediction_pdf(prediction_row, patient)
                    send_prediction_result_email(patient.user.email, patient.name, prediction_row, pdf_bytes)
                except Exception as e:
                    print(f"[EMAIL ERROR] Could not generate/send prediction report: {e}")

            return render_template("admin/results.html", prediction=prediction_row, patient=patient)

        except Exception as e:
            flash(f"Error making prediction: {str(e)}", "error")
            patients = Patient.query.order_by(Patient.name).all()
            return render_template("admin/predict.html", patients=patients)

    patients = Patient.query.order_by(Patient.name).all()
    return render_template("admin/predict.html", patients=patients)

@app.route("/uploads/<path:filename>")
@staff_required
def uploaded_file(filename):
    """Serve an uploaded cell image (admin/staff only - these are patient medical images)"""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

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

@app.route("/admin/results/<int:prediction_id>/pdf")
@staff_required
def admin_result_pdf(prediction_id):
    """Download a prediction's diagnostic report as a PDF (admin/staff only)"""
    prediction = db.session.get(Prediction, prediction_id)
    if not prediction:
        flash("Prediction not found", "error")
        return redirect("/admin/results")
    patient = db.session.get(Patient, prediction.patient_id)
    pdf_bytes = generate_prediction_pdf(prediction, patient)
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=malaria-report-{prediction.id}.pdf"},
    )

@app.route("/my-results/<int:prediction_id>/pdf")
@login_required
def my_result_pdf(prediction_id):
    """Let a patient download the PDF report for one of their own predictions"""
    prediction = db.session.get(Prediction, prediction_id)
    if not prediction:
        flash("Prediction not found", "error")
        return redirect("/my-results")

    patient = db.session.get(Patient, prediction.patient_id)
    owner_user_id = patient.user_id if patient else None
    if session.get("role") not in ["staff", "admin"] and owner_user_id != session.get("user"):
        flash("Access denied", "error")
        return redirect("/my-results")

    pdf_bytes = generate_prediction_pdf(prediction, patient)
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=malaria-report-{prediction.id}.pdf"},
    )

# ------------------- RUN APP -------------------

if __name__ == "__main__":
    app.run(debug=True)
