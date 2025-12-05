import os
import time
import logging
from flask import Flask, request, Response, send_from_directory, url_for
import requests
from twilio.twiml.voice_response import VoiceResponse, Gather

# ================== CONFIGURACIÓN ==================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

AUDIO_DIR = os.path.join(os.path.dirname(__file__), "static", "audio")
os.makedirs(AUDIO_DIR, exist_ok=True)

ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVEN_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "").strip()
ELEVEN_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "").strip()

logger.info(f"[BOOT] ELEVEN_API_KEY inicia: {ELEVEN_API_KEY[:6]}")
logger.info(f"[BOOT] ELEVEN_AGENT_ID: {ELEVEN_AGENT_ID}")
logger.info(f"[BOOT] ELEVEN_VOICE_ID: {ELEVEN_VOICE_ID}")

# ================== ELEVENLABS AGENT ==================

def elevenlabs_agent(text):
    """
    Envía texto al agente conversacional de ElevenLabs.
    Devuelve la respuesta en texto.
    """
    url = f"https://api.elevenlabs.io/v1/convai/chat"

    headers = {
        "xi-api-key": ELEVEN_API_KEY,
        "Content-Type": "application/json"
    }

    data = {
        "agent_id": ELEVEN_AGENT_ID,
        "text": text
    }

    try:
        resp = requests.post(url, json=data, headers=headers, timeout=20)
        resp.raise_for_status()
        result = resp.json()
        reply = result.get("reply", "Lo siento, no entendí.")
        logger.info(f"Agente ElevenLabs respondió: {reply}")
        return reply
    except Exception:
        logger.exception("Error llamando al agente ElevenLabs")
        return "Lo siento, hubo un problema procesando tu consulta."


# ================== ELEVENLABS TTS ==================

def elevenlabs_tts(text):
    """
    Convierte texto a MP3 usando ElevenLabs.
    """
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE_ID}"

    headers = {
        "xi-api-key": ELEVEN_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg"
    }

    data = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.8
        }
    }

    try:
        resp = requests.post(url, headers=headers, json=data, timeout=40)
        resp.raise_for_status()

        filename = f"tts_{int(time.time()*1000)}.mp3"
        filepath = os.path.join(AUDIO_DIR, filename)

        with open(filepath, "wb") as f:
            f.write(resp.content)

        return url_for("serve_audio", filename=filename, _external=True)

    except Exception:
        logger.exception("Error generando TTS ElevenLabs")
        return ""


# ================== RUTAS FLASK ==================

@app.route("/", methods=["GET"])
def index():
    return "ElevenLabs Conversational Agent activo", 200


@app.route("/audio/<path:filename>", methods=["GET"])
def serve_audio(filename):
    return send_from_directory(AUDIO_DIR, filename, mimetype="audio/mpeg")


@app.route("/voice", methods=["POST"])
def voice_webhook():
    vr = VoiceResponse()

    user_text = request.values.get("SpeechResult", "")
    logger.info(f"Usuario dijo: {user_text}")

    if not user_text:
        saludo = (
            "Hola, soy tu agente conversacional de Eleven Labs. "
            "Dime en qué puedo ayudarte."
        )
        audio = elevenlabs_tts(saludo)
        vr.play(audio)

        gather = Gather(
            input="speech",
            action="/voice",
            language="es-ES",
            speech_timeout="auto"
        )
        vr.append(gather)
        return Response(str(vr), mimetype="text/xml")

    # 1. AGENTE ELEVENLABS
    respuesta_texto = elevenlabs_agent(user_text)

    # 2. TTS
    audio_url = elevenlabs_tts(respuesta_texto)

    if audio_url:
        vr.play(audio_url)
    else:
        vr.say(respuesta_texto, language="es-ES")

    # 3. Continuar conversación
    gather = Gather(
        input="speech",
        action="/voice",
        language="es-ES",
        speech_timeout="auto"
    )
    vr.append(gather)

    return Response(str(vr), mimetype="text/xml")


# ================== MAIN ==================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)


