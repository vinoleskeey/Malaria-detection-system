from datetime import datetime

from models import db



class ChatSession(db.Model):
    __tablename__ = "chat_sessions"

    id = db.Column(db.Integer, primary_key=True)
    # Optional client identifier (cookie/session-based)
    session_key = db.Column(db.String(128), unique=True, index=True, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    messages = db.relationship("ChatMessage", backref="session", lazy=True, cascade="all, delete-orphan")


class ChatMessage(db.Model):
    __tablename__ = "chat_messages"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("chat_sessions.id"), nullable=False, index=True)

    role = db.Column(db.String(8), nullable=False)  # "user" | "bot"
    content = db.Column(db.Text, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


def create_or_get_session(db_session, session_key: str | None):
    """Helper used by routes to resolve current ChatSession."""
    if session_key is None:
        session_obj = ChatSession(session_key=None)
        db_session.add(session_obj)
        db_session.flush()  # obtain id
        return session_obj

    existing = ChatSession.query.filter_by(session_key=session_key).first()
    if existing:
        return existing

    session_obj = ChatSession(session_key=session_key)
    db_session.add(session_obj)
    db_session.flush()
    return session_obj

