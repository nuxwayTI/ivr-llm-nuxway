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

# Usamos una sesión global para reciclar conexiones
session = requests.Session()

# =========================
#  CONFIG TRANSFERENCIA A AGENTE / COLA
# =========================
AGENT_SIP = "sip:6049@nuxway.sip.twilio.com"
AGENT_NUMBER = ""  # lo dejamos vacío para no usar PSTN


def llamar_gpt(user_text: str) -> str:
    """
    Llama a la API de OpenAI y devuelve el texto de respuesta.
    Aquí medimos solo la parte del modelo.
    """
    if not OPENAI_API_KEY:
        app.logger.error("OPENAI_API_KEY no está configurada.")
        return "Hay un problema con la configuración de la inteligencia artificial."

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    # Prompt ultra corto y directo para reducir tokens
    data = {
        "model": "gpt-4.1-mini",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Eres un asistente telefónico de Nuxway Technology. "
                    "Respondes en español, muy breve y directo, máximo dos frases."
                ),
            },
            {"role": "user", "content": user_text}
        ],
        # Limitar tokens baja el tiempo de generación
        "max_tokens": 50,   # puedes probar incluso 30
        "temperature": 0.2,
    }

    t0 = time.monotonic()
    try:
        resp = session.post(
            OPENAI_URL,
            headers=headers,
            json=data,
            timeout=8  # menor timeout para evitar cuelgues largos
        )
        t1 = time.monotonic()
        app.logger.info(f"[GPT] Status: {resp.status_code}, Latencia: {t1 - t0:.2f} s")

        if resp.status_code != 200:
            app.logger.info(f"[GPT] Error body: {resp.text[:400]}")
            return "Tengo un problema con el servicio de inteligencia artificial. Inténtalo más tarde."

        j = resp.json()
        respuesta = j["choices"][0]["message"]["content"]
        app.logger.info(f"[GPT] Longitud respuesta: {len(respuesta)} caracteres")
        return respuesta

    except requests.exceptions.Timeout:
        t1 = time.monotonic()
        app.logger.error(f"[GPT] TIMEOUT tras {t1 - t0:.2f} s")
        return "La inteligencia artificial está tardando demasiado en responder. Por favor intenta de nuevo."

    except requests.exceptions.RequestException as e:
        t1 = time.monotonic()
        app.logger.exception(f"[GPT] Error de red tras {t1 - t0:.2f} s: {e}")
        return "Estoy teniendo problemas de conexión con la inteligencia artificial. Intenta nuevamente."

    except Exception as e:
        t1 = time.monotonic()
        app.logger.exception(f"[GPT] Error inesperado tras {t1 - t0:.2f} s: {e}")
        return "Ocurrió un error interno al procesar la respuesta. Intenta nuevamente."


def transferir_a_agente(vr: VoiceResponse) -> Response:
    """
    Genera TwiML para transferir a un agente humano / cola en la PBX.
    """
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


@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    t_inicio = time.monotonic()

    speech = request.values.get("SpeechResult")
    digits = request.values.get("Digits")

    app.logger.info(f"[IVR] SpeechResult: {speech}")
    app.logger.info(f"[IVR] Digits: {digits}")

    vr = VoiceResponse()

    # 1) Primera vuelta: pedir mensaje o DTMF
    if not speech and not digits:
        gather = Gather(
            input="speech dtmf",
            num_digits=1,
            language="es-ES",
            action="/ivr-llm",
            method="POST",
            timeout=3  # mantén esto bajo para no sumar espera
        )
        gather.say(
            "Hola, soy un asistente de Nuxway Technology con inteligencia artificial. "
            "Dime en pocas palabras cómo puedo ayudarte. "
            "Si quieres hablar con un agente humano, di 'agente' o presiona cero.",
            language="es-ES",
            voice="Polly.Lupe",
        )
        vr.append(gather)

        vr.say(
            "No escuché ninguna respuesta. Hasta luego.",
            language="es-ES",
            voice="Polly.Lupe",
        )

        t_fin = time.monotonic()
        app.logger.info(f"[IVR] Sin input, handler tomó: {t_fin - t_inicio:.2f} s")
        return Response(str(vr), mimetype="text/xml")

    # 2) Detectar si pidió humano
    texto = (speech or "").lower()
    if (digits == "0") or ("agente" in texto) or ("humano" in texto):
        app.logger.info("[IVR] Usuario pidió agente humano.")
        t_fin = time.monotonic()
        app.logger.info(f"[IVR] Tiempo hasta transferir a agente: {t_fin - t_inicio:.2f} s")
        return transferir_a_agente(vr)

    # 3) GPT para conversación normal
    t_gpt_ini = time.monotonic()
    respuesta = llamar_gpt(speech or "")
    t_gpt_fin = time.monotonic()
    app.logger.info(f"[IVR] llamar_gpt() tardó: {t_gpt_fin - t_gpt_ini:.2f} s")
    app.logger.info(f"[IVR] Respuesta GPT: {respuesta}")

    vr.say(
        respuesta,
        language="es-ES",
        voice="Polly.Lupe",
    )

    # 4) Segundo gather para continuar
    gather2 = Gather(
        input="speech dtmf",
        num_digits=1,
        language="es-ES",
        action="/ivr-llm",
        method="POST",
        timeout=3
    )
    gather2.say(
        "¿Puedo ayudarte en algo más? "
        "Recuerda que si quieres un humano puedes decir 'agente' o marcar cero.",
        language="es-ES",
        voice="Polly.Lupe",
    )
    vr.append(gather2)

    t_fin = time.monotonic()
    app.logger.info(f"[IVR] Handler /ivr-llm total: {t_fin - t_inicio:.2f} s")

    return Response(str(vr), mimetype="text/xml")


@app.route("/", methods=["GET"])
def home():
    return "Nuxway IVR LLM - Running (low-latency)!"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

