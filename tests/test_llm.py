"""Test multi-backend LLM analysis."""
import pytest
from unittest.mock import patch, MagicMock, MagicMock as MockResponse
import json
import urllib.request
from predictive_agent.llm import LLMAnalyzer, LLMBackend


def test_llm_backend_enum():
    """Test LLM backend enum."""
    assert LLMBackend.OLLAMA.value == "ollama"
    assert LLMBackend.SAIA.value == "saia"
    assert LLMBackend.TUD.value == "tud"
    assert LLMBackend.OPENAI.value == "openai"


def test_llm_analyzer_creation_ollama():
    """Test creating Ollama analyzer."""
    analyzer = LLMAnalyzer(
        backend=LLMBackend.OLLAMA,
        url="http://ollama.llm.svc.cluster.local:11434",
        model="qwen3-30b-a3b:latest",
    )
    assert analyzer.backend == LLMBackend.OLLAMA
    assert analyzer.url == "http://ollama.llm.svc.cluster.local:11434"
    assert analyzer.model == "qwen3-30b-a3b:latest"


def test_llm_analyzer_creation_saia():
    """Test creating SAIA analyzer."""
    analyzer = LLMAnalyzer(
        backend=LLMBackend.SAIA,
        url="https://chat-ai.academiccloud.de/v1",
        model="qwen3.5-35b-a3b",
        api_key="test-key",
    )
    assert analyzer.backend == LLMBackend.SAIA
    assert analyzer.api_key == "test-key"


def test_llm_analyzer_creation_tud():
    """Test creating TUD analyzer."""
    analyzer = LLMAnalyzer(
        backend=LLMBackend.TUD,
        url="https://llm-service.ai.tu-darmstadt.de/v1",
        model="GLM-5.2-AWQ-INT4",
        api_key="test-key",
    )
    assert analyzer.backend == LLMBackend.TUD


def test_llm_analyzer_creation_openai():
    """Test creating OpenAI analyzer."""
    analyzer = LLMAnalyzer(
        backend=LLMBackend.OPENAI,
        url="https://api.openai.com/v1",
        model="gpt-4o",
        api_key="test-key",
    )
    assert analyzer.backend == LLMBackend.OPENAI


def test_llm_build_prompt_ollama():
    """Test prompt building for Ollama."""
    analyzer = LLMAnalyzer(
        backend=LLMBackend.OLLAMA,
        url="http://localhost:11434",
        model="test-model",
    )
    prompt = analyzer.build_prompt(
        issue="Pod crash",
        context="ns=test pod=app-0 status=CrashLoopBackOff",
        prediction={"risk_score": 0.85, "ttf_minutes": 12, "confidence": 0.87}
    )
    assert "Pod crash" in prompt
    assert "CrashLoopBackOff" in prompt
    assert "0.85" in prompt
    assert "12" in prompt


def test_llm_build_prompt_with_prediction():
    """Test prompt includes prediction data."""
    analyzer = LLMAnalyzer(
        backend=LLMBackend.SAIA,
        url="https://chat-ai.academiccloud.de/v1",
        model="test",
        api_key="key",
    )
    prediction = {
        "risk_score": 0.92,
        "ttf_minutes": 8,
        "confidence": 0.91,
        "markov_state": "CRITICAL",
        "memory_trend": 2.5,
    }
    prompt = analyzer.build_prompt(
        issue="OOM predicted",
        context="memory rising at 2.5 MiB/min",
        prediction=prediction,
    )
    assert "0.92" in prompt
    assert "CRITICAL" in prompt
    assert "2.5" in prompt


def test_llm_parse_response():
    """Test parsing LLM JSON response."""
    analyzer = LLMAnalyzer(
        backend=LLMBackend.OLLAMA,
        url="http://localhost:11434",
        model="test",
    )
    response = '{"analysis": "Memory leak in container", "severity": "high", "action": "Restart pod", "command": "kubectl delete pod"}'
    parsed = analyzer.parse_response(response)
    assert parsed["analysis"] == "Memory leak in container"
    assert parsed["severity"] == "high"


def test_llm_parse_response_invalid():
    """Test parsing invalid JSON response."""
    analyzer = LLMAnalyzer(
        backend=LLMBackend.OLLAMA,
        url="http://localhost:11434",
        model="test",
    )
    parsed = analyzer.parse_response("not json at all")
    assert parsed is not None
    assert "analysis" in parsed  # Should have fallback


# ─── Enhanced tests: actual API calls (mocked) ─────────────────────────────


def test_llm_analyze_ollama_mock():
    """Test analyze() makes HTTP request to Ollama API."""
    analyzer = LLMAnalyzer(
        backend=LLMBackend.OLLAMA,
        url="http://localhost:11434",
        model="test-model",
    )
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = json.dumps({
        "response": '{"analysis": "OOM risk", "severity": "high", "action": "Restart", "command": "kubectl delete pod"}'
    }).encode()
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=None)

    with patch("urllib.request.urlopen", return_value=mock_response):
        result = analyzer.analyze("Pod crash", "ns=test")
        assert result is not None
        assert "analysis" in result


def test_llm_analyze_saia_mock():
    """Test analyze() makes HTTP request to SAIA API (OpenAI-compatible)."""
    analyzer = LLMAnalyzer(
        backend=LLMBackend.SAIA,
        url="https://chat-ai.academiccloud.de/v1",
        model="test-model",
        api_key="test-key",
    )
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = json.dumps({
        "choices": [{"message": {"content": '{"analysis": "Memory leak", "severity": "medium", "action": "Monitor", "command": ""}'}}]
    }).encode()
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=None)

    with patch("urllib.request.urlopen", return_value=mock_response):
        result = analyzer.analyze("High memory", "ns=test")
        assert result is not None
        assert "analysis" in result


def test_llm_analyze_tud_mock():
    """Test analyze() makes HTTP request to TUD API."""
    analyzer = LLMAnalyzer(
        backend=LLMBackend.TUD,
        url="https://llm-service.ai.tu-darmstadt.de/v1",
        model="test-model",
        api_key="test-key",
    )
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = json.dumps({
        "choices": [{"message": {"content": '{"analysis": "CPU throttle", "severity": "low", "action": "Scale", "command": ""}'}}]
    }).encode()
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=None)

    with patch("urllib.request.urlopen", return_value=mock_response):
        result = analyzer.analyze("CPU high", "ns=test")
        assert result is not None
        assert "analysis" in result


def test_llm_analyze_error_handling():
    """Test analyze() handles API errors gracefully."""
    analyzer = LLMAnalyzer(
        backend=LLMBackend.OLLAMA,
        url="http://localhost:11434",
        model="test-model",
    )
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
        result = analyzer.analyze("Pod crash", "ns=test")
        assert result is not None
        assert "analysis" in result  # Should return fallback


def test_llm_analyze_timeout():
    """Test analyze() handles timeouts gracefully."""
    analyzer = LLMAnalyzer(
        backend=LLMBackend.OLLAMA,
        url="http://localhost:11434",
        model="test-model",
        timeout=5,
    )
    import socket
    with patch("urllib.request.urlopen", side_effect=socket.timeout("timed out")):
        result = analyzer.analyze("Pod crash", "ns=test")
        assert result is not None
        assert "analysis" in result


def test_llm_build_prompt_no_prediction():
    """Test prompt building without prediction data."""
    analyzer = LLMAnalyzer(
        backend=LLMBackend.OLLAMA,
        url="http://localhost:11434",
        model="test",
    )
    prompt = analyzer.build_prompt(issue="Test issue", context="test context")
    assert "Test issue" in prompt
    assert "test context" in prompt
