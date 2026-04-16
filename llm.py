from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def chat(text: str):
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a concise AI assistant."},
            {"role": "user", "content": text}
        ],
        temperature=0.7,
        stream=False
    )

    return response.choices[0].message.content


while True:
    text = input("You: ").strip()
    if not text:
        continue

    reply = chat(text)
    print("AI:", reply)