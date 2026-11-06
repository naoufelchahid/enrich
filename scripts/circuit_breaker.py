"""
Module Coupe-Circuit (Circuit Breaker) — Contexte Lab

Arrêt d'urgence de sécurité 'Zero-Bounce' :
Bloque immédiatement tout envoi d'outreach dès qu'un rebond (NDR)
est détecté ou signalé, protégeant ainsi la réputation du domaine SMTP.
"""

import datetime
import json
import logging
import os
import tempfile
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class CircuitBreakerTrippedError(Exception):
    """Raised when an outreach send is attempted while the circuit breaker is active."""
    pass


class CircuitBreaker:
    """Zero-bounce safety circuit breaker: halts all sending immediately if 1 bounce occurs."""

    def __init__(self, state_file: str = ".circuit_breaker_state.json"):
        self.state_file = state_file

    def _read_state(self) -> Dict[str, Any]:
        """Read state from disk. Missing initial state is armed; corrupt or unreadable state blocks sending."""
        default_state: Dict[str, Any] = {
            "tripped": False,
            "reason": None,
            "bounce_email": None,
            "timestamp": None,
        }
        if not os.path.exists(self.state_file):
            return default_state

        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and type(data.get('tripped')) is bool:
                    return data
                return {**default_state, 'tripped': True, 'reason': 'INVALID_BREAKER_STATE'}
        except Exception as e:
            logger.warning("Could not read circuit breaker state file %s: %s", self.state_file, e)
            return {**default_state, 'tripped': True, 'reason': 'UNREADABLE_BREAKER_STATE'}

    def _write_state(self, state: Dict[str, Any]) -> None:
        """Persist state to disk safely."""
        os.makedirs(os.path.dirname(os.path.abspath(self.state_file)), exist_ok=True)
        parent = os.path.dirname(os.path.abspath(self.state_file))
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=parent, delete=False) as f:
            temporary = f.name
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, self.state_file)

    def is_tripped(self) -> bool:
        """Check if circuit breaker is currently tripped."""
        return bool(self._read_state().get("tripped", False))

    def trip(self, reason: str, bounce_email: Optional[str] = None) -> None:
        """Trip circuit breaker, immediately halting future outbound mailings."""
        logger.critical(
            "CIRCUIT BREAKER TRIPPED! Reason: %s (Bounce: %s)", reason, bounce_email
        )
        state = {
            "tripped": True,
            "reason": reason,
            "bounce_email": bounce_email,
            "timestamp": datetime.datetime.now().isoformat(),
        }
        self._write_state(state)

    def reset(self) -> None:
        """Manually reset circuit breaker to armed/ready state."""
        logger.info("Circuit breaker manually reset.")
        self._write_state({
            "tripped": False,
            "reason": None,
            "bounce_email": None,
            "timestamp": None,
        })

    def assert_can_send(self) -> None:
        """Verify that sending is allowed. Raises CircuitBreakerTrippedError if tripped."""
        state = self._read_state()
        if state.get("tripped"):
            raise CircuitBreakerTrippedError(
                f"Sending halted by Circuit Breaker. Tripped at {state.get('timestamp')} for: {state.get('reason')}"
            )
