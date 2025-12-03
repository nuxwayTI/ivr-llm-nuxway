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

# Sesión HTTP persistente (reduce latencia de red)
session = requests.Session()

# =========================
#  CONFIG TRANSFERENCIA
# =========================
AGENT_SIP = "sip:6049@nuxway.sip.twilio.com"
AGENT_NUMBER = ""  # lo dejamos vacío para no usar PSTN


# =========================
#  GPT LLAMADA OPTIMIZADA
# =========================
def llamar_gpt(user_text: str) -> str:
    if not OPENAI_API_KEY:
        logging.error("OPENAI_API_KEY no está configurada.")
        return "Hay un problema con la configuración de la inteligencia artificial."

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    data = {
        "model": "gpt-4.1-nano",   # modelo muy rápido
        "messages": [
            {
                "role": "system",
                "content": (
                    "Eres un asistente telefónico de Nuxway Technology. "
                    "Respondes siempre en español, muy breve y directo, "
                    "máximo 20 palabras, sin listas ni saltos de línea."
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
            timeout=5  # no esperamos más de 5 s al modelo
        )
        latency = time.monotonic() - t0
        logging.info(f"[GPT] Status: {resp.status_code} | Latencia: {latency:.2f} s")

        if resp.status_code != 200:
            logging.info(f"[GPT] Error body: {resp.text[:400]}")
            return "Tengo un problema con el servicio de inteligencia artificial. Inténtalo más tarde."

        j = resp.json()
        respuesta = j["choices"][0]["message"]["content"].strip()
        logging.info(f"[GPT] Longitud respuesta: {len(respuesta)} caracteres")
        return respuesta

    except requests.exceptions.Timeout:
        latency = time.monotonic() - t0
        logging.error(f"[GPT] TIMEOUT tras {latency:.2f} s")
        return "La inteligencia artificial está tardando demasiado en responder. Por favor intenta de nuevo."

    except requests.exceptions.RequestException as e:
        latency = time.monotonic() - t0
        logging.exception(f"[GPT] Error de red tras {latency:.2f} s: {e}")
        return "Estoy teniendo problemas de conexión con la inteligencia artificial. Intenta nuevamente."

    except Exception as e:
        latency = time.monotonic() - t0
        logging.exception(f"[GPT] Error inesperado tras {latency:.2f} s: {e}")
        return "Ocurrió un error interno al procesar la respuesta. Intenta nuevamente."


# =========================
#  TRANSFERIR A AGENTE
# =========================
def transferir_a_agente(vr: VoiceResponse) -> Response:
    vr.say(
        "Te voy a comunicar con un agente humano. Por favor espera.",
        language="es-ES",
        voice="Polly.Lupe",
    )

    if AGENT_SIP and AGENT_SIP.startswith("sip:"):
        dial = vr.dial()
        dial.sip(AGENT_SIP)
    elif AGENT_NUMBER:
        dial = vr.dial()
        dial.number(AGENT_NUMBER)
    else:
        vr.say(
            "No tengo un destino configurado para agentes en este momento.",
            language="es-ES",
            voice="Polly.Lupe"
        )

    return Response(str(vr), mimetype="text/xml")


# =========================
#  IVR PRINCIPAL
# =========================
@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    """
    Webhook que Twilio llama con SpeechResult / Digits.
    """
    t_inicio = time.monotonic()

    speech = request.values.get("SpeechResult")
    digits = request.values.get("Digits")

    logging.info(f"[IVR] SpeechResult: {speech}")
    logging.info(f"[IVR] Digits: {digits}")

    vr = VoiceResponse()

    # 1) Primera vuelta: pedir mensaje o DTMF
    if not speech and not digits:
        gather = Gather(
            input="speech dtmf",
            num_digits=1,
            language="es-ES",
            action="/ivr-llm",
            method="POST",
            timeout=4,            # ⬅️ más tiempo para que el usuario hable
            speech_timeout="auto" # ⬅️ Twilio decide fin de discurso
        )
        gather.say(
            "Hola, soy un asistente de Nuxway Technology con inteligencia artificial. "
            "Dime en pocas palabras cómo puedo ayudarte. "
            "Si quieres hablar con un agente humano, di 'agente' o presiona cero.",
            language="es-ES",
            voice="Polly.Lupe",
        )
        vr.append(gather)

        # Este mensaje solo se ejecuta si no hubo ningún input
        vr.say(
            "No escuché ninguna respuesta. Hasta luego.",
            language="es-ES",
            voice="Polly.Lupe",
        )

        t_fin = time.monotonic()
        logging.info(f"[IVR] Sin input, handler tomó: {t_fin - t_inicio:.2f} s")
        return Response(str(vr), mimetype="text/xml")

    # 2) Detectar si pidió humano
    texto = (speech or "").lower()
    if (digits == "0") or ("agente" in texto) or ("humano" in texto):
        logging.info("[IVR] Usuario pidió agente humano.")
        t_fin = time.monotonic()
        logging.info(f"[IVR] Tiempo hasta transferir a agente: {t_fin - t_inicio:.2f} s")
        return transferir_a_agente(vr)

    # 3) GPT para conversación normal
    t_gpt_ini = time.monotonic()
    respuesta = llamar_gpt(speech or "")
    t_gpt_fin = time.monotonic()
    logging.info(f"[IVR] llamar_gpt() tardó: {t_gpt_fin - t_gpt_ini:.2f} s")
    logging.info(f"[IVR] Respuesta GPT: {respuesta}")

    vr.say(
        respuesta,
        language="es-ES",
        voice="Polly.Lupe",
    )

    # 4) Segundo gather para continuar la conversación
    gather2 = Gather(
        input="speech dtmf",
        num_digits=1,
        language="es-ES",
        action="/ivr-llm",
        method="POST",
        timeout=4,             # también más cómodo aquí
        speech_timeout="auto"
    )
    gather2.say(
        "¿Puedo ayudarte en algo más? "
        "Recuerda que si quieres un humano puedes decir 'agente' o marcar cero.",
        language="es-ES",
        voice="Polly.Lupe",
    )
    vr.append(gather2)

    t_fin = time.monotonic()
    logging.info(f"[IVR] Handler /ivr-llm total: {t_fin - t_inicio:.2f} s")

    return Response(str(vr), mimetype="text/xml")


# =========================
#  TEST DE LATENCIA DIRECTA
# =========================
@app.route("/test-gpt", methods=["GET"])
def test_gpt():
    """
    Endpoint de prueba para medir solo Render + GPT, sin Twilio.
    """
    t0 = time.monotonic()
    respuesta = llamar_gpt("Responde en una frase: ¿qué es Nuxway Technology?")
    t1 = time.monotonic()
    return (
        f"Respuesta GPT: {respuesta}\n"
        f"Tiempo total en servidor (Render + GPT): {t1 - t0:.2f} s\n"
    )


@app.route("/", methods=["GET"])
def home():
    return "Nuxway IVR LLM - Running (latencia equilibrada)!"


if __name__ == "__main__":
    # Para local está bien debug=True. En Render normalmente no.
    app.run(host="0.0.0.0", port=5000, debug=True)


