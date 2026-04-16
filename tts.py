import sounddevice as sd
import numpy as np
from deepgram import DeepgramClient
from dotenv import load_dotenv
import os

load_dotenv()
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")

client = DeepgramClient(api_key=DEEPGRAM_API_KEY)

def speak(text: str):
    response = client.speak.v1.audio.generate(
        text=text,
        model="aura-2-thalia-en",
        encoding="linear16",
        sample_rate=24000,
    )

    audio_chunks = []

    for chunk in response:
        if chunk:
            audio_chunks.append(chunk)

    audio_bytes = b"".join(audio_chunks)

    audio_np = np.frombuffer(audio_bytes, dtype=np.int16)
    sd.play(audio_np, samplerate=24000)
    sd.wait()

while True:
    text = input("You: ").strip()
    if not text:
        continue
    speak(text)