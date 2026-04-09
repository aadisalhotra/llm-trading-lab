"""Google Gemini adapter."""
from __future__ import annotations

import os

from .base import BaseAdapter


class GeminiAdapter(BaseAdapter):
    provider_name = "google"
    supports_vision = True

    def _call_api(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[bytes] | None = None,
    ) -> tuple[str, str]:
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

        # Gemini takes a list of parts where each part is a dict with either
        # "text" or "inline_data": {"mime_type", "data"}. The SDK accepts raw
        # bytes for inline_data and handles base64 encoding internally.
        if images:
            parts: list = []
            for img in images:
                parts.append({"mime_type": "image/png", "data": img})
            parts.append(user_prompt)
            response = model.generate_content(parts)
        else:
            response = model.generate_content(user_prompt)

        text = getattr(response, "text", "") or ""
        return text, self.model  # Gemini SDK doesn't echo the model id back
