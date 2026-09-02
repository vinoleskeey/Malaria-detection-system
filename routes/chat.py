import importlib
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request, session as flask_session, Response, stream_with_context
import logging
import traceback
from services.whisper_service import WhisperService
from config import Config
from models.chat_models import ChatMessage, ChatSession, create_or_get_session
from models import db
from services.ollama_services import OllamaService

# Ensure models are bound to the SAME SQLAlchemy instance that app.py initialized.
try:
    _ = db.engine
except Exception:
    pass


logger = logging.getLogger(__name__)

chat_bp = Blueprint("chat", __name__)

MALARIA_KEYWORDS = (
    "malaria", "mosquito", "fever", "chills", "parasite", "plasmodium",
    "antimalarial", "quinine", "artemisinin",
)

# Direct, high-confidence booking phrases. A substring hit here means the user is
# clearly asking to book — no LLM inference needed.
BOOKING_KEYWORDS = (
    "book an appointment", "book appointment", "book a appointment",
    "make an appointment", "make appointment",
    "schedule an appointment", "schedule appointment",
    "set up an appointment", "request an appointment", "reserve an appointment",
    "book a test", "book a malaria test", "book a blood test",
    "schedule a test", "schedule a malaria test", "schedule a blood test",
    "book a consultation", "schedule a consultation",
    "book a checkup", "book a check-up", "schedule a checkup",
    "book a slot", "book a session",
    "i want to book", "i'd like to book", "i would like to book",
    "can i book", "how do i book", "how can i book", "i need to book",
    "want to schedule", "like to schedule", "need to schedule",
)

# Softer, ambiguous signals. A hit here is NOT enough on its own — it triggers a
# single strict YES/NO classification call to the local model.
BOOKING_AMBIGUOUS_HINTS = (
    "appointment", "book", "booking", "schedule", "scheduling",
    "see a doctor", "see the doctor", "get tested", "get a test",
    "consultation", "come in", "visit the clinic", "clinic visit",
)

# Canned reply for a detected booking intent. Kept to a single line (no newlines)
# so it survives being emitted as one SSE "data:" chunk in /chat/stream.
BOOKING_REPLY = (
    "It sounds like you'd like to book an appointment or a malaria test. I can't "
    "schedule it from the chat, but you can request a slot on the booking form at "
    "/book-appointment — pick a date and a time of day and our staff will confirm "
    "it. You can review your requests any time at /my-appointments. Meanwhile I'm "
    "happy to answer questions about malaria symptoms, prevention, or what to "
    "expect from testing."
)

DISCLAIMER = "This chatbot provides educational information only and is not a medical diagnosis system."


def _get_or_create_session_key() -> str:
    key = flask_session.get("chat_session_key")
    if key:
        return key

    key = uuid.uuid4().hex
    flask_session["chat_session_key"] = key
    return key


def _build_system_and_prompt(user_message: str) -> str:
    system_prompt = (
        "You are a helpful, friendly, knowledgeable general-purpose AI assistant. You answer "
        "questions on ANY topic — history, science, technology, culture, everyday life, coding, "
        "writing, and casual conversation — using your own general knowledge. Answer directly "
        "and informatively. Do not refer to \"the provided context\" or claim you lack context; "
        "if you genuinely don't know something, say so plainly instead.\n"
        "\n"
        "HEALTH AND MEDICINE ARE NORMAL TOPICS. General or educational questions about any "
        "disease, condition, medication, or medical concept are ordinary general knowledge — "
        "treat them exactly like a history or science question and answer them fully and "
        "confidently. This includes questions such as \"What is HIV?\", \"How does malaria "
        "spread?\", \"What are the stages of diabetes?\", \"What causes sickle cell anaemia?\", "
        "\"How do vaccines work?\", or \"What are the common symptoms of typhoid?\". Freely "
        "explain what a condition is, how it spreads, its typical signs and symptoms, how it is "
        "generally diagnosed and treated in broad terms, prevention, history, and statistics. "
        "NEVER refuse or deflect these questions. Someone asking what a disease is has NOT "
        "asked you to diagnose them, and a message is not a medical consultation just because "
        "it contains the name of a disease.\n"
        "\n"
        "Be more cautious ONLY in these two specific situations:\n"
        "1. The user describes THEIR OWN symptoms and is essentially asking whether they "
        "personally have a particular condition (e.g. \"I've had a fever and chills for three "
        "days, do I have malaria?\"). In that case: explain what such symptoms can be "
        "associated with, but do NOT state as fact that they do or do not have any condition — "
        "use wording like \"may indicate\" or \"possible causes include\" — and recommend they "
        "get proper testing and consult a qualified healthcare professional.\n"
        "2. The user asks what specific medication or dose THEY should take for their own "
        "situation. In that case: describe general treatment approaches and categories of "
        "treatment, but do NOT give specific drug names, brands, or dosages, and tell them to "
        "consult a doctor or pharmacist.\n"
        "\n"
        "In those two cases, and any time severe or emergency symptoms are described (trouble "
        "breathing, confusion, chest pain, inability to drink or keep fluids down, seizures, "
        "loss of consciousness), also advise seeking urgent in-person medical care immediately.\n"
        "\n"
        "Do not let this caution leak into general educational answers. If the message is not "
        "one person asking about their own diagnosis or their own medication, just answer it "
        "normally and helpfully, like any other question."
    )

    if getattr(Config, "CHAT_SYSTEM_PROMPT", None):
        system_prompt = f"{system_prompt}\n\nAdditional instructions:\n{Config.CHAT_SYSTEM_PROMPT}"

    return f"System: {system_prompt}\n\nUser: {user_message}\n\nAssistant:"


def _maybe_append_disclaimer(bot_text: str) -> str:
    """Only attach the medical disclaimer when the reply actually touches health/malaria topics."""
    lowered = bot_text.lower()
    if any(kw in lowered for kw in MALARIA_KEYWORDS) and DISCLAIMER not in bot_text:
        return f"{bot_text.strip()}\n\n{DISCLAIMER}"
    return bot_text


def _detect_booking_intent(user_message: str) -> bool:
    """
    Hybrid booking-intent detection.

    1. Fast substring match against BOOKING_KEYWORDS — zero inference cost.
    2. If only a softer BOOKING_AMBIGUOUS_HINTS signal is present, make ONE short
       strict YES/NO classification call to the local model.
    3. Otherwise (or on any error) return False so the normal chat flow runs.
    """
    if not user_message or not user_message.strip():
        return False

    lowered = user_message.lower()

    # 1. Direct, high-confidence phrases.
    if any(kw in lowered for kw in BOOKING_KEYWORDS):
        return True

    # 2. Ambiguous signal only — fall back to a single classification call.
    if any(hint in lowered for hint in BOOKING_AMBIGUOUS_HINTS):
        try:
            ollama = OllamaService()
            classification_prompt = (
                "You are an intent classifier for a malaria clinic chatbot. "
                "Decide whether the user's message is a request to book, schedule, "
                "arrange, or set up a medical appointment, test, or consultation.\n"
                "Reply with exactly one word: YES or NO. No explanation.\n\n"
                f"User message: \"{user_message.strip()}\"\n\n"
                "Answer:"
            )
            result = ollama.generate(classification_prompt) or ""
            # Look only at the leading token so stray markdown/punctuation
            # (e.g. "**YES**", "Yes.") still classifies correctly.
            leading = result.strip().upper().lstrip("*_`\"' ")[:4]
            return leading.startswith("YES")
        except Exception:
            logger.exception("_detect_booking_intent classification call failed")
            return False

    # 3. No booking signal.
    return False


def _save_bot_message(session_id, bot_text: str, req_id: str = "") -> None:
    """Persist a bot reply to ChatMessage history (shared by /chat and /chat/stream)."""
    try:
        db.session.add(ChatMessage(
            session_id=session_id,
            role="bot",
            content=bot_text,
            created_at=datetime.utcnow(),
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("chat DB commit failed for bot message request_id=%s", req_id)


@chat_bp.route("/chat", methods=["POST"])
def chat():
    req_id = uuid.uuid4().hex

    try:
        logger.info("/chat received request_id=%s", req_id)

        print("\n========== CHAT REQUEST ==========")
        print(request.get_json(silent=True))
        print("==================================")

        if not request.is_json:
            return jsonify({"reply": "Invalid request. Expected JSON with {\"message\": ...}."}), 400

        body = request.get_json(silent=True) or {}
        message = body.get("message", "")

        logger.info(
            "/chat request_id=%s json_keys=%s message_len=%s",
            req_id,
            list(body.keys()) if isinstance(body, dict) else None,
            len(message) if isinstance(message, str) else None,
        )

        if not isinstance(message, str) or not message.strip():
            return jsonify({"reply": "Please type a message."}), 400

        user_message = message.strip()
        print("User message:", user_message)

        session_key = _get_or_create_session_key()
        session_obj = create_or_get_session(db.session, session_key)

        try:
            user_row = ChatMessage(session_id=session_obj.id, role="user", content=user_message)
            db.session.add(user_row)
            db.session.commit()
        except Exception:
            logger.exception("/chat DB commit failed for user message request_id=%s", req_id)

        # Booking-intent short-circuit: skip the LLM entirely and point the user
        # at the real booking form. Still saved to history like any other reply.
        if _detect_booking_intent(user_message):
            logger.info("/chat booking intent detected request_id=%s", req_id)
            _save_bot_message(session_obj.id, BOOKING_REPLY, req_id)
            return jsonify({"reply": BOOKING_REPLY})

        prompt = _build_system_and_prompt(user_message)

        ollama = OllamaService()
        payload = {
            "model": getattr(ollama, "model", None) or "gemma:2b",
            "prompt": prompt,
            "stream": False,
            "keep_alive": "30m",
        }
        print("Sending request to Ollama...")
        print(payload)

        bot_text = ollama.generate(prompt)
        logger.info("/chat ollama generate complete request_id=%s reply_len=%s", req_id, len(bot_text or ""))

        print("Generated reply:")
        print(bot_text)

        bot_text = _maybe_append_disclaimer(bot_text)

        _save_bot_message(session_obj.id, bot_text, req_id)

        return jsonify({"reply": bot_text})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"reply": f"Server Error: {str(e)}"}), 500


@chat_bp.route("/chat/stream", methods=["POST"])
def chat_stream():
    body = request.get_json(silent=True) or {}
    user_message = (body.get("message") or "").strip()

    if not user_message:
        return jsonify({"reply": "Please type a message."}), 400

    session_key = _get_or_create_session_key()
    session_obj = create_or_get_session(db.session, session_key)
    # Capture the plain id now, while session_obj is still attached to the
    # session. The generators below run lazily, driven by the WSGI server
    # after this request-handling frame returns - by the time they resume,
    # session_obj can be detached (SQLAlchemy DetachedInstanceError on
    # accessing session_obj.id), which silently kills the generator mid-
    # stream and drops the "event: done" terminator. A plain int has no such
    # lifecycle issue.
    session_id = session_obj.id

    try:
        db.session.add(ChatMessage(session_id=session_id, role="user", content=user_message))
        db.session.commit()
    except Exception:
        logger.exception("chat_stream DB commit failed for user message")

    # Booking-intent short-circuit: don't call the model, emit the canned reply
    # as a single SSE chunk followed by the usual done terminator.
    if _detect_booking_intent(user_message):
        logger.info("chat_stream booking intent detected")

        def booking_generate():
            yield f"data: {BOOKING_REPLY}\n\n"
            _save_bot_message(session_id, BOOKING_REPLY)
            yield "event: done\ndata: [DONE]\n\n"

        return Response(stream_with_context(booking_generate()), mimetype="text/event-stream")

    prompt = _build_system_and_prompt(user_message)
    ollama = OllamaService()

    def generate():
        full_reply = []
        for chunk in ollama.generate_stream(prompt):
            full_reply.append(chunk)
            yield f"data: {chunk}\n\n"

        bot_text = "".join(full_reply).strip()
        bot_text = _maybe_append_disclaimer(bot_text)

        _save_bot_message(session_id, bot_text)

        yield "event: done\ndata: [DONE]\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@chat_bp.route("/history", methods=["GET"])
def history():
    session_key = flask_session.get("chat_session_key")

    if not session_key:
        return jsonify({"messages": []})

    session_obj = ChatSession.query.filter_by(session_key=session_key).first()
    if not session_obj:
        return jsonify({"messages": []})

    rows = (
        ChatMessage.query.filter_by(session_id=session_obj.id)
        .order_by(ChatMessage.created_at.asc())
        .limit(200)
        .all()
    )

    messages = [
        {"role": r.role, "text": r.content, "time": r.created_at.strftime("%H:%M")}
        for r in rows
    ]
    return jsonify({"messages": messages})


@chat_bp.route("/clear", methods=["POST"])
def clear():
    session_key = flask_session.get("chat_session_key")

    if not session_key:
        return jsonify({"ok": True})

    session_obj = ChatSession.query.filter_by(session_key=session_key).first()
    if session_obj:
        db.session.delete(session_obj)
        db.session.commit()

    flask_session.pop("chat_session_key", None)

    return jsonify({"ok": True})


@chat_bp.route("/voice/transcribe", methods=["POST"])
def voice_transcribe():
    if "audio" not in request.files:
        return jsonify({"error": "No audio file provided"}), 400

    audio_file = request.files["audio"]

    try:
        whisper = WhisperService()
        text = whisper.transcribe_file_storage(audio_file)

        if not text:
            return jsonify({"error": "Could not transcribe audio. Please try again."}), 500

        return jsonify({"text": text})

    except Exception:
        logger.exception("/voice/transcribe failed")
        return jsonify({"error": "Transcription failed"}), 500