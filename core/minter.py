import asyncio
import email
from email import policy
import imaplib
import json
import logging
import os
import random
import re
import string
import time
from typing import Optional, Tuple
import httpx

logger = logging.getLogger("flonno_minter")

BASE_URL = "https://www.flonno.com"
CLERK_API = "https://clerk.flonno.com/v1/client"
TURNSTILE_SITEKEY = "0x4AAAAAAAWXJGBD7bONzLBd"

class FlonnoMinter:
    def __init__(
        self,
        imap_host: str = "imap.gmail.com",
        imap_port: int = 993,
        imap_user: str = "",
        imap_pass: str = "",
        capsolver_key: str = "",
        proxy_url: Optional[str] = None
    ):
        self.imap_host = imap_host
        self.imap_port = imap_port
        self.imap_user = imap_user
        self.imap_pass = imap_pass
        self.capsolver_key = capsolver_key
        self.proxy_url = proxy_url

    def _generate_password(self, length: int = 16) -> str:
        chars = string.ascii_letters + string.digits + "!@#$%^&*"
        return "".join(random.choice(chars) for _ in range(length))

    def _generate_alias_email(self, base_email: str) -> str:
        if "+" in base_email or "@" not in base_email:
            return base_email
        user, domain = base_email.split("@", 1)
        tag = f"fl{int(time.time())}{random.randint(100, 999)}"
        return f"{user}+{tag}@{domain}"

    async def solve_turnstile(self) -> str:
        """Решение Cloudflare Turnstile через Capsolver API или headless fallback"""
        if not self.capsolver_key:
            raise ValueError("Capsolver API key is required for automated Turnstile solving.")
        
        async with httpx.AsyncClient(timeout=45.0) as client:
            create_task_payload = {
                "clientKey": self.capsolver_key,
                "task": {
                    "type": "AntiTurnstileTaskProxyLess",
                    "websiteURL": f"{BASE_URL}/sign-up",
                    "websiteKey": TURNSTILE_SITEKEY,
                    "metadata": {"action": "clerk_signup"}
                }
            }
            res = await client.post("https://api.capsolver.com/createTask", json=create_task_payload)
            data = res.json()
            if data.get("errorId", 0) != 0:
                raise RuntimeError(f"Capsolver createTask failed: {data.get('errorDescription')}")
            
            task_id = data["taskId"]
            logger.info(f"Capsolver task created: {task_id}. Waiting for token...")

            for _ in range(30):
                await asyncio.sleep(2)
                res_poll = await client.post(
                    "https://api.capsolver.com/getTaskResult",
                    json={"clientKey": self.capsolver_key, "taskId": task_id}
                )
                res_data = res_poll.json()
                if res_data.get("status") == "ready":
                    token = res_data["solution"]["token"]
                    logger.info("Turnstile solved successfully.")
                    return token
                if res_data.get("status") == "failed":
                    raise RuntimeError(f"Capsolver failed: {res_data.get('errorDescription')}")
            
            raise TimeoutError("Turnstile solving timed out.")

    def fetch_email_otp(self, target_email: str, after_timestamp: float, timeout_s: int = 90) -> str:
        """Извлечение 6-значного OTP-кода из ящика IMAP"""
        start = time.time()
        logger.info(f"Checking IMAP for OTP sent to {target_email} after {after_timestamp}...")
        while time.time() - start < timeout_s:
            time.sleep(3)
            try:
                mail = imaplib.IMAP4_SSL(self.imap_host, self.imap_port, timeout=10)
                mail.login(self.imap_user, self.imap_pass)
                mail.select("INBOX")
                
                # Поиск писем от Clerk / Flonno
                status, msg_ids = mail.search(None, '(OR FROM "clerk" FROM "flonno")')
                id_list = msg_ids[0].split()
                if id_list:
                    for mid in reversed(id_list[-5:]):
                        status, data = mail.fetch(mid, "(RFC822)")
                        msg_obj = email.message_from_bytes(data[0][1], policy=policy.default)
                        
                        # Проверяем дату
                        date_tuple = email.utils.parsedate_to_datetime(msg_obj["Date"])
                        if date_tuple.timestamp() >= after_timestamp - 10:
                            body = ""
                            if msg_obj.is_multipart():
                                for part in msg_obj.walk():
                                    if part.get_content_type() in ("text/plain", "text/html"):
                                        payload = part.get_payload(decode=True)
                                        if payload:
                                            body += payload.decode("utf-8", errors="ignore")
                            else:
                                payload = msg_obj.get_payload(decode=True)
                                if payload:
                                    body = payload.decode("utf-8", errors="ignore")

                            # Ищем 6 цифр
                            codes = re.findall(r'\b([0-9]{6})\b', body)
                            if codes:
                                otp = codes[0]
                                logger.info(f"Found OTP code: {otp}")
                                mail.logout()
                                return otp
                mail.logout()
            except Exception as e:
                logger.debug(f"IMAP check poll error: {e}")
        
        raise TimeoutError(f"OTP code not received within {timeout_s}s.")

    async def mint_key(self) -> Tuple[str, str]:
        """Полный цикл создания аккаунта, клейма $1.00 и генерации API-ключа"""
        target_email = self._generate_alias_email(self.imap_user)
        password = self._generate_password()
        
        logger.info(f"Minting new Flonno account: {target_email}")
        
        # 1. Решение Turnstile
        turnstile_token = await self.solve_turnstile()
        
        req_start_ts = time.time()
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/sign-up",
        }
        
        async with httpx.AsyncClient(timeout=30.0, headers=headers, proxy=self.proxy_url) as client:
            # 2. Инициализация Sign-Up в Clerk
            res_signup = await client.post(
                f"{CLERK_API}/sign_ups?_clerk_js_version=5.0.0",
                data={
                    "email_address": target_email,
                    "password": password,
                    "captcha_token": turnstile_token
                }
            )
            signup_data = res_signup.json()
            if "errors" in signup_data:
                raise RuntimeError(f"Clerk Sign-up failed: {signup_data['errors']}")
            
            signup_id = signup_data["response"]["id"]
            logger.info(f"Sign-up created: {signup_id}. Preparing email verification...")
            
            # 3. Отправка verification code
            await client.post(
                f"{CLERK_API}/sign_ups/{signup_id}/prepare_verification?_clerk_js_version=5.0.0",
                data={"strategy": "email_code"}
            )
            
            # 4. Получение OTP из IMAP
            loop = asyncio.get_running_loop()
            otp = await loop.run_in_executor(None, self.fetch_email_otp, target_email, req_start_ts)
            
            # 5. Верификация кода
            res_verify = await client.post(
                f"{CLERK_API}/sign_ups/{signup_id}/attempt_verification?_clerk_js_version=5.0.0",
                data={"code": otp}
            )
            verify_data = res_verify.json()
            client_obj = verify_data.get("client", {})
            sessions = client_obj.get("sessions", [])
            if not sessions:
                raise RuntimeError(f"Verification failed: {verify_data}")
            
            jwt_token = sessions[0]["last_active_token"]["jwt"]
            logger.info("User authenticated. Session JWT acquired.")
            
            # 6. Клейм $1.00 пробного баланса
            auth_headers = {"Authorization": f"Bearer {jwt_token}"}
            res_claim = await client.post(f"{BASE_URL}/api/attribution/claim", headers=auth_headers)
            logger.info(f"Trial attribution claimed: {res_claim.status_code}")
            
            # 7. Генерация scoped API-ключа
            key_payload = {
                "name": f"auto-{int(time.time())}",
                "models": ["qwen3.8-27b"],
                "endpoints": ["chat:completions", "models:read"]
            }
            res_key = await client.post(f"{BASE_URL}/api/keys", headers=auth_headers, json=key_payload)
            if res_key.status_code not in (200, 201):
                raise RuntimeError(f"Failed to generate API key: {res_key.text}")
            
            key_json = res_key.json()
            api_key = key_json.get("key") or key_json.get("secret")
            if not api_key:
                raise RuntimeError(f"Key missing in response: {key_json}")
            
            logger.info(f"Successfully minted Flonno key: {api_key[:12]}***")
            return api_key, target_email
