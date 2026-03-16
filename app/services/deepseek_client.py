from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..config import settings
from ..models import TranslationSession


class DeepSeekClient:
    def __init__(self) -> None:
        self.api_key = settings.deepseek_api_key
        self.base_url = settings.deepseek_base_url.rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _post(self, payload: dict) -> dict:
        request = urllib.request.Request(
            url=f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=settings.request_timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"DeepSeek API returned HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Unable to reach DeepSeek API: {exc}") from exc

    def translate_text(
        self,
        *,
        session: TranslationSession,
        source_text: str,
        block_kind: str,
        document_name: str,
    ) -> str:
        if not self.configured:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured.")

        if not source_text.strip():
            return source_text

        system_prompt = (
            "You are a professional technical translator. "
            "This conversation window is dedicated to one document only. "
            f"Session id: {session.id}. "
            f"Translate all source text faithfully into {session.target_language}. "
            "Keep structure, formulas, citations, numbering, abbreviations, and line breaks whenever possible. "
            "Do not summarize. Do not explain. Do not add notes. "
            "If the source is already in the target language, return it unchanged."
        )
        user_prompt = (
            f"Document name: {document_name}\n"
            f"Block type: {block_kind}\n"
            "Return only the translated text.\n"
            "Source:\n"
            f"{source_text}"
        )

        payload = {
            "model": session.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": settings.deepseek_temperature,
            "stream": False,
        }
        data = self._post(payload)
        return data["choices"][0]["message"]["content"].strip()
