from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from openai import OpenAI
import os

app = Flask(__name__)

# La API KEY va como variable de entorno en Render
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    speech = request.values.get("SpeechResult")
    vr = VoiceResponse()

    # 1) Primera vuelta (no hay speech aún)
    if not speech:
        gather = Gather(
            input="speech",
            language="es-ES",
            action="/ivr-llm",
            method="POST",
            timeout=5
        )
        gather.say("Hola, soy un asistente con inteligencia artificial. ¿En qué puedo ayudarte?")
        vr.append(gather)
        vr.say("No escuché nada. Hasta luego.")
        return Response(str(vr), mimetype="text/xml")

    # 2) Ya tenemos SpeechResult → Llamamos a GPT
    respuesta = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "Eres un IVR en español. Responde corto y claro."},
            {"role": "user", "content": speech}
        ]
    ).choices[0].message.content

    vr.say(language="es-ES", voice="Polly.Lupe", text=respuesta)
    return Response(str(vr), mimetype="text/xml")

@app.route("/", methods=["GET"])
def home():
    return "Nuxway IVR LLM - Running!"

if __name__ == "__main__":
    app.run(port=5000)

