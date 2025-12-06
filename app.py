from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import os
import logging
import requests
import time
from collections import defaultdict

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# =========================
#  CONFIG OPENAI
# =========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

session = requests.Session()  # menor latencia

# =========================
# MEMORIA POR LLAMADA
# =========================
# NOTA: Esto guarda la conversación en memoria RAM del proceso.
# Para producción serio conviene usar Redis o una base externa.
conversaciones = defaultdict(list)

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
1. Antes de dar una solución, realiza 1 o 2 preguntas para entender mejor el caso.
2. Si el caso es complejo o el cliente pide hablar con un humano:
   - Sugiere amablemente derivarlo a un agente humano.
3. No inventes información. Si algo no lo sabes:
   - Di la verdad y ofrece escalar el caso.
4. Responde siempre con frases cortas y claras (máx. 2–3 frases).
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
🧮 CÁLCULOS Y RESPUESTAS CON NÚMEROS
======================================================
Si el usuario te pide operaciones numéricas simples (sumar, restar, multiplicar, dividir):
- Calcula el resultado con precisión.
- Responde de forma breve indicando el resultado explícito.

Ejemplo:
Usuario: "¿Cuánto es 35 + 7?"
Agente: "35 más 7 es igual a 42."

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
#  GPT CALL (CON MEMORIA POR CallSid)
# =========================
def llamar_gpt(call_sid: str, prompt_usuario: str) -> str:
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    historial = conversaciones[call_sid]  # lista de mensajes previos

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *historial,
        {"role": "user", "content": prompt_usuario},
    ]

    data = {
        "model": "gpt-4.1-mini",
        "messages": messages,
        "max_tokens": 120,      # un poco más largo para respuestas claras
        "temperature": 0.2,
    }

    try:
        r = session.post(OPENAI_URL, json=data, headers=headers, timeout=8)
        if r.status_code != 200:
            logging.error(f"OPENAI ERROR: {r.status_code} {r.text}")
            return "Tengo problemas con la inteligencia artificial en este momento."

        json_resp = r.json()
        respuesta = json_resp["choices"][0]["message"]["content"]

        # Guardamos el turno en la memoria de la llamada
        conversaciones[call_sid].append({"role": "user", "content": prompt_usuario})
        conversaciones[call_sid].append({"role": "assistant", "content": respuesta})

        return respuesta
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
    call_sid = request.values.get("CallSid", "unknown-call")

    phase = request.args.get("phase", "initial")
    attempt = int(request.args.get("attempt", "1"))

    logging.info(f"[IVR] call_sid={call_sid} phase={phase} attempt={attempt} speech={speech} digits={digits}")

    vr = VoiceResponse()

    # ==============================================================
    # 1. NO INPUT (Silencio)
    # ==============================================================
    if not speech and not digits:

        # FOLLOWUP → colgar
        if phase == "followup":
            vr.say(
                "Gracias por comunicarse con Nuxway Technology. Hasta luego.",
                language="es-ES",
                voice="Polly.Lupe"
            )
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        # INITIAL → repetir 2 veces máximo
        if attempt >= 3:
            vr.say(
                "No escuché ninguna respuesta. Gracias por su llamada. Hasta luego.",
                language="es-ES",
                voice="Polly.Lupe"
            )
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        if attempt == 1:
            mensaje = (
                "Hola, soy el Agente con Inteligencia Artificial General de Nuxway Technology. "
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
    # Usamos lo que haya: primero voz, si no hay, los dígitos
    texto_usuario = speech or digits or ""
    logging.info(f"[IVR] Texto para GPT: {texto_usuario}")

    respuesta_gpt = llamar_gpt(call_sid, texto_usuario)

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
    # En Render normalmente usas el puerto del entorno, pero para local está bien 5000
    app.run(host="0.0.0.0", port=5000, debug=True)
