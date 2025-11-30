from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from openai import OpenAI
import os
import logging

# Logs para ver qué pasa en Render
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# Cliente OpenAI - usa la variable de entorno OPENAI_API_KEY
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)

@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    speech = request.values.get("SpeechResult")
    app.logger.info(f"SpeechResult recibido: {speech}")
    vr = VoiceResponse()

    # 1) Primera vuelta: todavía no hay texto del usuario
    if not speech:
        gather = Gather(
            input="speech",
            language="es-ES",
            action="/ivr-llm",   # Twilio volverá a llamar a esta misma ruta
            method="POST",
            timeout=5
        )
        gather.say(
            language="es-ES",
            voice="Polly.Lupe",
            text="Hola, soy un asistente de Nuxway Technology con inteligencia artificial. ¿En qué puedo ayudarte?"
        )
        vr.append(gather)

        # Si el usuario no dice nada
        vr.say(
            language="es-ES",
            voice="Polly.Lupe",
            text="No escuché ninguna respuesta. Hasta luego."
        )
        return Response(str(vr), mimetype="text/xml")

    # 2) Ya tenemos SpeechResult → llamamos al LLM
    try:
        completion = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres un asistente telefónico de Nuxway Technology. "
                        "Respondes siempre en español, corto, claro y amable. "
                        "Hablas como IVR: frases simples, sin párrafos largos."
                    )
                },
                {"role": "user", "content": speech}
            ]
        )
        respuesta = completion.choices[0].message.content
    except Exception as e:
        app.logger.exception("Error llamando a OpenAI")
        respuesta = (
            "En este momento tengo problemas para conectarme "
            "al motor de inteligencia artificial. Intenta de nuevo más tarde."
        )

    app.logger.info(f"Respuesta LLM: {respuesta}")

    # Respuesta hablada al usuario
    vr.say(
        language="es-ES",
        voice="Polly.Lupe",
        text=respuesta
    )

    # 3) Segundo gather para seguir conversando (opcional)
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
    # Para pruebas locales
    app.run(host="0.0.0.0", port=5000, debug=True)



