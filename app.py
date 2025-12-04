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
session = requests.Session()

# =========================
# PROMPT IA
# =========================
SYSTEM_PROMPT = """
Eres un Ingeniero de Soporte Especializado de Nuxway Technology.
Respondes SOLO en español.

Tu estilo:
• Profesional, claro, amable y empático.
• Frases cortas, pausadas para voz.
• Cordial y directo.

Reglas:
• Primero felicitas por las fiestas.
• Preguntas nombre y empresa.
• Ayudas en redes, comunicaciones unificadas y soporte.
• Acompañas paso a paso.
• Escalas a humano si lo piden.

Mensaje obligatorio (una vez):
"Queremos desearle unas felices fiestas de fin de año de parte de toda la familia Nuxway. Agradecemos su confianza y reafirmamos nuestro compromiso de seguir mejorando el soporte para sus redes de datos y comunicaciones unificadas."
"""

# =========================
#  GPT CALL
# =========================
def llamar_gpt(prompt_usuario: str) -> str:
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": "gpt-4.1-nano",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt_usuario},
        ],
        "max_tokens": 60,
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
    dial = vr.dial()
    dial.sip(AGENT_SIP)
    return Response(str(vr), mimetype="text/xml")


# =========================
#  IVR PRINCIPAL
# =========================
@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    speech = request.values.get("SpeechResult")
    digits = request.values.get("Digits")

    # Eventos especiales de Twilio
    event_twilio = request.values.get("Event", "")
    timeout_reason = request.values.get("TimeoutReason", "")

    phase = request.args.get("phase", "initial")  # initial / followup
    attempt = int(request.args.get("attempt", "1"))

    logging.info(f"[IVR] phase={phase} attempt={attempt} speech={speech} event={event_twilio} timeout={timeout_reason}")

    vr = VoiceResponse()

    # =====================================================
    # DETECCIÓN DE SILENCIO REAL (MEGA FIX)
    # =====================================================
    no_hablo = (
        speech is None or
        speech.strip() == "" or
        ("timeout" in event_twilio.lower()) or
        ("timeout" in timeout_reason.lower()) or
        ("noinput" in event_twilio.lower())
    )

    # =====================================================
    # 1. NO HABLÓ
    # =====================================================
    if no_hablo:

        # FOLLOWUP → cuelga
        if phase == "followup":
            vr.say(
                "No recibí ninguna respuesta. Gracias por comunicarse con Nuxway Technology. Hasta luego.",
                language="es-ES",
                voice="Polly.Lupe"
            )
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        # INITIAL → repetir 2 veces
        if attempt >= 3:
            vr.say(
                "No escuché ninguna respuesta. Muchas gracias por comunicarse con Nuxway Technology. Hasta luego.",
                language="es-ES",
                voice="Polly.Lupe"
            )
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        next_attempt = attempt + 1

        mensaje_inicial = (
            "¡Hola! Soy el Agente con Inteligencia Artificial de Nuxway Technology. "
            "Para comenzar, ¿podrías brindarme tu nombre y el de tu empresa, por favor?"
        )

        # En el segundo intento, repetir explícito
        if attempt == 2:
            mensaje_inicial = (
                "Parece que no logramos escucharte. Te repito nuevamente. "
                + mensaje_inicial
            )

        gather = Gather(
            input="speech dtmf",
            language="es-ES",
            action=f"/ivr-llm?phase=initial&attempt={next_attempt}",
            method="POST",
            timeout=7,
            speech_timeout="auto"
        )
        gather.say(mensaje_inicial, language="es-ES", voice="Polly.Lupe")

        vr.append(gather)
        return Response(str(vr), mimetype="text/xml")

    # =====================================================
    # 2. PIDIO HUMANO
    # =====================================================
    text_lower = (speech or "").lower()

    if digits == "0" or "humano" in text_lower or "agente" in text_lower:
        return transferir_a_agente(vr)

    # =====================================================
    # 3. GPT
    # =====================================================
    respuesta_gpt = llamar_gpt(speech or "")

    vr.say(respuesta_gpt, language="es-ES", voice="Polly.Lupe")

    # =====================================================
    # 4. FOLLOWUP
    # =====================================================
    gather2 = Gather(
        input="speech dtmf",
        language="es-ES",
        action="/ivr-llm?phase=followup",
        method="POST",
        timeout=7,
        speech_timeout="auto"
    )
    gather2.say(
        "¿Puedo ayudarte en algo más? Si necesitas un humano, di 'humano' o marca cero. "
        "Si no me respondes, finalizaré la llamada.",
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
    return "Nuxway IVR LLM – Soporte IA activo."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)



