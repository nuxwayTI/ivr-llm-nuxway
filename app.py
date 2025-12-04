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
session = requests.Session()  # mantener conexiones HTTP


# =========================
# PROMPT DEL AGENTE IA
# =========================
SYSTEM_PROMPT = """
Eres el Agente de Inteligencia Artificial de Nuxway Technology.
Respondes SOLO en español.

En tu PRIMER mensaje al cliente debes seguir SIEMPRE esta estructura:

1) Preséntate claramente, por ejemplo:
   "Hola, soy el Agente de Inteligencia Artificial de Nuxway Technology."
2) Felicita brevemente por las fiestas de Navidad y Año Nuevo.
3) Explica en una frase que estás para ayudar con soporte de redes, comunicaciones unificadas y servicios de Nuxway.
4) Luego pide de forma amable el nombre de la persona y el de su empresa.
5) Incluye EXACTAMENTE una vez este mensaje (puede ser en ese mismo saludo o justo después):
   "Queremos desearle unas felices fiestas de fin de año de parte de toda la familia Nuxway. Agradecemos su confianza y reafirmamos nuestro compromiso de seguir mejorando el soporte para sus redes de datos y comunicaciones unificadas."

En mensajes posteriores:
- Responde de forma clara, breve y profesional.
- Adapta tu nivel técnico al del cliente.
- Usa un tono empático, cordial y tranquilo.
- Puedes hacer preguntas para entender mejor el problema.
- Siempre que el cliente lo pida o lo veas necesario, ofrece derivar a un agente humano.

Guardrails:
- No inventes información técnica.
- Si no sabes algo, dilo honestamente y sugiere escalar a un humano.
- No compartas datos sensibles.
- Mantén siempre un tono respetuoso y profesional.
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
        "max_tokens": 80,
        "temperature": 0.2,
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
    attempt_param = request.args.get("attempt")   # None en la PRIMERA vez
    attempt = int(attempt_param) if attempt_param is not None else None

    logging.info(f"[IVR] phase={phase} attempt={attempt} speech={speech} digits={digits}")

    vr = VoiceResponse()

    # =====================================================
    # 0. PRIMERA ENTRADA DESDE TWILIO (SIN attempt)
    # =====================================================
    if phase == "initial" and attempt is None:
        # Primer contacto: siempre mostramos el mensaje inicial bonito
        # y empezamos contador attempt=1 en la siguiente vuelta.
        gather = Gather(
            input="speech",
            language="es-ES",
            action="/ivr-llm?phase=initial&attempt=1",
            method="POST",
            timeout=7,
            speech_timeout="3"
        )
        gather.say(
            "¡Hola! Soy el Agente de Inteligencia Artificial de Nuxway Technology. "
            "Para comenzar, por favor dime tu nombre y el de tu empresa después de este mensaje.",
            language="es-ES",
            voice="Polly.Lupe"
        )
        vr.append(gather)
        return Response(str(vr), mimetype="text/xml")

    # A partir de aquí, attempt SIEMPRE tiene un entero (1, 2, 3…)
    if attempt is None:
        attempt = 1  # fallback por si acaso

    # =====================================================
    # 1. DETECTAR SILENCIO (NO HABLÓ NADA)
    # =====================================================
    no_hablo = (speech is None) or (speech.strip() == "")

    if no_hablo:
        # FOLLOWUP: si no habla en followup, colgamos directamente
        if phase == "followup":
            vr.say(
                "No recibí ninguna respuesta. Gracias por comunicarse con Nuxway Technology. Hasta luego.",
                language="es-ES",
                voice="Polly.Lupe"
            )
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        # INITIAL: queremos máximo 2 repeticiones, en la tercera colgamos
        if attempt >= 3:
            vr.say(
                "No escuché ninguna respuesta. Muchas gracias por comunicarse con Nuxway Technology. Hasta luego.",
                language="es-ES",
                voice="Polly.Lupe"
            )
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        next_attempt = attempt + 1

        # Mensaje inicial (se repite si no habló)
        if attempt == 1:
            mensaje_inicial = (
                "Por favor, dime tu nombre y el de tu empresa después de este mensaje."
            )
        else:  # attempt == 2
            mensaje_inicial = (
                "Parece que no logré escucharte. Te repito nuevamente la instrucción. "
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
        gather.say(mensaje_inicial, language="es-ES", voice="Polly.Lupe")
        vr.append(gather)
        return Response(str(vr), mimetype="text/xml")

    # =====================================================
    # 2. PIDIÓ HUMANO (DTMF O VOZ)
    # =====================================================
    text_lower = (speech or "").lower()

    if digits == "0" or "humano" in text_lower or "agente" in text_lower:
        return transferir_a_agente(vr)

    # =====================================================
    # 3. GPT RESPONDE (PRESENTACIÓN, FIESTAS, ETC.)
    # =====================================================
    respuesta_gpt = llamar_gpt(speech or "")
    vr.say(respuesta_gpt, language="es-ES", voice="Polly.Lupe")

    # =====================================================
    # 4. FOLLOWUP: CONTINUAR O COLGAR SI NO HABLA
    # =====================================================
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



