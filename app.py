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
ELEVEN_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "").strip()  # p.ej: "21m00Tcm4TlvDq8ikWAM"

OPENAI_URL = "https://api.openai.com/v1/chat/completions"

if not OPENAI_API_KEY:
    logger.warning("⚠️ Falta OPENAI_API_KEY en el entorno")

if not ELEVEN_API_KEY:
    logger.warning("⚠️ Falta ELEVENLABS_API_KEY en el entorno")

if not ELEVEN_VOICE_ID:
    logger.warning("⚠️ Falta ELEVENLABS_VOICE_ID en el entorno")


# ================== FUNCIONES AUXILIARES ==================

def ask_openai(user_text: str) -> str:
    """
    Llama a OpenAI para generar la respuesta del asistente.
    Puedes ajustar el 'system' según tu caso de uso PBX / Nuxway.
    """
    if not OPENAI_API_KEY:
        return "Lo siento, no tengo configurada la clave de OpenAI."

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    data = {
        "model": "gpt-4.1-mini",  # ajusta el modelo si quieres otro
        "messages": [
            {
                "role": "system",
                "content": (
                    "Eres el agente de soporte de Nuxway Technology. "
                    "Respondes corto, claro y profesional sobre PBX IP, SIP, "
                    "telefonía, redes, call center y soluciones de Nuxway."
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
    except Exception as e:
        logger.exception("Error llamando a OpenAI")
        return "Hubo un problema interno procesando tu consulta."


def elevenlabs_tts(text: str) -> str:
    """
    Llama a ElevenLabs para convertir texto en audio (MP3).
    Devuelve la URL absoluta del archivo para que Twilio haga <Play>.
    """
    if not ELEVEN_API_KEY or not ELEVEN_VOICE_ID:
        logger.error("Faltan ELEVENLABS_API_KEY o ELEVENLABS_VOICE_ID")
        return ""

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE_ID}"

    headers = {
        "xi-api-key": ELEVEN_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }

    data = {
        "text": text,
        "model_id": "eleven_multilingual_v2",  # modelo recomendado (ajustable)
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.8,
        },
    }

    try:
        resp = requests.post(url, headers=headers, json=data, timeout=40)
        resp.raise_for_status()

        # Nombre único para el archivo
        filename = f"tts_{int(time.time() * 1000)}.mp3"
        filepath = os.path.join(AUDIO_DIR, filename)

        with open(filepath, "wb") as f:
            f.write(resp.content)

        audio_url = url_for("serve_audio", filename=filename, _external=True)
        logger.info(f"Audio generado en: {audio_url}")
        return audio_url
    except Exception as e:
        logger.exception("Error llamando a ElevenLabs")
        return ""


# ================== RUTAS FLASK ==================


@app.route("/", methods=["GET"])
def index():
    return "PBX + OpenAI + ElevenLabs: OK", 200


@app.route("/audio/<path:filename>", methods=["GET"])
def serve_audio(filename):
    """
    Twilio va a pedir aquí el MP3 generado por ElevenLabs.
    """
    return send_from_directory(AUDIO_DIR, filename, mimetype="audio/mpeg")


@app.route("/voice", methods=["POST"])
def voice_webhook():
    """
    Webhook de Twilio Voice.
    - Si no hay texto del usuario todavía, pide que hable.
    - Si hay SpeechResult / Digits, manda a OpenAI y luego TTS con ElevenLabs.
    """
    vr = VoiceResponse()

    # Intentamos recoger lo que dijo el usuario
    speech_result = request.values.get("SpeechResult", "")
    transcription = request.values.get("TranscriptionText", "")
    digits = request.values.get("Digits", "")

    user_text = speech_result or transcription or digits

    logger.info(f"Texto recibido del usuario: '{user_text}'")

    if not user_text:
        # Primer ingreso: pedimos que hable
        gather = Gather(
            input="speech",
            action="/voice",
            method="POST",
            language="es-ES",
            speech_timeout="auto",
        )
        gather.say("Hola, soy el asistente de Nuxway. ¿En qué puedo ayudarte?")
        vr.append(gather)
        return Response(str(vr), mimetype="text/xml")

    # Ya tenemos algo del usuario -> pedimos respuesta a OpenAI
    answer_text = ask_openai(user_text)

    # Convertimos la respuesta a audio con ElevenLabs
    audio_url = elevenlabs_tts(answer_text)

    if not audio_url:
        # Si falló ElevenLabs, usamos <Say> como fallback
        vr.say(answer_text, language="es-ES", voice="alice")
    else:
        vr.play(audio_url)

    # Preparamos un nuevo Gather para seguir la conversación
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


