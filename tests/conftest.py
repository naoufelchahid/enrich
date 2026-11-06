"""Tests must never contact providers or send DNS packets."""
import socket

import pytest

from scripts import email_hygiene


@pytest.fixture(autouse=True)
def offline_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Real network forbidden in tests')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket.socket, 'sendto', forbidden)
    # Existing integration tests use synthetic provider-verified example addresses.
    # DNS behavior itself is tested separately with an injected resolver.
    monkeypatch.setattr(email_hygiene, 'resolve_mail_domain', lambda domain: 'PASS')
