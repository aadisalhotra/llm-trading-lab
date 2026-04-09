"""Google Gemini adapter."""
from __future__ import annotations

import os

from .base import BaseAdapter


class GeminiAdapter(BaseAdapter):
    provider_name = "google"

    def _call_api(self, system_prompt: str, user_prompt: str) -> tuple[str, str]:
        import google.generativeai as genai

        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY not set")

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name=self.model,
            system_instruction=system_prompt,
            generation_config={
                "response_mime_type": "application/json",
                "max_output_tokens": 4096,
            },
        )
        response = model.generate_content(user_prompt)
        text = getattr(response, "text", "") or ""
        return text, self.model  # Gemini SDK doesn't echo the model id back
