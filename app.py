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
# PROMPT DEL AGENTE IA
# =========================
SYSTEM_PROMPT = """
Eres un Ingeniero de Soporte Especializado de Nuxway Technology.
Respondes SOLO en español.

Tu estilo:
• Profesional, claro, técnico cuando corresponde.
• Empático, cordial, conversacional.
• Frases cortas, pausadas para voz.
• Siempre amable.

Contexto del rol:
• Ayudas en temas de redes, comunicaciones unificadas, soporte y servicios de Nuxway.
• Haces preguntas para entender el caso.
• Acompañas paso a paso.
• Ofreces siempre derivar a un humano si lo pide.

Regla especial (IMPORTANTE):
Después de que el usuario diga su nombre y empresa,
DEBES incluir SIEMPRE este mensaje una vez:

"Queremos desearle unas felices fiestas de fin de año de parte de toda la familia Nuxway. Agradecemos su confianza y reafirmamos nuestro compromiso de seguir mejorando el soporte para sus redes de datos y comunicaciones unificadas."

Guardrails:
• No inventes información.
• Si no sabes algo, dilo y deriva.
• Nunca compartas datos sensibles.
"""


# =========================
#  API CALL GPT
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
            {"role": "user", "content": prompt_usuario}
        ],
        "max_tokens": 60,
        "temperature": 0.2
    }

    t0 = time.monotonic()

    try:
        r = session.post(OPENAI_URL, json=data, headers=headers, timeout=5)
        lat = time.monotonic() - t0
        logging.info(f"[GPT] {r.status_code} | {lat:.2f} s")

        if r.status_code != 200:
            return "Tengo problemas con el servicio de inteligencia artificial en este momento."

        return r.json()["choices"][0]["message"]["content"]

    except Exception as e:
        logging.exception("[GPT] Error")
        return "Hubo un problema con la inteligencia artificial, intenta nuevamente."


# =========================
#  TRANSFERENCIA
# =========================
AGENT_SIP = "sip:6049@nuxway.sip.twilio.com"

def transferir_a_agente(vr: VoiceResponse):
    vr.say(
        "Te voy a comunicar con un agente humano. Por favor espera.",
        language="es-ES", voice="Polly.Lupe"
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

    phase = request.args.get("phase", "initial")  # initial / followup
    attempt = int(request.args.get("attempt", "1"))

    logging.info(f"[IVR] phase={phase} attempt={attempt} speech={speech}")

    vr = VoiceResponse()

    # ================
    # 1. SIN INPUT
    # ================
    if not speech and not digits:

        # Followup: si no responde → colgamos
        if phase == "followup":
            vr.say(
                "No recibí ninguna respuesta. Gracias por comunicarse con Nuxway Technology. Hasta luego.",
                language="es-ES", voice="Polly.Lupe"
            )
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        # Initial: repetir hasta 3 veces
        if attempt >= 3:
            vr.say(
                "No recibí ninguna respuesta. Gracias por comunicarse con Nuxway Technology. Hasta luego.",
                language="es-ES", voice="Polly.Lupe"
            )
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        # Repetir mensaje inicial SIN colgar
        next_attempt = attempt + 1

        gather = Gather(
            input="speech dtmf",
            language="es-ES",
            action=f"/ivr-llm?phase=initial&attempt={next_attempt}",
            method="POST",
            timeout=4,
            speech_timeout="auto"
        )
        gather.say(
            "¡Hola! Soy el Agente de Inteligencia Artificial de Nuxway Technology. "
            "Para comenzar, ¿podrías brindarme tu nombre y el de tu empresa, por favor?",
            language="es-ES",
            voice="Polly.Lupe"
        )

        vr.append(gather)
        return Response(str(vr), mimetype="text/xml")

    # ================
    # 2. Pidió humano
    # ================
    text_lower = (speech or "").lower()

    if digits == "0" or "humano" in text_lower or "agente" in text_lower:
        return transferir_a_agente(vr)

    # ================
    # 3. RESPUESTA GPT
    # ================
    respuesta_gpt = llamar_gpt(speech or "")

    # Si es la PRIMERA interacción → agregar saludo festivo fijo
    if phase == "initial":
        respuesta_gpt += (
            "\n\nQueremos desearle unas felices fiestas de fin de año "
            "de parte de toda la familia Nuxway. Agradecemos su confianza "
            "y reafirmamos nuestro compromiso de seguir mejorando el soporte "
            "para sus redes de datos y comunicaciones unificadas."
        )

    vr.say(respuesta_gpt, language="es-ES", voice="Polly.Lupe")

    # ================
    # 4. FOLLOWUP
    # ================
    gather2 = Gather(
        input="speech dtmf",
        language="es-ES",
        action="/ivr-llm?phase=followup",
        method="POST",
        timeout=4,
        speech_timeout="auto"
    )
    gather2.say(
        "¿Puedo ayudarte en algo más? "
        "Si necesitas un humano, di 'humano' o marca cero. "
        "Si no respondes, finalizaré la llamada.",
        language="es-ES",
        voice="Polly.Lupe"
    )

    vr.append(gather2)
    return Response(str(vr), mimetype="text/xml")


# =========================
#  TEST
# =========================
@app.route("/")
def home():
    return "Nuxway IVR LLM – Soporte IA activo."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)


