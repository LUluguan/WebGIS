# -*- coding: utf-8 -*-
import os, sys, numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(sys.path[0])
import sar_change

def test_change_mask():
    base = np.full((32, 32), 12.0, dtype="float32")          # 陆地 dB
    flood = base.copy()
    flood[8:24, 8:24] = 0.0                                   # 中央区被水淹没 -> -12dB
    m = sar_change.change_mask(flood, base, drop_db=-8.0)
    assert m[8:24, 8:24].all(), "淹没区应全判为新增水面"
    assert not m[:4, :4].any(), "未变化区不应误判"

def test_to_db_linear():
    lin = np.array([[100.0, 100.0], [100.0, 1.0]], dtype="float32")
    db = sar_change.to_db(lin)
    assert abs(db[0, 0] - 20.0) < 1e-3, "100 线性幅度应≈20dB"

if __name__ == "__main__":
    test_change_mask(); test_to_db_linear()
    print("test_sar_change OK")
