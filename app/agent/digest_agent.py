"""Digest agent for generating news digests."""

from typing import List
from pydantic import BaseModel


class DigestAgent:
    """Agent responsible for generating news digests from articles."""
    
    def __init__(self):
        """Initialize the digest agent."""
        pass
    
    def generate_digest(self, articles: List[BaseModel]) -> str:
        """
        Generate a digest from articles.
        
        Args:
            articles: List of articles to digest
            
        Returns:
            Generated digest text
        """
        pass
