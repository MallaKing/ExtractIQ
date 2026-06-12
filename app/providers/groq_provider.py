from groq import Groq
from app.config import settings
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class GroqProvider:
    """Groq LLM provider abstraction."""
    
    SUPPORTED_MODELS = {
        "llama-3-70b": "llama-3-70b-versatile",
        "qwen": "qwen-2.5-72b-instruct",
        "gemma": "gemma2-9b-it",
        "deepseek": "deepseek-r1-distill-llama-70b",
    }
    
    def __init__(self, api_key: str = None):
        """Initialize Groq client. Uses provided api_key or falls back to settings."""
        key = api_key or settings.groq_api_key
        self.client = Groq(api_key=key)
        logger.info("Groq provider initialized")
    
    def generate(
        self,
        prompt: str,
        model: str = "llama-3-70b",
        temperature: float = 0.0,
        max_tokens: int = 1024,
        **kwargs
    ) -> str:
        """
        Generate text using Groq.
        
        Args:
            prompt: Input prompt
            model: Model name (default: llama-3-70b)
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            
        Returns:
            Generated text
        """
        model_id = self.SUPPORTED_MODELS.get(model, model)
        
        try:
            response = self.client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=model_id,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Error calling Groq: {e}")
            raise
    
    def generate_json(
        self,
        prompt: str,
        model: str = "llama-3-70b",
        temperature: float = 0.0,
        max_tokens: int = 2048,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate JSON structured response.
        
        Args:
            prompt: Input prompt
            model: Model name
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            
        Returns:
            Parsed JSON response
        """
        model_id = self.SUPPORTED_MODELS.get(model, model)
        
        try:
            response = self.client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=model_id,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                **kwargs
            )
            import json
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.error(f"Error calling Groq with JSON mode: {e}")
            raise
    
    def stream(
        self,
        prompt: str,
        model: str = "llama-3-70b",
        temperature: float = 0.0,
        max_tokens: int = 1024,
        **kwargs
    ):
        """
        Stream text generation.
        
        Args:
            prompt: Input prompt
            model: Model name
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            
        Yields:
            Text chunks
        """
        model_id = self.SUPPORTED_MODELS.get(model, model)
        
        try:
            with self.client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=model_id,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                **kwargs
            ) as response:
                for chunk in response:
                    if chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
        except Exception as e:
            logger.error(f"Error streaming from Groq: {e}")
            raise
    
    def get_models(self) -> list:
        """Get list of supported models."""
        return list(self.SUPPORTED_MODELS.keys())


# Singleton instance
_groq_provider = None


def get_groq_provider() -> GroqProvider:
    """Get Groq provider singleton."""
    global _groq_provider
    if _groq_provider is None:
        _groq_provider = GroqProvider()
    return _groq_provider
