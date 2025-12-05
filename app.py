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
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVEN_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "").strip()

OPENAI_URL = "https://api.openai.com/v1/chat/completions"

logger.info(f"[BOOT] OPENAI_API_KEY set: {bool(OPENAI_API_KEY)}")
logger.info(f"[BOOT] ELEVEN_API_KEY empieza con: {ELEVEN_API_KEY[:6]}")
logger.info(f"[BOOT] ELEVEN_VOICE_ID: {ELEVEN_VOICE_ID}")

if not OPENAI_API_KEY:
    logger.warning("⚠️ Falta OPENAI_API_KEY en Render")

if not ELEVEN_API_KEY:
    logger.warning("⚠️ Falta ELEVENLABS_API_KEY en Render")

if not ELEVEN_VOICE_ID:
    logger.warning("⚠️ Falta ELEVENLABS_VOICE_ID en Render")


# ================== AGENTE: OPENAI ==================

def ask_openai(user_text: str) -> str:
    """
    Llama a OpenAI para que actúe como agente de Nuxway.
    Devuelve texto en español listo para leer con ElevenLabs.
    """
    if not OPENAI_API_KEY:
        logger.error("No hay OPENAI_API_KEY configurado")
        return "Lo siento, tengo un problema interno y no puedo responder ahora mismo."

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    data = {
        "model": "gpt-4.1-mini",  # puedes cambiar a otro modelo si quieres
        "messages": [
            {
                "role": "system",
                "content": (
                    "Eres el agente de soporte de Nuxway Technology. "
                    "Respondes en español, con frases cortas y claras. "
                    "Tono profesional, amable y seguro. "
                    "Ayudas en temas de PBX IP, SIP, telefonía, Twilio, redes, VPN, "
                    "contact center y soluciones de Nuxway como Cloud PBX, NuxCaller y NuxGATE."
                ),
            },
            {"role": "user", "content": user_text},
        ],
        "temperature": 0.3,
    }

    try:
        resp = requests.post(OPENAI_URL, headers=headers, json=data, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
        answer = payload["choices"][0]["message"]["content"].strip()
        logger.info(f"OpenAI respondió: {answer}")
        return answer
    except Exception:
        logger.exception("Error llamando a OpenAI")
        return "Hubo un problema interno al procesar tu consulta. Por favor, intenta de nuevo."


# ================== VOZ: ELEVENLABS TTS ==================

def elevenlabs_tts(text: str) -> str:
    """
    Convierte texto en audio MP3 usando ElevenLabs.
    Devuelve la URL pública para que Twilio la reproduzca.
    """
    logger.info(f"ELEVEN_API_KEY empieza con: {ELEVEN_API_KEY[:6]}")
    logger.info(f"ELEVEN_VOICE_ID: {ELEVEN_VOICE_ID}")

    if not ELEVEN_API_KEY or not ELEVEN_VOICE_ID:
        logger.error("❌ No hay API KEY o VOICE ID configurado para ElevenLabs.")
        return ""

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE_ID}"

    headers = {
        "xi-api-key": ELEVEN_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }

    # Ajusta el modelo según tu plan: eleven_multilingual_v2, eleven_turbo_v2, etc.
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
    return "PBX + OpenAI + ElevenLabs funcionando", 200


@app.route("/audio/<path:filename>", methods=["GET"])
def serve_audio(filename):
    """Twilio descargará el MP3 desde aquí."""
    return send_from_directory(AUDIO_DIR, filename, mimetype="audio/mpeg")


@app.route("/voice", methods=["POST"])
def voice_webhook():
    vr = VoiceResponse()

    # Texto de Twilio (STT)
    user_speech = request.values.get("SpeechResult", "")
    logger.info(f"Usuario dijo: {user_speech}")

    # -------- PRIMER TURNO: NO HAY TEXTO AÚN --------
    if not user_speech:
        saludo = (
            "Hola, soy el agente virtual de Nuxway. "
            "Estoy usando inteligencia artificial y voz de Eleven Labs. "
            "Cuéntame en pocas palabras en qué necesitas ayuda."
        )
        saludo_url = elevenlabs_tts(saludo)

        if saludo_url:
            vr.play(saludo_url)
        else:
            vr.say("Hola, soy el agente virtual de Nuxway. ¿En qué puedo ayudarte?",
                   language="es-ES")

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

    # 1) Preguntamos a OpenAI (agente)
    answer_text = ask_openai(user_speech)

    # 2) Convertimos esa respuesta a voz con ElevenLabs
    audio_url = elevenlabs_tts(answer_text)

    if audio_url:
        vr.play(audio_url)
    else:
        vr.say(answer_text, language="es-ES")

    # 3) Dejamos la conversación abierta para más preguntas
    gather = Gather(
        input="speech",
        action="/voice",
        method="POST",
        language="es-ES",
        speech_timeout="auto",
    )
    vr.append(gather)

    return Response(str(vr), mimetype="text/xml")


