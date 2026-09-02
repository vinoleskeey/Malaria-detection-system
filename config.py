import os


class Config:
    # Ollama
    OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma:2b")
    OLLAMA_TIMEOUT_SECONDS = int(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "180"))


    # Chat behavior.
    # NOTE: this text is appended verbatim to the chatbot's system prompt in
    # routes/chat.py as "Additional instructions". The old default here re-framed
    # the assistant as a narrow "Malaria Awareness Assistant" and forced a
    # disclaimer onto every reply, which made gemma:2b over-refuse general
    # educational questions. The safety guidance now lives in
    # _build_system_and_prompt(), so the default is empty. Set the
    # CHAT_SYSTEM_PROMPT env var only if you want to add extra instructions.
    CHAT_SYSTEM_PROMPT = os.environ.get("CHAT_SYSTEM_PROMPT", "")

