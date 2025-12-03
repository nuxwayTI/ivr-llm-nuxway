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

# Sesión HTTP persistent (reduce 300–500 ms)
session = requests.Session()

# =========================
#  CONFIG TRANSFERENCIA
# =========================
AGENT_SIP = "sip:6049@nuxway.sip.twilio.com"
AGENT_NUMBER = ""


# =========================
#  GPT LLAMADA OPTIMIZADA
# =========================
def llamar_gpt(user_text: str) -> str:
    if not OPENAI_API_KEY:
        return "Error interno: API KEY no configurada."

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    # ⚡ Versión optimizada para velocidad
    data = {
        "model": "gpt-4.1-nano",   # ⚡ ultra rápido
        "messages": [
            {
                "role": "system",
                "content": (
                    "Eres un asistente telefónico de Nuxway Technology. "
                    "Responde SIEMPRE en español, máximo 20 palabras, directo, claro."
                ),
            },
            {"role": "user", "content": user_text}
        ],
        "max_tokens": 30,
        "temperature": 0.2,
    }

    t0 = time.monotonic()
    try:
        resp = session.post(
            OPENAI_URL,
            headers=headers,
            json=data,
            timeout=5   # reduce tiempo de espera
        )
        latency = time.monotonic() - t0

        logging.info(f"[GPT] {resp.status_code} | Latencia: {latency:.2f} s")

        if resp.status_code != 200:
            return "Hubo un problema interno, intenta nuevamente."

        respuesta = resp.json()["choices"][0]["message"]["content"]
        return respuesta.strip()

    except Exception as e:
        logging.error(f"[GPT] Error: {e}")
        return "Error al procesar la inteligencia artificial."


# =========================
#  TRANSFERIR A AGENTE
# =========================
def transferir_a_agente(vr: VoiceResponse) -> Response:
    vr.say(
        "Te comunico con un agente humano. Por favor espera.",
        language="es-ES",
        voice="Polly.Lupe",
    )

    dial = vr.dial()
    if AGENT_SIP:
        dial.sip(AGENT_SIP)

    return Response(str(vr), mimetype="text/xml")


# =========================
#  IVR PRINCIPAL
# =========================
@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    t_inicio = time.monotonic()

    speech = request.values.get("SpeechResult")
    digits = request.values.get("Digits")

    logging.info(f"[IVR] Speech: {speech}")
    logging.info(f"[IVR] Digits: {digits}")

    vr = VoiceResponse()

    # Primera interacción: pedir mensaje
    if not speech and not digits:
        gather = Gather(
            input="speech dtmf",
            num_digits=1,
            language="es-ES",
            action="/ivr-llm",
            method="POST",
            timeout=2    # ⚡ más rápido
        )
        gather.say(
            "Hola, soy el asistente de Nuxway. ¿Cómo puedo ayudarte? "
            "Di agente o marca cero para humano.",
            language="es-ES",
            voice="Polly.Lupe",
        )
        vr.append(gather)

        vr.say(
            "No escuché tu respuesta. Gracias por llamar.",
            language="es-ES",
            voice="Polly.Lupe"
        )
        return Response(str(vr), mimetype="text/xml")

    # Detectar agente humano
    texto = (speech or "").lower()
    if digits == "0" or "agente" in texto or "humano" in texto:
        return transferir_a_agente(vr)

    # Llamar a GPT
    t_gpt_ini = time.monotonic()
    respuesta = llamar_gpt(speech or "")
    t_gpt_fin = time.monotonic()

    logging.info(f"[GPT] tiempo: {t_gpt_fin - t_gpt_ini:.2f}s | resp: {respuesta}")

    vr.say(respuesta, language="es-ES", voice="Polly.Lupe")

    # Segundo turno
    gather2 = Gather(
        input="speech dtmf",
        num_digits=1,
        language="es-ES",
        action="/ivr-llm",
        method="POST",
        timeout=2
    )
    gather2.say(
        "¿Puedo ayudarte con algo más?",
        language="es-ES",
        voice="Polly.Lupe",
    )
    vr.append(gather2)

    logging.info(f"[IVR] Handler total: {time.monotonic() - t_inicio:.2f} s")

    return Response(str(vr), mimetype="text/xml")


# =========================
#  TEST DE LATENCIA
# =========================
@app.route("/test-gpt", methods=["GET"])
def test_gpt():
    t0 = time.monotonic()
    respuesta = llamar_gpt("¿Qué es Nuxway Technology?")
    t1 = time.monotonic()

    return (
        f"GPT: {respuesta}\n"
        f"Tiempo total: {t1 - t0:.2f} s\n"
    )


@app.route("/", methods=["GET"])
def home():
    return "Nuxway IVR LLM optimizado ⚡"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

