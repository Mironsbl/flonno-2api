import os
from openai import OpenAI

# Тест через наш локальный Flonno 2API шлюз
client = OpenAI(
    api_key="dummy-token",
    base_url="http://127.0.0.1:8097/v1",
)

def test_chat():
    print("=== Testing Chat Completion (Non-streaming) ===")
    response = client.chat.completions.create(
        model="qwen3.8-27b",
        messages=[{"role": "user", "content": "Скажи коротко: кто ты и на каком чипе запущен?"}],
        stream=False,
    )
    print("Ответ модели:\n", response.choices[0].message.content)
    print("\nИспользовано токенов:", response.usage.total_tokens)

def test_stream():
    print("\n=== Testing Chat Completion (Streaming) ===")
    response = client.chat.completions.create(
        model="qwen3.8-27b",
        messages=[{"role": "user", "content": "Назови 3 главных плюса Rust перед C++."}],
        stream=True,
    )
    for chunk in response:
        if chunk.choices:
            delta = chunk.choices[0].delta.content or ""
            print(delta, end="", flush=True)
    print("\n")

if __name__ == "__main__":
    test_chat()
    test_stream()
