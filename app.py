from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather

app = Flask(__name__)

@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    speech = request.values.get("SpeechResult")
    vr = VoiceResponse()

    # 1) PRIMERA VEZ: todavía no hay texto del usuario
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

    # 2) SEGUNDA VEZ: de momento solo repetimos lo que dijo el usuario
    texto = f"Dijiste: {speech}"
    vr.say(language="es-ES", voice="Polly.Lupe", text=texto)
    return Response(str(vr), mimetype="text/xml")


@app.route("/", methods=["GET"])
def home():
    return "Nuxway IVR LLM - Running (sin GPT)."

if __name__ == "__main__":
    app.run(port=5000)

