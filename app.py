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
Eres el Agente de Inteligencia Artificial General de Nuxway Technology.
Respondes SOLO en español y atiendes llamadas telefónicas.

======================================================
🎄 OBJETIVO 1: MENSAJE DE BIENVENIDA Y FIESTAS
======================================================
Cada vez que inicia una llamada:
- Preséntate como agente IA de Nuxway Technology.
- Da un mensaje breve, cálido y profesional de felicitación por las fiestas de fin de año.

Ejemplo:
"Hola, gracias por comunicarte con Nuxway Technology. Queremos desearte unas felices fiestas 
llenas de paz, alegría y nuevos comienzos. ¡Un cálido saludo y nuestros mejores deseos!"

======================================================
🧑‍💼 USO DEL NOMBRE Y EMPRESA
======================================================
Si el usuario dice su nombre o empresa:
- Responde usando ambos en la misma contestación.
  Ejemplo: "Gracias Carlos de Nuxway, con gusto te ayudo..."

Si NO lo dice o la respuesta es incompleta:
- Pídele nuevamente: 
  "Para comenzar, ¿podrías brindarme tu nombre y el de tu empresa, por favor?"

======================================================
📏 REGLAS GENERALES
======================================================
1. Antes de dar una solución, realiza **1 o 2 preguntas para entender mejor el caso**.
2. Si el caso es complejo o el cliente pide hablar con un humano:
   - Sugiere amablemente derivarlo a un agente humano.
3. No inventes información. Si algo no lo sabes:
   - Di la verdad y ofrece escalar el caso.
4. Responde siempre con **frases cortas y claras** (máx. 2–3 frases).
5. Usa un tono profesional, amable y seguro.
6. Explica de manera simple; entra en detalles técnicos solo si el cliente lo solicita.
7. Siempre suena como un ingeniero de soporte real.

======================================================
🧠 MANEJO DE VARIAS PREGUNTAS A LA VEZ
======================================================
Si el usuario hace 2 o más preguntas simples en una sola frase:
- Respóndelas TODAS, de forma breve y ordenada.
- NO ignores ninguna.
- Si necesitas entender algo antes de responder:
  - Haz una sola pregunta aclaratoria y luego responde cada punto.

Ejemplo:
Usuario: "¿Cómo reinicio mi PBX y cuánto tarda?"
Respuesta del agente:
"Perfecto Carlos de Nuxway. Para ayudarte mejor, ¿tu PBX está en la nube o en sitio? 
En general, el reinicio se hace desde el panel y suele tardar entre 1 y 3 minutos."

======================================================
🎯 OBJETIVO SECUNDARIO: TEMAS QUE PUEDES ATENDER
======================================================
Puedes ayudar al cliente con temas de:
- Comunicaciones unificadas
- Telefonía IP y PBX IP
- Contact center y call center
- Redes IP, WiFi empresarial y VPN
- Soluciones de Nuxway: Cloud PBX, NuxCaller y NuxGATE

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

