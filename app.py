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
session = requests.Session()  # mantener conexiones abiertas


# =========================
# PROMPT DEL AGENTE IA
# =========================
SYSTEM_PROMPT = """
Eres el Agente con Inteligencia Artificial de Nuxway Technology.
Respondes SOLO en español.

Tu estilo:
- Profesional, claro y amable.
- Frases cortas y pausadas, adecuadas para una llamada telefónica.
- Siempre empático y cordial.

Al inicio de la PRIMERA interacción con el cliente (cuando aún no conoces su nombre):
1) Preséntate brevemente como el Agente de Inteligencia Artificial de Nuxway Technology.
2) Felicita por las fiestas de Navidad y Año Nuevo.
3) Pide el nombre de la persona y el de su empresa.
4) Incluye EXACTAMENTE una vez este mensaje (en algún momento del saludo inicial o inmediatamente después):

"Queremos desearle unas felices fiestas de fin de año de parte de toda la familia Nuxway. Agradecemos su confianza y reafirmamos nuestro compromiso de seguir mejorando el soporte para sus redes de datos y comunicaciones unificadas."

Contexto del rol:
- Ayudas en temas de redes de datos, comunicaciones unificadas, servicios e implementaciones de Nuxway y soporte técnico.
- Haces preguntas para entender la situación del cliente.
- Acompañas paso a paso.
- Siempre ofreces derivar a un humano si el cliente lo pide o si el caso lo requiere.

Guardrails:
- No inventes información técnica.
- Si no sabes algo, dilo honestamente y sugiere escalar a un humano.
- No compartas datos sensibles.
- Mantén siempre un tono respetuoso y profesional, incluso si el cliente está frustrado.
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
    attempt = int(request.args.get("attempt", "1"))

    logging.info(f"[IVR] phase={phase} attempt={attempt} speech={speech} digits={digits}")

    vr = VoiceResponse()

    # =====================================================
    # 1. DETECCIÓN DE SILENCIO (NO HABLÓ NADA)
    # =====================================================
    no_hablo = (speech is None) or (speech.strip() == "")

    if no_hablo:
        # FOLLOWUP → cuelga directo si no responde
        if phase == "followup":
            vr.say(
                "No recibí ninguna respuesta. Gracias por comunicarse con Nuxway Technology. Hasta luego.",
                language="es-ES",
                voice="Polly.Lupe"
            )
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        # INITIAL → 2 repeticiones, en la 3 cuelga
        if attempt >= 3:
            vr.say(
                "No escuché ninguna respuesta. Muchas gracias por comunicarse con Nuxway Technology. Hasta luego.",
                language="es-ES",
                voice="Polly.Lupe"
            )
            vr.hangup()
            return Response(str(vr), mimetype="text/xml")

        next_attempt = attempt + 1

        # Mensaje inicial del IVR (ahora neutro, el branding lo hace GPT)
        base_msg = (
            "Por favor, indica tu nombre y el de tu empresa después de este mensaje."
        )

        if attempt == 1:
            mensaje_inicial = base_msg
        else:
            # attempt == 2 → lo repite explícitamente
            mensaje_inicial = (
                "Parece que no logré escucharte. Te repito nuevamente la instrucción. "
                + base_msg
            )

        gather = Gather(
            input="speech",  # solo speech para que Twilio maneje bien silencios
            language="es-ES",
            action=f"/ivr-llm?phase=initial&attempt={next_attempt}",
            method="POST",
            timeout=7,          # tiempo total para que el usuario hable
            speech_timeout="3"  # 3 segundos de silencio antes de cortar el speech
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
    # 3. GPT RESPONDE (CON SALUDO Y TODO SEGÚN PROMPT)
    # =====================================================
    respuesta_gpt = llamar_gpt(speech or "")

    vr.say(respuesta_gpt, language="es-ES", voice="Polly.Lupe")

    # =====================================================
    # 4. FOLLOWUP: CONTINUAR O COLGAR SI NO HABLA
    # =====================================================
    gather2 = Gather(
        input="speech",
        language="es-ES",
        action="/ivr-llm?phase=followup",
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



