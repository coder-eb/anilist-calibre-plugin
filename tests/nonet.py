"""Imported by every test module: any real network call from a test fails loudly.

The tests use fake transports; this makes sure no test (or CI run) can ever reach the real AniList.
"""
import socket
import urllib.request


def _blocked(*args, **kwargs):
    raise AssertionError('a test tried to use the real network (urllib/socket); use a fake transport')


urllib.request.urlopen = _blocked
socket.create_connection = _blocked
