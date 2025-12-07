from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import os
import logging
import requests
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
conversaciones = defaultdict(list)


# =========================
# PROMPT DEL AGENTE IA
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

======================================================
🧑‍💼 USO DEL NOMBRE Y EMPRESA
======================================================
Si el usuario dice su nombre o empresa:
- Respóndele usando ambos.
Si NO lo dice:
- Pídeselo nuevamente.

======================================================
📏 ESTILO
======================================================
- Frases cortas y claras.
- Tono profesional, amable y seguro.
- No inventes información.
- Haz 1 o 2 preguntas antes de responder casos técnicos.
"""


# =========================
#  GPT CALL
# =========================
def llamar_gpt(call_sid: str, prompt_usuario: str) -> str:
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    historial = conversaciones[call_sid]

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *historial,
        {"role": "user", "content": prompt_usuario},
    ]

    data = {
        "model": "gpt-4.1-mini",
        "messages": messages,
        "max_tokens": 120,
        "temperature": 0.2,
    }

    try:
        r = session.post(OPENAI_URL, json=data, headers=headers, timeout=8)

        if r.status_code != 200:
            logging.error(f"OPENAI ERROR: {r.status_code} {r.text}")
            return "Tengo problemas con la inteligencia artificial en este momento."

        respuesta = r.json()["choices"][0]["message"]["content"]

        conversaciones[call_sid].append({"role": "user", "content": prompt_usuario})
        conversaciones[call_sid].append({"role": "assistant", "content": respuesta})

        return respuesta

    except Exception:
        logging.exception("GPT ERROR")
        return "Hubo un problema con la inteligencia artificial, intenta nuevamente."


# =========================
#  TRANSFERENCIA A AGENTE HUMANO
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

    # Posible detección de máquina si Twilio manda AnsweredBy
    answered_by = request.values.get("AnsweredBy")
    logging.info(
        f"[IVR] call_sid={call_sid} phase={phase} attempt={attempt} "
        f"answered_by={answered_by} speech={speech} digits={digits}"
    )

    vr = VoiceResponse()

    # Si Twilio nos dice explícitamente que NO es humano
    if answered_by and answered_by != "human":
        vr.say(
            "Detecté que no hay una persona en la línea. "
            "Voy a finalizar esta llamada.",
            language="es-ES",
            voice="Polly.Lupe"
        )
        vr.hangup()
        return Response(str(vr), mimetype="text/xml")

    # ==============================================================
    # 1. NO INPUT (Silencio)
    # ==============================================================
    if not speech and not digits:

        # -------- FOLLOWUP (usuario en segunda ronda) ----------
        if phase == "followup":

            if attempt >= 3:
                vr.say(
                    "No logré escucharte. Gracias por comunicarte con Nuxway Technology. Hasta luego.",
                    language="es-ES",
                    voice="Polly.Lupe"
                )
                vr.hangup()
                return Response(str(vr), mimetype="text/xml")

            # Usamos OpenAI para que la IA hable cuando hay silencio
            prompt_usuario = (
                f"El usuario guardó silencio en la llamada (intento {attempt}) "
                "cuando le preguntaste si necesitaba algo más. "
                "Vuelve a hablar tú: salúdalo brevemente y pregúntale si necesita algo más, "
                "recordándole que puede decir 'humano' o marcar cero para hablar con un agente humano. "
                "Sé muy breve y claro."
            )
            respuesta_gpt = llamar_gpt(call_sid, prompt_usuario)

            next_attempt = attempt + 1

            gather = Gather(
                input="speech dtmf",
                language="es-ES",
                action=f"/ivr-llm?phase=followup&attempt={next_attempt}",
                method="POST",
                timeout=7,
                speech_timeout="auto"
            )
            # La IA habla dentro del Gather
            gather.say(respuesta_gpt, language="es-ES", voice="Polly.Lupe")
            vr.append(gather)
            return Response(str(vr), mimetype="text/xml")

        # -------- INICIO (pedir nombre/empresa) ----------
        if attempt >= 3:
            vr.say(
                "No escuché ninguna respuesta. Gracias por su llamada. Hasta luego.",
                language="es-ES",
                voice="Polly.Lupe"
            )
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        # Usamos OpenAI también para la parte inicial con silencio
        prompt_usuario = (
            f"El usuario guardó silencio al inicio de la llamada (intento {attempt}). "
            "Vuelve a hablar tú: saluda brevemente, desea felices fiestas como indica el sistema, "
            "y pídele nuevamente que diga su nombre y el de su empresa, de forma muy clara y corta."
        )
        respuesta_gpt = llamar_gpt(call_sid, prompt_usuario)

        next_attempt = attempt + 1

        gather = Gather(
            input="speech dtmf",
            language="es-ES",
            action=f"/ivr-llm?phase=initial&attempt={next_attempt}",
            method="POST",
            timeout=6,
            speech_timeout="auto"
        )
        gather.say(respuesta_gpt, language="es-ES", voice="Polly.Lupe")
        vr.append(gather)
        return Response(str(vr), mimetype="text/xml")

    # ==============================================================
    # 2. PIDIÓ HABLAR CON HUMANO
    # ==============================================================
    text_lower = (speech or "").lower()

    if digits == "0" or "humano" in text_lower or "agente" in text_lower:
        return transferir_a_agente(vr)

    # ==============================================================
    # 3. GPT — Responder consulta normal
    # ==============================================================
    texto_usuario = speech or digits or ""
    logging.info(f"[IVR] Texto para GPT: {texto_usuario}")

    respuesta_gpt = llamar_gpt(call_sid, texto_usuario)

    vr.say(respuesta_gpt, language="es-ES", voice="Polly.Lupe")

    # ==============================================================
    # 4. FOLLOWUP – Preguntar si necesita algo más
    # ==============================================================
    gather2 = Gather(
        input="speech dtmf",
        language="es-ES",
        action="/ivr-llm?phase=followup&attempt=1",
        method="POST",
        timeout=7,
        speech_timeout="auto"
    )
    gather2.say(
        "¿Puedo ayudarte en algo más? Si necesitas hablar con un humano, di 'humano' o marca cero. "
        "Si no respondes, volveré a hablarte.",
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


