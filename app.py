from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from openai import OpenAI

app = Flask(__name__)

client = OpenAI(api_key="TU_API_KEY")

@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    speech = request.values.get("SpeechResult")
    vr = VoiceResponse()

    if not speech:
        gather = Gather(
            input="speech",
            language="es-ES",
            action="/ivr-llm",
            method="POST",
            timeout=5
        )
        gather.say("Hola, soy un asistente con inteligencia artificial. ¿Cuál es tu consulta?")
        vr.append(gather)
        vr.say("No escuché nada. Adiós.")
        return Response(str(vr), mimetype="text/xml")

    # --- AQUÍ USAS GPT ---
    respuesta = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role":"system","content":"Eres un IVR en español, responde corto y claro."},
            {"role":"user","content":speech}
        ]
    ).choices[0].message.content

    vr.say(language="es-ES", text=respuesta)
    return Response(str(vr), mimetype="text/xml")

if __name__ == "__main__":
    app.run(port=5000)
