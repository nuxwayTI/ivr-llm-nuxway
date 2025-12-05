import os
import time
import logging
from flask import Flask, request, Response, send_from_directory, url_for
import requests
from twilio.twiml.voice_response import VoiceResponse, Gather

# ================== LOG & APP ==================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

AUDIO_DIR = os.path.join(os.path.dirname(__file__), "static", "audio")
os.makedirs(AUDIO_DIR, exist_ok=True)

ELEVEN_API_KEY  = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVEN_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "").strip()
ELEVEN_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "").strip()

logger.info(f"[BOOT] ELEVENLABS_API_KEY inicia: {ELEVEN_API_KEY[:6]}")
logger.info(f"[BOOT] ELEVENLABS_AGENT_ID: {ELEVEN_AGENT_ID}")
logger.info(f"[BOOT] ELEVENLABS_VOICE_ID: {ELEVEN_VOICE_ID}")

if not ELEVEN_API_KEY:
    logger.warning("⚠️ Falta ELEVENLABS_API_KEY en Render")
if not ELEVEN_AGENT_ID:
    logger.warning("⚠️ Falta ELEVENLABS_AGENT_ID en Render")
if not ELEVEN_VOICE_ID:
    logger.warning("⚠️ Falta ELEVENLABS_VOICE_ID en Render")

# ================== MEMORIA POR LLAMADA ==================
# Guardamos el histórico de la conversación por CallSid
# { call_sid: [mensajes_estilo_openai] }

conversation_histories = {}


# ================== CLIENTE AGENTE ELEVENLABS ==================

def elevenlabs_agent_reply(call_sid: str, user_text: str) -> str:
    """
    Llama al agente de ElevenLabs usando el endpoint de simulate-conversation.
    Usa ELEVENLABS_AGENT_ID y mantiene histórico por CallSid.
    """
    if not ELEVEN_API_KEY or not ELEVEN_AGENT_ID:
        logger.error("❌ Falta API KEY o AGENT_ID de ElevenLabs")
        return "Lo siento, hubo un problema con la configuración del agente."

    history = conversation_histories.get(call_sid, [])

    # Construimos el body según la API de simulate-conversation
    # Ruta típica: /v1/convai/agents/{agent_id}/simulate-conversation
    url = f"https://api.elevenlabs.io/v1/convai/agents/{ELEVEN_AGENT_ID}/simulate-conversation"

    headers = {
        "xi-api-key": ELEVEN_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # Mensaje actual del usuario en formato "messages"
    user_message = {
        "role": "user",
        "content": [
            {
                "type": "input_text",
                "text": user_text
            }
        ]
    }

    payload = {
        # Histórico completo de la conversación hasta ahora
        "conversation_history": history,
        # Mensaje actual que queremos que el agente responda
        "input_messages": [user_message],
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=40)
        if resp.status_code != 200:
            logger.error(f"❌ Error agente ElevenLabs {resp.status_code}: {resp.text}")
            resp.raise_for_status()

        data = resp.json()
        # El formato exacto puede variar, pero normalmente hay una lista de mensajes de salida
        output_messages = data.get("output_messages") or data.get("messages") or []

        if not output_messages:
            logger.warning("⚠️ Agente ElevenLabs no devolvió mensajes")
            reply_text = "Lo siento, no tengo una respuesta en este momento."
        else:
            # Tomamos el primer mensaje del agente
            first_agent_msg = output_messages[0]
            contents = first_agent_msg.get("content", [])
            # buscamos el primer bloque de texto
            reply_text = "Lo siento, no entendí tu consulta."
            for c in contents:
                if c.get("type") in ("output_text", "text", "assistant_text"):
                    reply_text = c.get("text") or c.get("value") or reply_text
                    break

        # Actualizamos el histórico: usuario + agente
        history.append(user_message)
        agent_message = {
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": reply_text
                }
            ]
        }
        history.append(agent_message)
        conversation_histories[call_sid] = history

        logger.info(f"🤖 Agente ElevenLabs respondió: {reply_text}")
        return reply_text

    except Exception:
        logger.exception("❌ Error llamando al agente ElevenLabs")
        return "Lo siento, hubo un problema procesando tu consulta."


# ================== ELEVENLABS TTS ==================

def elevenlabs_tts(text: str) -> str:
    """
    Convierte texto a MP3 usando ElevenLabs TTS.
    """
    if not ELEVEN_API_KEY or not ELEVEN_VOICE_ID:
        logger.error("❌ No hay API KEY o VOICE ID configurado para ElevenLabs TTS.")
        return ""

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE_ID}"

    headers = {
        "xi-api-key": ELEVEN_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }

    data = {
        "text": text,
        # Ajusta modelo si tu plan requiere otro (p.ej. eleven_turbo_v2)
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.8,
        },
    }

    try:
        resp = requests.post(url, headers=headers, json=data, timeout=40)
        if resp.status_code != 200:
            logger.error(f"❌ Error ElevenLabs TTS {resp.status_code}: {resp.text}")
            resp.raise_for_status()

        filename = f"tts_{int(time.time() * 1000)}.mp3"
        filepath = os.path.join(AUDIO_DIR, filename)

        with open(filepath, "wb") as f:
            f.write(resp.content)

        audio_url = url_for("serve_audio", filename=filename, _external=True)
        logger.info(f"✅ Audio TTS generado: {audio_url}")
        return audio_url

    except Exception:
        logger.exception("Error generando TTS ElevenLabs")
        return ""


# ================== RUTAS FLASK / TWILIO ==================

@app.route("/", methods=["GET"])
def index():
    return "IVR Nuxway + ElevenLabs Agent (simulate-conversation) + TTS", 200


@app.route("/audio/<path:filename>", methods=["GET"])
def serve_audio(filename):
    return send_from_directory(AUDIO_DIR, filename, mimetype="audio/mpeg")


@app.route("/voice", methods=["POST"])
def voice_webhook():
    vr = VoiceResponse()

    call_sid = request.values.get("CallSid", "")
    user_text = request.values.get("SpeechResult", "")

    logger.info(f"CallSid: {call_sid}")
    logger.info(f"Usuario dijo: {user_text}")

    # PRIMER TURNO: no hay texto todavía => saludo del agente
    if not user_text:
        # Para el saludo, podemos mandar un "Hola" interno al agente
        saludo_user_text = "El usuario acaba de llamar. Preséntate como agente virtual de Nuxway y pregúntale en qué necesita ayuda."
        reply_text = elevenlabs_agent_reply(call_sid, saludo_user_text)

        audio_url = elevenlabs_tts(reply_text)
        if audio_url:
            vr.play(audio_url)
        else:
            vr.say(reply_text, language="es-ES")

        gather = Gather(
            input="speech",
            action="/voice",
            language="es-ES",
            speech_timeout="auto",
        )
        vr.append(gather)
        return Response(str(vr), mimetype="text/xml")

    # SIGUIENTES TURNOS: usuario ya habló, pasamos por el agente de ElevenLabs
    reply_text = elevenlabs_agent_reply(call_sid, user_text)
    audio_url = elevenlabs_tts(reply_text)

    if audio_url:
        vr.play(audio_url)
    else:
        vr.say(reply_text, language="es-ES")

    # Seguimos la conversación mientras no corte
    gather = Gather(
        input="speech",
        action="/voice",
        language="es-ES",
        speech_timeout="auto",
    )
    vr.append(gather)

    return Response(str(vr), mimetype="text/xml")


# ================== MAIN LOCAL ==================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)



