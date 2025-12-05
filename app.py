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

AUDIO_DIR = os.path.join(os.path.dirname(__file__), "static", "audio")
os.makedirs(AUDIO_DIR, exist_ok=True)

ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVEN_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "").strip()

logger.info(f"[BOOT] ELEVEN_API_KEY inicia: {ELEVEN_API_KEY[:6]}")
logger.info(f"[BOOT] ELEVEN_VOICE_ID: {ELEVEN_VOICE_ID}")

if not ELEVEN_API_KEY:
    logger.warning("⚠️ Falta ELEVENLABS_API_KEY en Render")

if not ELEVEN_VOICE_ID:
    logger.warning("⚠️ Falta ELEVENLABS_VOICE_ID en Render")


# ================== AGENTE LOCAL NUXWAY (SIN OPENAI) ==================

def agente_nuxway(user_text: str) -> str:
    """
    Agente simple hecho en Python.
    Aquí puedes agregar reglas según palabras clave.
    NO usa OpenAI ni ElevenLabs Agent.
    """
    texto = (user_text or "").lower()

    if "hola" in texto or "buenas" in texto:
        return "Hola, soy el agente virtual de Nuxway. ¿Preguntas por PBX, por redes o por call center?"

    if "pbx" in texto or "troncal" in texto or "sip" in texto:
        return "Puedes decirme si quieres ayuda con configuración de troncales SIP, extensiones o IVR."

    if "twilio" in texto:
        return "Trabajamos con Twilio para integrar llamadas con tu PBX IP y con inteligencia artificial."

    if "soporte" in texto or "ayuda" in texto:
        return "Con gusto te ayudo. Dime brevemente cuál es tu problema y en qué sistema, por ejemplo PBX, WiFi o VPN."

    # Respuesta por defecto
    return "Te escucho. Cuéntame con más detalle qué necesitas y en qué plataforma estás trabajando."


# ================== ELEVENLABS TTS ==================

def elevenlabs_tts(text: str) -> str:
    """
    Convierte texto a MP3 usando ElevenLabs.
    """
    if not ELEVEN_API_KEY or not ELEVEN_VOICE_ID:
        logger.error("❌ No hay API KEY o VOICE ID configurado para ElevenLabs.")
        return ""

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE_ID}"

    headers = {
        "xi-api-key": ELEVEN_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg"
    }

    data = {
        "text": text,
        "model_id": "eleven_multilingual_v2",  # cambia a eleven_turbo_v2 si tu plan lo requiere
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.8
        }
    }

    try:
        resp = requests.post(url, headers=headers, json=data, timeout=40)

        if resp.status_code != 200:
            logger.error(f"❌ Error ElevenLabs {resp.status_code}: {resp.text}")
            resp.raise_for_status()

        filename = f"tts_{int(time.time()*1000)}.mp3"
        filepath = os.path.join(AUDIO_DIR, filename)

        with open(filepath, "wb") as f:
            f.write(resp.content)

        audio_url = url_for("serve_audio", filename=filename, _external=True)
        logger.info(f"✅ Audio generado: {audio_url}")
        return audio_url

    except Exception:
        logger.exception("Error generando TTS ElevenLabs")
        return ""


# ================== RUTAS FLASK ==================

@app.route("/", methods=["GET"])
def index():
    return "IVR Nuxway + ElevenLabs TTS (sin OpenAI)", 200


@app.route("/audio/<path:filename>", methods=["GET"])
def serve_audio(filename):
    return send_from_directory(AUDIO_DIR, filename, mimetype="audio/mpeg")


@app.route("/voice", methods=["POST"])
def voice_webhook():
    vr = VoiceResponse()

    user_text = request.values.get("SpeechResult", "")
    logger.info(f"Usuario dijo: {user_text}")

    # PRIMER TURNO: no hay texto todavía
    if not user_text:
        saludo = (
            "Hola, soy el agente virtual de Nuxway con voz de Eleven Labs. "
            "Dime brevemente en qué necesitas ayuda."
        )
        audio_saludo = elevenlabs_tts(saludo)

        if audio_saludo:
            vr.play(audio_saludo)
        else:
            vr.say("Hola, soy el agente virtual de Nuxway. ¿En qué puedo ayudarte?",
                   language="es-ES")

        gather = Gather(
            input="speech",
            action="/voice",
            language="es-ES",
            speech_timeout="auto"
        )
        vr.append(gather)
        return Response(str(vr), mimetype="text/xml")

    # SIGUIENTES TURNOS: ya habló el usuario
    respuesta_texto = agente_nuxway(user_text)
    audio_url = elevenlabs_tts(respuesta_texto)

    if audio_url:
        vr.play(audio_url)
    else:
        vr.say(respuesta_texto, language="es-ES")

    # Dejar la conversación abierta
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



