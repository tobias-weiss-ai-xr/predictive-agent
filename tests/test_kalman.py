"""Test KalmanTrend class for trend estimation."""
import pytest
from dev_agent.kalman import KalmanTrend


def test_kalman_trend_initialization():
    """Test that KalmanTrend initializes correctly."""
    kt = KalmanTrend()
    assert kt.level == 0.0
    assert kt.velocity == 0.0
    assert kt.level_uncertainty > 0
    assert kt.velocity_uncertainty > 0


def test_kalman_trend_update():
    """Test that KalmanTrend updates state correctly."""
    kt = KalmanTrend()
    kt.update(100.0)
    assert kt.level == 100.0
    assert kt.velocity == 0.0


def test_kalman_trend_predict():
    """Test that KalmanTrend can predict future values."""
    kt = KalmanTrend()
    kt.update(100.0)
    kt.update(105.0)
    kt.update(110.0)

    pred_level, pred_sigma = kt.predict(1.0)
    assert pred_level > 110.0  # Should predict increasing trend
    assert pred_sigma > 0


def test_kalman_trend_time_to_threshold():
    """Test time-to-threshold calculation."""
    kt = KalmanTrend()
    kt.update(100.0)
    kt.update(105.0)
    kt.update(110.0)

    steps, confidence = kt.time_to_threshold(150.0, max_steps=100)
    assert steps > 0
    assert 0 <= confidence <= 1.0


def test_kalman_trend_decreasing():
    """Test that decreasing values produce negative velocity."""
    kt = KalmanTrend()
    kt.update(100.0)
    kt.update(95.0)
    kt.update(90.0)
    assert kt.velocity < 0


def test_kalman_trend_stable():
    """Test that stable values produce near-zero velocity."""
    kt = KalmanTrend()
    for _ in range(10):
        kt.update(100.0)
    assert abs(kt.velocity) < 1.0


def test_kalman_trend_noise_resistance():
    """Test that Kalman filter resists noise."""
    kt = KalmanTrend(process_noise=0.1, measurement_noise=10.0)
    # Feed noisy data around 100
    for val in [105, 95, 102, 98, 103, 97, 101, 99, 104, 96]:
        kt.update(float(val))
    # Level should be close to 100 despite noise
    assert 90 < kt.level < 110


def test_kalman_trend_predict_far_future():
    """Test prediction far into the future has higher uncertainty."""
    kt = KalmanTrend()
    kt.update(100.0)
    kt.update(110.0)

    _, sigma1 = kt.predict(1.0)
    _, sigma10 = kt.predict(10.0)
    assert sigma10 > sigma1
