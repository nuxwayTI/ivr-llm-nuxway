from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import os
import logging
import requests
import time

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# =========================
#  CONFIG OPENAI
# =========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

session = requests.Session()  # menor latencia

# =========================
# PROMPT DEL AGENTE IA (OPTIMIZADO)
# =========================
SYSTEM_PROMPT = """
Eres el Agente de Inteligencia Artificial de Nuxway Technology.
Respondes SOLO en español y atiendes llamadas telefónicas.

Necesitas presentarte y dar mensajes de felicitación por fiestas de fin de anio: 


Tu objetivo secundario
Ayudar al cliente con temas de:
- comunicaciones unificadas,
- telefonía IP y PBX,
- contact center y call center,
- redes de datos, WiFi empresarial y VPN,
- soluciones de Nuxway como Cloud PBX, NuxCaller y NuxGATE.

🎙 Estilo de respuesta
- Frases cortas y muy claras (máx. 2–3 frases por respuesta).
- Tono profesional, amable y seguro.
- Explica de forma simple; entra en detalles técnicos solo si el cliente lo necesita.
- Siempre suena como un ingeniero de soporte real.

👤 Uso del nombre
Si el usuario dice su nombre (por ejemplo: "me llamo Carlos", "habla Ana de Empresa X"):
- Respóndele usando su nombre en esa misma respuesta y su empresa, por ejemplo:
  "Gracias Carlos de Nuxway, con gusto te ayudo..." o "Perfecto Ana de Nuxway, revisemos tu caso...".

📏 Reglas
- Antes de dar una solución, haz 1 o 2 preguntas para entender la situación.
- Si el caso parece complejo o el cliente pide un humano, sugiere derivar a un agente humano.
- No inventes información; si no sabes algo, dilo de forma honesta y propone escalar el caso.
"""

# =========================
#  GPT CALL
# =========================
def llamar_gpt(prompt_usuario: str) -> str:
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}

    data = {
        "model": "gpt-4.1-mini",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt_usuario},
        ],
        "max_tokens": 45,      # respuestas cortas, menos latencia
        "temperature": 0.2,
    }

    try:
        r = session.post(OPENAI_URL, json=data, headers=headers, timeout=6)
        if r.status_code != 200:
            logging.error(r.text)
            return "Tengo problemas con la inteligencia artificial en este momento."
        return r.json()["choices"][0]["message"]["content"]
    except Exception:
        logging.exception("GPT ERROR")
        return "Hubo un problema con la inteligencia artificial, intenta nuevamente."

# =========================
#  TRANSFERENCIA
# =========================
AGENT_SIP = "sip:6049@nuxway.sip.twilio.com"

def transferir_a_agente(vr):
    vr.say(
        "Te voy a comunicar con un agente humano. Por favor espera.",
        language="es-ES",
        voice="Polly.Lupe"
    )
    d = vr.dial()
    d.sip(AGENT_SIP)
    return Response(str(vr), mimetype="text/xml")

# =========================
#  IVR PRINCIPAL
# =========================
@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    speech = request.values.get("SpeechResult")
    digits = request.values.get("Digits")

    phase = request.args.get("phase", "initial")
    attempt = int(request.args.get("attempt", "1"))

    logging.info(f"[IVR] phase={phase} attempt={attempt} speech={speech}")

    vr = VoiceResponse()

    # ==============================================================
    # 1. NO INPUT (Silencio)
    # ==============================================================
    if not speech and not digits:

        # FOLLOWUP → colgar
        if phase == "followup":
            vr.say("Gracias por comunicarse con Nuxway Technology. Hasta luego.",
                   language="es-ES", voice="Polly.Lupe")
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        # INITIAL → repetir 2 veces máximo
        if attempt >= 3:
            vr.say("No escuché ninguna respuesta. Gracias por su llamada. Hasta luego.",
                   language="es-ES", voice="Polly.Lupe")
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        if attempt == 1:
            mensaje = (
                "Hola, soy el Agente  con Inteligencia Artificial General de Nuxway Technology. "
                "Para comenzar, ¿podrías brindarme tu nombre y el de tu empresa, por favor?"
            )
        else:
            mensaje = (
                "No logré escucharte. Te repito nuevamente. "
                "Por favor dime tu nombre y el de tu empresa."
            )

        next_attempt = attempt + 1

        gather = Gather(
            input="speech dtmf",
            language="es-ES",
            action=f"/ivr-llm?phase=initial&attempt={next_attempt}",
            method="POST",
            timeout=6,
            speech_timeout="auto"
        )
        gather.say(mensaje, language="es-ES", voice="Polly.Lupe")
        vr.append(gather)
        return Response(str(vr), mimetype="text/xml")

    # ==============================================================
    # 2. PIDIÓ HUMANO
    # ==============================================================
    text_lower = (speech or "").lower()

    if digits == "0" or "humano" in text_lower or "agente" in text_lower:
        return transferir_a_agente(vr)

    # ==============================================================
    # 3. GPT
    # ==============================================================
    respuesta_gpt = llamar_gpt(speech or "")

    vr.say(respuesta_gpt, language="es-ES", voice="Polly.Lupe")

    # ==============================================================
    # 4. FOLLOWUP – segunda ronda
    # ==============================================================
    gather2 = Gather(
        input="speech dtmf",
        language="es-ES",
        action="/ivr-llm?phase=followup",
        method="POST",
        timeout=7,
        speech_timeout="auto"
    )
    gather2.say(
        "¿Puedo ayudarte en algo más? Si necesitas hablar con un humano, di 'humano' o marca cero. "
        "Si no respondes, finalizaré la llamada.",
        language="es-ES",
        voice="Polly.Lupe"
    )
    vr.append(gather2)

    return Response(str(vr), mimetype="text/xml")

# =========================
#  HOME
# =========================
@app.route("/")
def home():
    return "Nuxway IVR LLM – Soporte IA activo ✔"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

