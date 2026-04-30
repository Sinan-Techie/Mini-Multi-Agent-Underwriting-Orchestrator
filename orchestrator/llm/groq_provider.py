import os
from typing import AsyncIterator

from groq import AsyncGroq


class GroqProvider:
    def __init__(self):
        self.client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        """
        Streams tokens from Groq
        """
        stream = await self.client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            stream=True,
            timeout=10
        )

        async for chunk in stream:
            content = chunk.choices[0].delta.content
            if content is not None:
                yield content