"""All regular tests are offline: allow only local event-loop/test sockets."""
import socket

import pytest


@pytest.fixture(autouse=True)
def forbid_external_network(monkeypatch):
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    resolve = socket.getaddrinfo
    local = {'localhost', '127.0.0.1', '::1', None}

    def check(address):
        if isinstance(address, tuple) and address[0] not in local:
            raise AssertionError('External network is forbidden in offline tests')

    def guarded_connect(sock, address):
        check(address)
        return connect(sock, address)

    def guarded_connect_ex(sock, address):
        check(address)
        return connect_ex(sock, address)

    def guarded_resolve(host, *args, **kwargs):
        if host not in local:
            raise AssertionError('External DNS is forbidden in offline tests')
        return resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, 'connect', guarded_connect)
    monkeypatch.setattr(socket.socket, 'connect_ex', guarded_connect_ex)
    monkeypatch.setattr(socket, 'getaddrinfo', guarded_resolve)
