import httpx
from app.core.config import settings

class OllamaClient:
    async def generate(self, prompt: str):
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{settings.OLLAMA_URL}/api/generate",
                json={
                    "model": settings.MODEL_NAME,
                    "prompt": prompt,
                    "stream": False
                }
            )
        return response.json()["response"]