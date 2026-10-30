"""OpenAI embedder using text-embedding models."""

import os
from typing import List

import numpy as np
from openai import OpenAI

from tau2.knowledge.embedders.base import BaseEmbedder


class OpenAIEmbedder(BaseEmbedder):
    """Embedder using OpenAI's embedding models."""

    def __init__(self, model: str = "text-embedding-ada-002", api_key: str = None):
        """
        Initialize OpenAI embedder.

        Args:
            model: OpenAI model name. Supported models include:
                   - text-embedding-ada-002 (default, 1536 dimensions)
                   - text-embedding-3-small (1536 dimensions)
                   - text-embedding-3-large (3072 dimensions)
            api_key: API key. Defaults to TAU2_EMBEDDING_API_KEY, then
                OPENAI_API_KEY if no dedicated embedding credential is set.
        """
        self.model = model
        self.client = OpenAI(
            api_key=api_key
            or os.getenv("TAU2_EMBEDDING_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        )

    def embed(self, texts: List[str]) -> np.ndarray:
        """
        Embed texts using OpenAI API.

        Args:
            texts: List of text strings to embed

        Returns:
            Array of embeddings with shape (len(texts), embedding_dim)
        """
        if not texts:
            raise ValueError("No text to embed.")

        response = self.client.embeddings.create(input=texts, model=self.model)
        embeddings = [item.embedding for item in response.data]
        return np.array(embeddings)

    def get_name(self) -> str:
        """Return the name of the embedder."""
        return f"openai_{self.model}"
