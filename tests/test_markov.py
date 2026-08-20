"""Test MarkovChain class for state transitions."""
import pytest
from predictive_agent.markov import MarkovChain


def test_markov_chain_initialization():
    """Test that MarkovChain initializes correctly."""
    mc = MarkovChain()
    assert mc.STATES == ["HEALTHY", "DEGRADED", "STRESSED", "CRITICAL", "FAILED", "RECOVERED"]
    assert mc.total_transitions == 0


def test_markov_chain_record_transition():
    """Test recording a state transition."""
    mc = MarkovChain()
    mc.record_transition("HEALTHY", "DEGRADED")
    assert mc.total_transitions == 1


def test_markov_chain_transition_matrix():
    """Test transition matrix calculation."""
    mc = MarkovChain()
    mc.record_transition("HEALTHY", "DEGRADED")
    mc.record_transition("DEGRADED", "STRESSED")

    matrix = mc.transition_matrix()
    assert len(matrix) == 6
    assert len(matrix[0]) == 6
    # Each row should sum to ~1.0
    for row in matrix:
        assert abs(sum(row) - 1.0) < 0.01


def test_markov_chain_predict():
    """Test state prediction."""
    mc = MarkovChain()
    mc.record_transition("HEALTHY", "DEGRADED")
    mc.record_transition("DEGRADED", "STRESSED")

    prediction = mc.predict("HEALTHY", steps=1)
    assert "DEGRADED" in prediction
    assert prediction["DEGRADED"] > 0


def test_markov_chain_persistence():
    """Test saving and loading state."""
    mc1 = MarkovChain()
    mc1.record_transition("HEALTHY", "DEGRADED")

    data = mc1.to_dict()
    assert "counts" in data
    assert data["total_transitions"] == 1

    mc2 = MarkovChain.from_dict(data)
    assert mc2.total_transitions == 1


def test_markov_chain_most_likely_next():
    """Test most likely next state."""
    mc = MarkovChain()
    # Record many HEALTHY→DEGRADED transitions
    for _ in range(100):
        mc.record_transition("HEALTHY", "DEGRADED")

    state, prob = mc.most_likely_next("HEALTHY")
    assert state == "DEGRADED"
    assert prob > 0.5


def test_markov_chain_multi_step():
    """Test multi-step prediction."""
    mc = MarkovChain()
    for _ in range(50):
        mc.record_transition("HEALTHY", "DEGRADED")
    for _ in range(50):
        mc.record_transition("DEGRADED", "STRESSED")

    pred_1 = mc.predict("HEALTHY", steps=1)
    pred_5 = mc.predict("HEALTHY", steps=5)
    # Multi-step should spread probability more
    assert pred_5["STRESSED"] > pred_1["STRESSED"]


def test_markov_chain_unknown_state():
    """Test that unknown state defaults to index 0."""
    mc = MarkovChain()
    mc.record_transition("UNKNOWN", "HEALTHY")
    assert mc.total_transitions == 1


def test_markov_chain_prior_counts():
    """Test that prior counts are present initially."""
    mc = MarkovChain()
    matrix = mc.transition_matrix()
    # HEALTHY should mostly stay HEALTHY with prior
    assert matrix[0][0] > 0.8  # 9.5/10
