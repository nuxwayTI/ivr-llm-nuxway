from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from openai import OpenAI
import os

app = Flask(__name__)

# Cliente de OpenAI usando la variable de entorno
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    speech = request.values.get("SpeechResult")
    vr = VoiceResponse()

    # 1) PRIMERA VEZ: todavía no hay texto del usuario
    if not speech:
        gather = Gather(
            input="speech",
            language="es-ES",
            action="/ivr-llm",   # Twilio vuelve a llamar aquí
            method="POST",
            timeout=5
        )
        gather.say(
            language="es-ES",
            voice="Polly.Lupe",
            text="Hola, soy un asistente con inteligencia artificial. ¿En qué puedo ayudarte?"
        )
        vr.append(gather)

        # Si no dice nada
        vr.say(
            language="es-ES",
            voice="Polly.Lupe",
            text="No escuché ninguna respuesta. Hasta luego."
        )
        return Response(str(vr), mimetype="text/xml")

    # 2) SEGUNDA VEZ: Twilio ya nos mandó lo que dijo el usuario en SpeechResult
    user_text = speech
    print("Usuario dijo:", user_text)

    # ---- AQUÍ ENTRA GPT ----
    completion = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "Eres un asistente telefónico de Nuxway Technology. "
                    "Respondes en español, de forma breve, clara y amable. "
                    "Si no entiendes algo, pide que lo repitan o aclaren."
                )
            },
            {"role": "user", "content": user_text}
        ]
    )
    respuesta_llm = completion.choices[0].message.content

    # Respuesta al usuario por voz
    vr.say(
        language="es-ES",
        voice="Polly.Lupe",
        text=respuesta_llm
    )

    # Si quisieras seguir conversando en loop, aquí podrías agregar otro Gather.
    return Response(str(vr), mimetype="text/xml")


@app.route("/", methods=["GET"])
def home():
    return "Nuxway IVR LLM - Running!"

if __name__ == "__main__":
    app.run(port=5000)
