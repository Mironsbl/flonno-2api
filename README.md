# Flonno 2API Gateway & Autonomous Session Minter

Автономный шлюз OpenAI-совместимого API для инференса **Qwen3.8-27B** (RTX 3090, Швейцария) с пулом ключей и авторотацией баланса.

## Архитектура
* **Порт**: `8097` (локальный `http://127.0.0.1:8097/v1`)
* **Модель**: `qwen3.8-27b`
* **Автоминтинг**: Генерация аккаунтов через Clerk Frontend API + решение Cloudflare Turnstile (`0x4AAAAAAAWXJGBD7bONzLBd`) + автосбор OTP через IMAP Gmail (`megashopingspbd@gmail.com`).
* **Активация баланса**: Автоматический вызов `POST /api/attribution/claim` для получения `$1.00` на каждый новый ключ (33.3M входных токенов).

## Быстрый запуск
```cmd
cd C:\Users\beres\flonno-2api
start.bat
```

Или в терминале:
```bash
python server.py
```

## Эндпоинты
* `GET /health` — статус пула ключей и доступных токенов
* `GET /v1/models` — список поддерживаемых моделей (`qwen3.8-27b`)
* `POST /v1/chat/completions` — streaming и non-streaming инференс (OpenAI format)
* `POST /pool/mint` — принудительный запуск генерации нового аккаунта с $1 баланса

## Интеграция в LiteLLM / Codex / Claude Code
```yaml
model_list:
  - model_name: qwen3.8-27b
    litellm_params:
      model: openai/qwen3.8-27b
      api_base: http://127.0.0.1:8097/v1
      api_key: any-dummy-key
```
