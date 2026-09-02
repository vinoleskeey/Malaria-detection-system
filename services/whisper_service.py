import logging
import tempfile
import os

from importlib import import_module

logger = logging.getLogger(__name__)


class WhisperService:
    _model = None  # loaded once, shared across requests

    def __init__(self, model_size: str = "base"):
        if WhisperService._model is None:
            logger.info("Loading Whisper model size=%s", model_size)
            try:
                whisper_model = import_module("faster_whisper").WhisperModel
            except (ImportError, AttributeError) as exc:
                logger.exception("Unable to load faster-whisper")
                raise RuntimeError(
                    "The faster-whisper package is required for transcription"
                ) from exc

            WhisperService._model = whisper_model(
                model_size, device="cpu", compute_type="int8"
            )
        self.model = WhisperService._model

    def transcribe_file_storage(self, file_storage) -> str:
        """Takes a Flask FileStorage object (from request.files), returns transcribed text."""
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
                file_storage.save(tmp.name)
                tmp_path = tmp.name

            segments, info = self.model.transcribe(tmp_path)
            text = " ".join(seg.text.strip() for seg in segments).strip()

            logger.info("Whisper transcribed lang=%s len=%s", info.language, len(text))
            return text

        except Exception:
            logger.exception("Whisper transcription failed")
            return ""

        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)