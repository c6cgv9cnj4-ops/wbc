# -*- coding: utf-8 -*-
"""State/JSONファイルの安全な書き込み(2026-10-12、独立監査の指摘で追加)。

テンポラリファイルに全量を書いてから os.replace で差し替える。途中で落ちても元のファイルは壊れない
(書き込み途中の切断でJSONが破損し、既送信記録が消えて二重配信になるのを防ぐ)。
"""
import json
import os
import tempfile


def write_json_atomic(path, obj, **dump_kwargs):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, **dump_kwargs)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
