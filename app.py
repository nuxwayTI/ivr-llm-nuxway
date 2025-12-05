import os
import time
import logging
from flask import Flask, request, Response, send_from_directory, url_for
import requests
from twilio.twiml.voice_response import VoiceResponse, Gather

# ================== CONFIGURACIÓN BÁSICA ==================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Directorio donde guardaremos los audios generados por ElevenLabs
AUDIO_DIR = os.path.join(os.path.dirname(__file__), "static", "audio")
os.makedirs(AUDIO_DIR, exist_ok=True)

# --------- ENV VARS (Render / .env) ---------
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVEN_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "").strip()

logger.info(f"[BOOT] ELEVEN_API_KEY empieza con: {ELEVEN_API_KEY[:6]}")
logger.info(f"[BOOT] ELEVEN_VOICE_ID: {ELEVEN_VOICE_ID}")

if not ELEVEN_API_KEY:
    logger.warning("⚠️ Falta ELEVENLABS_API_KEY en Render")

if not ELEVEN_VOICE_ID:
    logger.warning("⚠️ Falta ELEVENLABS_VOICE_ID en Render")


# ================== FUNCIÓN ELEVENLABS TTS ==================

def elevenlabs_tts(text: str) -> str:
    """
    Convierte texto en audio MP3 usando ElevenLabs.
    Devuelve la URL pública para que Twilio la reproduzca.
    """
    logger.info(f"ELEVEN_API_KEY empieza con: {ELEVEN_API_KEY[:6]}")
    logger.info(f"ELEVEN_VOICE_ID: {ELEVEN_VOICE_ID}")

    if not ELEVEN_API_KEY or not ELEVEN_VOICE_ID:
        logger.error("❌ No hay API KEY o VOICE ID configurado.")
        return ""

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE_ID}"

    headers = {
        "xi-api-key": ELEVEN_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }

    # Si quieres, cambia el modelo según tu plan:
    # "eleven_multilingual_v2", "eleven_turbo_v2", etc.
    data = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.8,
        },
    }

    try:
        resp = requests.post(url, headers=headers, json=data, timeout=40)

        if resp.status_code != 200:
            logger.error(f"❌ Error ElevenLabs {resp.status_code}: {resp.text}")
            resp.raise_for_status()

        filename = f"tts_{int(time.time() * 1000)}.mp3"
        filepath = os.path.join(AUDIO_DIR, filename)

        with open(filepath, "wb") as f:
            f.write(resp.content)

        audio_url = url_for("serve_audio", filename=filename, _external=True)
        logger.info(f"✅ Audio generado: {audio_url}")
        return audio_url

    except Exception:
        logger.exception("❌ Error llamando a ElevenLabs")
        return ""


# ================== RUTAS FLASK ==================

@app.route("/", methods=["GET"])
def index():
    return "PBX + ElevenLabs funcionando", 200


@app.route("/audio/<path:filename>", methods=["GET"])
def serve_audio(filename):
    """Twilio descargará el MP3 desde aquí."""
    return send_from_directory(AUDIO_DIR, filename, mimetype="audio/mpeg")


@app.route("/voice", methods=["POST"])
def voice_webhook():
    vr = VoiceResponse()

    user_speech = request.values.get("SpeechResult", "")
    logger.info(f"Usuario dijo: {user_speech}")

    # -------- PRIMER TURNO: NO HAY TEXTO AÚN --------
    if not user_speech:
        # Saludo inicial con ElevenLabs
        saludo = "Hola, soy el agente de Nuxway con Eleven Labs. ¿En qué puedo ayudarte?"
        saludo_url = elevenlabs_tts(saludo)

        if saludo_url:
            vr.play(saludo_url)
        else:
            # Fallback a voz Twilio solo si falla ElevenLabs
            vr.say("Hola, soy el agente de Nuxway. ¿En qué puedo ayudarte?", language="es-ES")

        # Después del saludo, pedimos que hable
        gather = Gather(
            input="speech",
            action="/voice",
            method="POST",
            language="es-ES",
            speech_timeout="auto",
        )
        vr.append(gather)
        return Response(str(vr), mimetype="text/xml")

    # -------- SIGUIENTES TURNOS: YA HABLÓ EL USUARIO --------

    # De momento, respuesta fija de prueba
    respuesta = (
        "Hola. Esta es una prueba de Eleven Labs. "
        "Si me escuchas claramente, es que todo salió bien con la integración."
    )

    audio_url = elevenlabs_tts(respuesta)

    if audio_url:
        vr.play(audio_url)
    else:
        vr.say("Lo siento, hubo un problema generando el audio.", language="es-ES")

    # Volvemos a escuchar al usuario (mantener la conversación abierta)
    gather = Gather(
        input="speech",
        action="/voice",
        method="POST",
        language="es-ES",
        speech_timeout="auto",
    )
    vr.append(gather)

    return Response(str(vr), mimetype="text/xml")


# ================== MAIN ==================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)


