import json
import logging
import requests

from config import Config

logger = logging.getLogger(__name__)


class OllamaService:
    """
    Local Ollama service for the malaria chatbot.
    Uses the Ollama HTTP API running locally.
    """

    def __init__(self):
        self.base_url = getattr(
            Config,
            "OLLAMA_BASE_URL",
            "http://localhost:11434"
        ).rstrip("/")

        self.model = getattr(
            Config,
            "OLLAMA_MODEL",
            "gemma:2b"
        )

        self.timeout = max(
            int(getattr(Config, "OLLAMA_TIMEOUT_SECONDS", 300)),
            180
        )

    def generate(self, prompt: str) -> str:
        """
        Send a prompt to the local Ollama model and return its response (blocking).
        """
        if not prompt or not prompt.strip():
            return "Please provide a message."

        url = f"{self.base_url}/api/generate"

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": "30m"
        }

        logger.info("Sending Ollama request: model=%s url=%s", self.model, url)

        try:
            response = requests.post(url, json=payload, timeout=(10, self.timeout))
            logger.info("Ollama HTTP status: %s", response.status_code)
            response.raise_for_status()

            data = response.json()
            reply = data.get("response")

            if not isinstance(reply, str):
                logger.error("Ollama response missing 'response' field: %s", data)
                return "The AI service returned an invalid response."

            reply = reply.strip()
            if not reply:
                return "The AI service returned an empty response."

            return reply

        except requests.exceptions.Timeout:
            logger.exception("Ollama request timed out")
            return "AI request timed out. Please try again."

        except requests.exceptions.ConnectionError:
            logger.exception("Could not connect to Ollama")
            return "Could not connect to the local AI service. Please make sure Ollama is running."

        except requests.exceptions.HTTPError:
            logger.exception("Ollama returned an HTTP error")
            return "The local AI service returned an error."

        except requests.exceptions.RequestException:
            logger.exception("Ollama request failed")
            return "The AI service request failed."

        except ValueError:
            logger.exception("Ollama returned invalid JSON")
            return "The AI service returned an invalid response."

        except Exception:
            logger.exception("Unexpected Ollama error")
            return "An unexpected AI service error occurred."

    def generate_stream(self, prompt: str, model: str | None = None):
        """
        Streams tokens from Ollama's /api/generate endpoint.
        Yields decoded text chunks as they arrive. Never raises —
        yields a user-friendly error string chunk instead.
        """
        url = f"{self.base_url}/api/generate"
        selected_model = model or self.model or "gemma:2b"

        payload = {
            "model": selected_model,
            "prompt": prompt,
            "stream": True,
            "keep_alive": "30m",
        }

        try:
            with requests.post(url, json=payload, timeout=(10, self.timeout), stream=True) as resp:
                if not resp.ok:
                    yield "Sorry—local AI service is temporarily unavailable."
                    return

                for line in resp.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except ValueError:
                        continue

                    chunk = data.get("response", "")
                    if chunk:
                        yield chunk

                    if data.get("done"):
                        break

        except requests.exceptions.Timeout:
            logger.exception("Ollama stream timeout model=%s url=%s", selected_model, url)
            yield "Sorry—the AI generation timed out."
        except requests.exceptions.ConnectionError:
            logger.exception("Ollama stream connection failure model=%s url=%s", selected_model, url)
            yield "Sorry—local AI service is offline right now."
        except Exception:
            logger.exception("Unexpected error in generate_stream model=%s", selected_model)
            yield "Sorry—something went wrong."