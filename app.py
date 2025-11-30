from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather

app = Flask(__name__)

@app.route("/ivr-llm", methods=["GET", "POST"])
def ivr_llm():
    speech = request.values.get("SpeechResult")
    vr = VoiceResponse()

    # Si todavía no hay SpeechResult (primera vez)
    if not speech:
        gather = Gather(
            input="speech",
            language="es-ES",
            action="/ivr-llm",
            method="POST",
            timeout=6
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

    # Si ya hay SpeechResult, solo repetimos lo que dijo el usuario
    texto = f"Dijiste: {speech}"
    vr.say(language="es-ES", voice="Polly.Lupe", text=texto)
    return Response(str(vr), mimetype="text/xml")


@app.route("/", methods=["GET"])
def home():
    return "Nuxway IVR LLM - Running (test sin GPT)."

if __name__ == "__main__":
    app.run(port=5000)


