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
# PROMPT DEL AGENTE IA (CORTO Y ESTABLE)
# =========================
SYSTEM_PROMPT = """
Eres un Ingeniero de Soporte Especializado de Nuxway Technology.
Respondes SOLO en español.

Tu estilo:
- Profesional, claro y amable.
- Respuestas cortas (1 a 3 frases).
- Tono cercano y tranquilo.

Tu rol:
- Ayudas con redes de datos, comunicaciones unificadas y servicios de Nuxway.
- Haces preguntas simples para entender el problema.
- Si el caso lo requiere o el cliente lo pide, sugiere derivar a un agente humano.

En la primera interacción, si el usuario todavía no te ha dicho su nombre,
preséntate de forma natural como:
"Hola, soy el Agente de Inteligencia Artificial de Nuxway Technology."
y luego pide su nombre y el de su empresa dentro de la conversación.
"""

# =========================
#  API CALL GPT
# =========================
def llamar_gpt(prompt_usuario: str) -> str:
    if not OPENAI_API_KEY:
        logging.error("OPENAI_API_KEY no configurada")
        return "Hay un problema con la configuración de la inteligencia artificial."

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": "gpt-4.1-nano",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt_usuario}
        ],
        "max_tokens": 80,
        "temperature": 0.2
    }

    t0 = time.monotonic()
    try:
        r = session.post(OPENAI_URL, json=data, headers=headers, timeout=6)
        lat = time.monotonic() - t0
        logging.info(f"[GPT] {r.status_code} | {lat:.2f} s")

        if r.status_code != 200:
            logging.error(f"[GPT] Error body: {r.text[:300]}")
            return "Tengo problemas con la inteligencia artificial en este momento."

        return r.json()["choices"][0]["message"]["content"]

    except Exception:
        logging.exception("[GPT] Error")
        return "Hubo un problema con la inteligencia artificial, intenta nuevamente."


# =========================
#  TRANSFERENCIA A HUMANO
# =========================
AGENT_SIP = "sip:6049@nuxway.sip.twilio.com"

def transferir_a_agente(vr: VoiceResponse) -> Response:
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

    phase = request.args.get("phase", "initial")  # "initial" / "followup"
    attempt_param = request.args.get("attempt")
    attempt = int(attempt_param) if attempt_param is not None else 1

    logging.info(f"[IVR] phase={phase} attempt={attempt} speech={speech} digits={digits}")

    vr = VoiceResponse()

    # ===============================
    # 1) SILENCIO / SIN INPUT
    # ===============================
    if not speech and not digits:
        # FOLLOWUP: si no responde → colgamos directo
        if phase == "followup":
            vr.say(
                "No recibí ninguna respuesta. Gracias por comunicarse con Nuxway Technology. Hasta luego.",
                language="es-ES",
                voice="Polly.Lupe"
            )
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        # INITIAL: repetir mensaje 2 veces, en la 3ra colgar
        if attempt >= 3:
            vr.say(
                "No escuché ninguna respuesta. Muchas gracias por comunicarse con Nuxway Technology. Hasta luego.",
                language="es-ES",
                voice="Polly.Lupe"
            )
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        next_attempt = attempt + 1

        # Mensaje inicial: se repite si no hablan
        if attempt == 1:
            mensaje = (
                "Hola, soy el Agente de Inteligencia Artificial de Nuxway Technology. "
                "Para comenzar, por favor dime tu nombre y el de tu empresa después de este mensaje."
            )
        else:  # attempt == 2
            mensaje = (
                "Parece que no logré escucharte. Te repito nuevamente el mensaje. "
                "Por favor, dime tu nombre y el de tu empresa después de este mensaje."
            )

        gather = Gather(
            input="speech",
            language="es-ES",
            action=f"/ivr-llm?phase=initial&attempt={next_attempt}",
            method="POST",
            timeout=7,
            speech_timeout="3"
        )
        gather.say(mensaje, language="es-ES", voice="Polly.Lupe")
        vr.append(gather)
        return Response(str(vr), mimetype="text/xml")

    # ===============================
    # 2) PIDIÓ HUMANO
    # ===============================
    text_lower = (speech or "").lower()
    if digits == "0" or "humano" in text_lower or "agente" in text_lower:
        return transferir_a_agente(vr)

    # ===============================
    # 3) GPT RESPONDE
    # ===============================
    respuesta_gpt = llamar_gpt(speech or "")
    vr.say(respuesta_gpt, language="es-ES", voice="Polly.Lupe")

    # ===============================
    # 4) FOLLOWUP
    # ===============================
    gather2 = Gather(
        input="speech",
        language="es-ES",
        action="/ivr-llm?phase=followup&attempt=1",
        method="POST",
        timeout=7,
        speech_timeout="3"
    )
    gather2.say(
        "¿Puedo ayudarte en algo más? "
        "Si necesitas un humano, di 'humano' o marca cero. "
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



