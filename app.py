from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from openai import OpenAI
import os

app = Flask(__name__)

# La API KEY va como variable de entorno en Render (OPENAI_API_KEY)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# URL pública de tu webhook en Render
URL_WEBHOOK = "https://ivr-llm-nuxway.onrender.com/ivr-llm"

@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    # Texto que reconoce Twilio del audio del usuario
    speech = request.values.get("SpeechResult")
    vr = VoiceResponse()

    # ---------- 1) PRIMERA VEZ: NO HAY SPEECH ----------
    if not speech:
        gather = Gather(
            input="speech",
            language="es-ES",
            action=URL_WEBHOOK,   # Twilio volverá a llamar aquí
            method="POST",
            timeout=5
        )
        gather.say(
            "Hola, soy un asistente con inteligencia artificial de Nuxway Technology. "
            "¿En qué puedo ayudarte?"
        )
        vr.append(gather)

        # Si el usuario no dice nada dentro del timeout
        vr.say("No escuché ninguna respuesta. Hasta luego.")
        return Response(str(vr), mimetype="text/xml")

    # ---------- 2) SEGUNDA VEZ: YA TENEMOS TEXTO ----------
    print("Usuario dijo:", speech)

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
    print("GPT responde:", respuesta)

    # Respuesta hablada al usuario
    vr.say(respuesta, language="es-ES", voice="Polly.Lupe")

    # ---------- 3) OPCIONAL: SEGUIR CONVERSANDO ----------
    gather2 = Gather(
        input="speech",
        language="es-ES",
        action=URL_WEBHOOK,
        method="POST",
        timeout=5
    )
    gather2.say("¿Puedo ayudarte en algo más?")
    vr.append(gather2)

    return Response(str(vr), mimetype="text/xml")


@app.route("/", methods=["GET"])
def home():
    return "Nuxway IVR LLM - Running!"

if __name__ == "__main__":
    app.run(port=5000)


