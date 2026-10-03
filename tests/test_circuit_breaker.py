import pytest
from scripts.circuit_breaker import CircuitBreaker, CircuitBreakerTrippedError


def test_circuit_breaker_flow(tmp_path):
    state_path = str(tmp_path / "cb_state.json")
    cb = CircuitBreaker(state_file=state_path)

    # Initialement armé et prêt
    assert cb.is_tripped() is False
    cb.assert_can_send()

    # Déclenchement sur 1 seul bounce
    cb.trip(reason="550 Mailbox not found", bounce_email="bad@target.ma")
    assert cb.is_tripped() is True

    # Vérifier l'exception bloquante
    with pytest.raises(CircuitBreakerTrippedError):
        cb.assert_can_send()

    # Réinitialisation manuelle
    cb.reset()
    assert cb.is_tripped() is False
    cb.assert_can_send()


def test_circuit_breaker_persistence(tmp_path):
    state_path = str(tmp_path / "cb_state.json")
    cb1 = CircuitBreaker(state_file=state_path)
    cb1.trip(reason="554 Delivery failed", bounce_email="unknown@corp.ma")

    # Nouveau client pointant vers le même fichier
    cb2 = CircuitBreaker(state_file=state_path)
    assert cb2.is_tripped() is True
    with pytest.raises(CircuitBreakerTrippedError) as exc_info:
        cb2.assert_can_send()
    assert "554 Delivery failed" in str(exc_info.value)


def test_circuit_breaker_corrupt_or_missing_file(tmp_path):
    # Fichier inexistant
    state_path = str(tmp_path / "nonexistent.json")
    cb = CircuitBreaker(state_file=state_path)
    assert cb.is_tripped() is False
    cb.assert_can_send()

    # Fichier corrompu
    with open(state_path, "w", encoding="utf-8") as f:
        f.write("{invalid json...")
    assert cb.is_tripped() is False
    cb.assert_can_send()
