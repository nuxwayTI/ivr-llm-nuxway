from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from openai import OpenAI

app = Flask(__name__)

# ---- CONFIGURA AQUÍ TU API KEY DE OPENAI ----
client = OpenAI(api_key="TU_API_KEY_DE_OPENAI")


@app.route("/ivr-llm", methods=["POST"])
def ivr_llm():
    # Este parámetro lo envía Twilio cuando ya escuchó al usuario
    speech_result = request.values.get("SpeechResult")

    vr = VoiceResponse()

    # 1) Primera vez: aún no hay SpeechResult → pedimos que hable
    if not speech_result:
        gather = Gather(
            input="speech",
            language="es-ES",       # reconocimiento en español
            action="/ivr-llm",      # Twilio volverá a llamar a esta misma URL
            method="POST",
            timeout=6
        )
        gather.say(
            language="es-ES",
            voice="Polly.Lupe",
            text="Hola, soy un asistente con inteligencia artificial. "
                 "Por favor, explícanos brevemente en qué podemos ayudarte."
        )
        vr.append(gather)

        # Si no dice nada
        vr.say(
            language="es-ES",
            voice="Polly.Lupe",
            text="No he escuchado ninguna respuesta. Hasta luego."
        )
        return Response(str(vr), mimetype="text/xml")

    # 2) Segunda vez: ya tenemos lo que dijo el usuario en texto
    user_text = speech_result
    print("Usuario dijo:", user_text)

    # Llamamos al LLM
    llm_answer = llamar_llm(user_text)

    # Respondemos por voz al usuario
    vr.say(
        language="es-ES",
        voice="Polly.Lupe",
        text=llm_answer
    )

    # Si quieres que siga la conversación, podrías aquí volver a hacer otro Gather.
    # Por ahora, terminamos la llamada después de responder.
    return Response(str(vr), mimetype="text/xml")


def llamar_llm(texto_usuario: str) -> str:
    """
    Envía el texto al LLM (OpenAI) y devuelve una frase corta para leer por voz.
    """
    completion = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "Eres un asistente de IVR en español para una empresa de tecnología. "
                    "Responde de forma breve, clara y amable, en una o dos frases."
                )
            },
            {"role": "user", "content": texto_usuario}
        ]
    )

    return completion.choices[0].message.content


if __name__ == "__main__":
    # Para pruebas locales
    app.run(host="0.0.0.0", port=5000, debug=True)


