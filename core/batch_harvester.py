import asyncio
import json
import logging
import os
import random
import re
import string
import time
from typing import Dict, List, Optional
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("flonno_batch_harvester")

MAIL_TM_API = "https://api.mail.tm"
BASE_URL = "https://www.flonno.com"
KEYS_FILE = "C:/Users/beres/flonno-2api/keys_pool.json"

class MailTmClient:
    def __init__(self):
        self.client = httpx.Client(base_url=MAIL_TM_API, timeout=20.0)
        self.token = None
        self.address = None
        self.password = None

    def create_account(self) -> str:
        # 1. Get available domains
        res = self.client.get("/domains")
        domains = res.json().get("hydra:member", [])
        if not domains:
            raise RuntimeError("No mail.tm domains available")
        domain = domains[0]["domain"]

        # 2. Generate credentials
        rand_str = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        self.address = f"fl_{rand_str}@{domain}"
        self.password = f"P@ss_{rand_str}_2026!"

        # 3. Create account
        create_res = self.client.post("/accounts", json={"address": self.address, "password": self.password})
        if create_res.status_code not in (200, 201):
            raise RuntimeError(f"Failed to create mail.tm account: {create_res.text}")

        # 4. Login to get token
        token_res = self.client.post("/token", json={"address": self.address, "password": self.password})
        self.token = token_res.json()["token"]
        self.client.headers.update({"Authorization": f"Bearer {self.token}"})
        logger.info(f"Mail.tm account created: {self.address}")
        return self.address

    def wait_for_otp(self, timeout_s: int = 60) -> str:
        start = time.time()
        logger.info(f"Polling mail.tm for OTP ({self.address})...")
        while time.time() - start < timeout_s:
            time.sleep(3)
            try:
                res = self.client.get("/messages")
                messages = res.json().get("hydra:member", [])
                if messages:
                    msg_id = messages[0]["id"]
                    msg_detail = self.client.get(f"/messages/{msg_id}").json()
                    intro = msg_detail.get("intro", "")
                    text = msg_detail.get("text", "")
                    combined = f"{intro} {text}"
                    codes = re.findall(r'\b([0-9]{6})\b', combined)
                    if codes:
                        logger.info(f"OTP received: {codes[0]}")
                        return codes[0]
            except Exception as e:
                logger.debug(f"Poll error: {e}")
        raise TimeoutError("OTP timed out")
