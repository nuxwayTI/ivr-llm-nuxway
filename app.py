from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import os
import logging
import requests
import json

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

def llamar_gpt(user_text: str) -> str:
    """
    Llama directamente a la API de OpenAI usando requests,
    sin usar la librería openai para evitar problemas de httpx/httpcore.
    """
    if not OPENAI_API_KEY:
        app.logger.error("OPENAI_API_KEY no está configurada")
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
                    "Respondes siempre en español, de forma breve, clara y amable. "
                    "Hablas como un IVR, con frases cortas y fáciles de entender."
                ),
            },
            {
                "role": "user",
                "content": user_text,
            },
        ],
    }

    try:
        resp = requests.post(OPENAI_URL, headers=headers, json=data, timeout=10)
        app.logger.info(f"Respuesta HTTP de OpenAI: {resp.status_code}")
        app.logger.debug(f"Cuerpo de respuesta: {resp.text}")

        resp.raise_for_status()
        j = resp.json()
        return j["choices"][0]["message"]["content"]
    except requests.exceptions.RequestException as e:
        app.logger.exception("Error de red llamando a OpenAI con requests:")
        return "Estoy teniendo problemas de conexión con la inteligencia artificial. Intenta nuevamente en unos momentos."
    except Exception as e:
        app.logger.exception("Error inesperado procesando la respuesta de OpenAI:")
        return "Ocurrió un error interno al procesar la respuesta. Intenta nuevamente en unos momentos."


@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    speech = request.values.get("SpeechResult")
    app.logger.info(f"SpeechResult recibido: {speech}")
    vr = VoiceResponse()

    # 1) Primera vuelta: todavía no tenemos lo que dijo el usuario
    if not speech:
        gather = Gather(
            input="speech",
            language="es-ES",
            action="/ivr-llm",
            method="POST",
            timeout=5,
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

    # 2) Ya tenemos texto → llamamos a GPT vía requests
    respuesta = llamar_gpt(speech)
    app.logger.info(f"Respuesta GPT: {respuesta}")

    vr.say(
        language="es-ES",
        voice="Polly.Lupe",
        text=respuesta
    )

    # 3) Segundo gather para seguir conversando
    gather2 = Gather(
        input="speech",
        language="es-ES",
        action="/ivr-llm",
        method="POST",
        timeout=5,
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



