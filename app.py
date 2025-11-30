from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from openai import OpenAI
import os
import logging

# Logs visibles en Render
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# Cliente OpenAI con la API key de Render
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url="https://api.openai.com/v1"   # IMPORTANTE: evita APIConnectionError
)

@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    speech = request.values.get("SpeechResult")
    app.logger.info(f"SpeechResult recibido: {speech}")
    vr = VoiceResponse()

    # 1) Primera vuelta: Twilio aún no tiene texto
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
            text="Hola, soy un asistente de Nuxway Technology. ¿En qué puedo ayudarte?"
        )
        vr.append(gather)

        vr.say(
            language="es-ES",
            voice="Polly.Lupe",
            text="No escuché ninguna respuesta. Hasta luego."
        )
        return Response(str(vr), mimetype="text/xml")

    # 2) Ya tenemos la frase del usuario → GPT
    try:
        completion = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres un IVR inteligente de Nuxway Technology. "
                        "Respondes corto, claro, amable y siempre en español."
                    )
                },
                {"role": "user", "content": speech}
            ]
        )
        respuesta = completion.choices[0].message.content
    except Exception as e:
        # Si GPT falla, evita que se caiga Twilio
        app.logger.exception("ERROR llamando a GPT:")
        respuesta = (
            "Estoy experimentando problemas para conectarme con la inteligencia "
            "artificial. Intenta nuevamente en unos momentos."
        )

    # Log de la respuesta
    app.logger.info(f"Respuesta GPT: {respuesta}")

    # Responderle al usuario
    vr.say(
        language="es-ES",
        voice="Polly.Lupe",
        text=respuesta
    )

    # 3) Volver a escuchar al usuario para seguir conversando
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



