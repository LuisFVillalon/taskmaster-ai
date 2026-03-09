import httpx
from app.core.config import settings


class OllamaClient:
    """Client for interacting with Ollama LLM service."""
    
    async def generate(self, prompt: str, temperature: float = 0.7) -> str:
        """
        Generate text from Ollama model.
        
        Args:
            prompt: The input prompt for the model
            temperature: Model creativity level (0-1)
            
        Returns:
            Generated response text
        """
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{settings.OLLAMA_URL}/api/generate",
                json={
                    "model": settings.MODEL_NAME,
                    "prompt": prompt,
                    "stream": False,
                    "temperature": temperature
                }
            )
            response.raise_for_status()
            return response.json()["response"]


ollama_client = OllamaClient()
