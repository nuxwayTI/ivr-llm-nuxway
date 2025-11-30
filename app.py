from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather

app = Flask(__name__)

@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    speech = request.values.get("SpeechResult")
    vr = VoiceResponse()

    # 1) PRIMERA VUELTA: no hay speech aún -> pedir que hable
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
            text="No escuché nada. Hasta luego."
        )
        return Response(str(vr), mimetype="text/xml")

    # 2) SEGUNDA VUELTA: ya tenemos SpeechResult -> responder sin GPT
    respuesta = f"Ok, te escuché decir: {speech}"
    vr.say(language="es-ES", voice="Polly.Lupe", text=respuesta)

    return Response(str(vr), mimetype="text/xml")


@app.route("/", methods=["GET"])
def home():
    return "Nuxway IVR LLM - Running! (sin GPT, modo prueba SpeechResult)"

if __name__ == "__main__":
    app.run(port=5000)
