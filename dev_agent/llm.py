"""Multi-backend LLM analyzer (Ollama, SAIA, TUD, OpenAI)."""

import json
from enum import Enum


class LLMBackend(Enum):
    """LLM backend enum."""
    OLLAMA = "ollama"
    SAIA = "saia"
    TUD = "tud"
    OPENAI = "openai"


class LLMAnalyzer:
    """Multi-backend LLM analyzer for Kubernetes health analysis."""

    def __init__(self, backend, url, model, api_key=None):
        """Initialize LLM analyzer.
        
        Args:
            backend: LLMBackend enum value
            url: API endpoint URL
            model: Model name
            api_key: Optional API key (required for SAIA, TUD, OpenAI)
        """
        self.backend = backend
        self.url = url
        self.model = model
        self.api_key = api_key

    def build_prompt(self, issue, context, prediction=None):
        """Build LLM prompt with issue, context, and prediction data.
        
        Args:
            issue: Issue description
            context: Context information
            prediction: Optional prediction data dict with keys:
                - risk_score: Risk score (0-1)
                - ttf_minutes: Time to failure in minutes
                - confidence: Confidence score (0-1)
                - markov_state: Markov state string
                - memory_trend: Memory trend value
        
        Returns:
            Formatted prompt string
        """
        prompt_parts = []
        prompt_parts.append(f"Issue: {issue}")
        prompt_parts.append(f"Context: {context}")
        
        if prediction:
            prompt_parts.append("\nPrediction data:")
            if "risk_score" in prediction:
                prompt_parts.append(f"  Risk score: {prediction['risk_score']}")
            if "ttf_minutes" in prediction:
                prompt_parts.append(f"  Time to failure: {prediction['ttf_minutes']} minutes")
            if "confidence" in prediction:
                prompt_parts.append(f"  Confidence: {prediction['confidence']}")
            if "markov_state" in prediction:
                prompt_parts.append(f"  Markov state: {prediction['markov_state']}")
            if "memory_trend" in prediction:
                prompt_parts.append(f"  Memory trend: {prediction['memory_trend']}")
        
        prompt_parts.append("\nAnalyze the issue and provide:")
        prompt_parts.append("- analysis: Brief analysis of the root cause")
        prompt_parts.append("- severity: high, medium, or low")
        prompt_parts.append("- action: Recommended action")
        prompt_parts.append("- command: kubectl command to execute (if applicable)")
        
        return "\n".join(prompt_parts)

    def parse_response(self, response):
        """Parse LLM JSON response with fallback for invalid JSON.
        
        Args:
            response: Raw LLM response string
            
        Returns:
            Parsed dict with fallback for invalid JSON
        """
        try:
            parsed = json.loads(response)
            # Ensure required fields exist
            if not isinstance(parsed, dict):
                return self._fallback_response()
            
            # Add missing required fields with defaults
            if "analysis" not in parsed:
                parsed["analysis"] = "Unknown issue"
            if "severity" not in parsed:
                parsed["severity"] = "medium"
            if "action" not in parsed:
                parsed["action"] = "Monitor"
            if "command" not in parsed:
                parsed["command"] = ""
            
            return parsed
        except (json.JSONDecodeError, TypeError, AttributeError):
            return self._fallback_response()

    def _fallback_response(self):
        """Generate fallback response for invalid JSON."""
        return {
            "analysis": "Unknown issue - invalid LLM response",
            "severity": "medium",
            "action": "Monitor",
            "command": ""
        }
