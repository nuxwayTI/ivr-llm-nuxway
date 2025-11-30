from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather

app = Flask(__name__)

@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    speech = request.values.get("SpeechResult")
    vr = VoiceResponse()

    # 1) Primera vuelta (no hay speech aún) -> pedimos que hable
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
            text="Hola, soy un asistente con inteligencia artificial. ¿En qué puedo ayudarte?"
        )
        vr.append(gather)

        vr.say(
            language="es-ES",
            voice="Polly.Lupe",
            text="No escuché ninguna respuesta. Hasta luego."
        )
        return Response(str(vr), mimetype="text/xml")

    # 2) Segunda vuelta: ya tenemos SpeechResult -> responder SIN GPT
    respuesta = f"Ok, te escuché decir: {speech}"
    vr.say(language="es-ES", voice="Polly.Lupe", text=respuesta)

    return Response(str(vr), mimetype="text/xml")


@app.route("/", methods=["GET"])
def home():
    return "Nuxway IVR LLM - Running! (prueba sin GPT)"

if __name__ == "__main__":
    app.run(port=5000)
