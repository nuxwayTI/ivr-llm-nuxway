from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import os
import logging
import requests

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"


# ==========================================================
# FUNCIÓN GPT - ESTA ES LA QUE TIENES QUE ACTUALIZAR
# ==========================================================
def llamar_gpt(user_text: str) -> str:
    """
    Llamada directa a OpenAI usando requests en vez del SDK.
    Esto nos permite ver el código de error y el cuerpo completo.
    """

    if not OPENAI_API_KEY:
        app.logger.error("OPENAI_API_KEY no está configurada en Render.")
        return "Hay un problema interno con la configuración de la inteligencia artificial."

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    data = {
        "model": "gpt-4.1-mini",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Eres un asistente telefónico de Nuxway Technology. "
                    "Respondes siempre en español, de forma breve, clara y amable."
                ),
            },
            {
                "role": "user",
                "content": user_text
            },
        ],
    }

    try:
        resp = requests.post(OPENAI_URL, headers=headers, json=data, timeout=10)

        # Logs CLAVE para ver qué dice OpenAI realmente
        app.logger.info(f"Status OpenAI: {resp.status_code}")
        app.logger.info(f"Cuerpo OpenAI: {resp.text}")

        if resp.status_code != 200:
            return "Tengo un problema con el servicio de inteligencia artificial. Inténtalo más tarde."

        j = resp.json()
        return j["choices"][0]["message"]["content"]

    except requests.exceptions.RequestException as e:
        app.logger.exception("Error de red llamando a OpenAI con requests:")
        return "Estoy teniendo problemas de conexión con la inteligencia artificial. Intenta nuevamente."
    except Exception as e:
        app.logger.exception("Error inesperado procesando respuesta:")
        return "Ocurrió un error interno al procesar la respuesta. Intenta nuevamente."


# ==========================================================
# RUTA PRINCIPAL DEL IVR
# ==========================================================
@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    speech = request.values.get("SpeechResult")
    app.logger.info(f"SpeechResult recibido: {speech}")
    vr = VoiceResponse()

    if not speech:
        gather = Gather(
            input="speech",
            language="es-ES",
            action="/ivr-llm",
            method="POST",
            timeout=5
        )
        gather.say(
            language="es-ES",
            voice="Polly.Lupe",
            text="Hola, soy un asistente de Nuxway Technology con inteligencia artificial. ¿En qué puedo ayudarte?"
        )
        vr.append(gather)
        vr.say(
            language="es-ES",
            voice="Polly.Lupe",
            text="No escuché ninguna respuesta. Hasta luego."
        )
        return Response(str(vr), mimetype="text/xml")

    respuesta = llamar_gpt(speech)
    app.logger.info(f"Respuesta GPT: {respuesta}")

    vr.say(
        language="es-ES",
        voice="Polly.Lupe",
        text=respuesta
    )

    gather2 = Gather(
        input="speech",
        language="es-ES",
        action="/ivr-llm",
        method="POST",
        timeout=5
    )
    gather2.say(
        language="es-ES",
        voice="Polly.Lupe",
        text="¿Puedo ayudarte en algo más?"
    )
    vr.append(gather2)

    return Response(str(vr), mimetype="text/xml")


@app.route("/", methods=["GET"])
def home():
    return "Nuxway IVR LLM - Running!"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)



