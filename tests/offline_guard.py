# -*- coding: utf-8 -*-
"""
バドミントン新規モジュールのテスト用・通信遮断ガード。

目的: テスト中に実ネットワークへ一切接続させない（偽キーによる実API接続の再発防止）。
方式: 通信の最下層（ソケット接続・DNS解決）と、主要なHTTPの入口（requests / urllib）を差し替え、
      呼ばれた時点で例外を送出し、試行を ATTEMPTS に記録する。モックを忘れた経路は必ず失敗する。

このモジュールは各テストファイルの先頭で import する（import 時点で遮断を有効化）。
"""
from __future__ import annotations

import socket
import urllib.request

ATTEMPTS: list[str] = []


class NetworkBlocked(RuntimeError):
    """テスト中に実通信が試みられたことを示す例外。"""


def _block(kind: str):
    def _raise(*args, **kwargs):
        ATTEMPTS.append(f"{kind} {args[1] if kind == 'connect' and len(args) > 1 else ''}".strip())
        raise NetworkBlocked(f"テスト中の実通信を遮断しました: {kind}")
    return _raise


def install() -> None:
    """遮断を有効化する。二重に呼んでも安全。"""
    if getattr(socket, "_badminton_guard_installed", False):
        return
    socket.socket.connect = _block("connect")
    socket.socket.connect_ex = _block("connect_ex")
    socket.create_connection = _block("create_connection")
    socket.getaddrinfo = _block("getaddrinfo")
    urllib.request.urlopen = _block("urlopen")
    try:
        import requests
        requests.sessions.Session.send = _block("requests.send")
    except ImportError:  # requests が無い環境でもソケット層で遮断される
        pass
    socket._badminton_guard_installed = True


def reset_attempts() -> None:
    ATTEMPTS.clear()


install()
