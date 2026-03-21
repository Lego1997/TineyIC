"""Investor persona definitions for tinyIC."""

from tinyic.personas.base import InvestorPersona
from tinyic.personas.registry import PERSONA_REGISTRY, list_personas, load_persona

__all__ = ["InvestorPersona", "PERSONA_REGISTRY", "list_personas", "load_persona"]
