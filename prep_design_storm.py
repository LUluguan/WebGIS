# -*- coding: utf-8 -*-
"""
prep_design_storm.py — 权威设计暴雨(P-III 型)推求 2/5/10/50/100 年 24h 设计雨量

数据依据: 《广东省暴雨径流查算图表》/《广东省暴雨参数等值线图》(2003) 广州(珠江新城)参数:
  - 24h 暴雨均值  H24 = 130 mm
  - 变差系数     Cv  = 0.40
  - 偏态系数     Cs  = 3.5 × Cv = 1.40 (广东省常用 Cs=3.5Cv)
方法: 皮尔逊 III 型频率分析, Kp(离均系数) 用 Wilson-Hilferty 近似:
      Φp = (2/Cs)·[(1 + Cs·Φn/6 − Cs²/36)³ − 1]
      Hp = H24 · (1 + Cv·Φp)
校验锚点: 多个广州工程查算成果 P=1%(100年) 24h 设计暴雨 ≈ 300 mm
          (白云区蚌湖桥工程 300.3mm; 中大站 299mm) —— 本脚本应复现 ~300mm

输出: 打印 5 期设计雨量; 也可作为 bathtub_flood.py 的 RETURNS 输入(import 或复制)。
"""
import math
import json

# 广州 24h 暴雨参数(权威来源)
H24 = 130.0      # 24h 暴雨均值 (mm)
CV = 0.40        # 变差系数
CS = 3.5 * CV    # 偏态系数 = 3.5Cv (广东省标准)

# 重现期 -> 超概率 P(≥) (年最大超概率)
RETURN_P = {2: 0.50, 5: 0.20, 10: 0.10, 50: 0.02, 100: 0.01}


def norm_cdf(z):
    """标准正态 CDF(stdlib math.erf)。"""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def norm_ppf(q):
    """标准正态逆 CDF(二分法, 无 scipy 依赖)。q ∈ (0,1)。"""
    lo, hi = -8.0, 8.0
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if norm_cdf(mid) < q:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def p3_phi(cs, exceed_prob):
    """皮尔逊 III 型离均系数 Φp(Wilson-Hilferty 近似)。
       exceed_prob = P(≥), 用非超概率 F=1-P 的标准正态分位 Φn。"""
    zn = norm_ppf(1.0 - exceed_prob)          # 非超概率分位
    inner = 1.0 + cs * zn / 6.0 - cs * cs / 36.0
    return (2.0 / cs) * (inner ** 3 - 1.0)


def design_rainfall(periods=(2, 5, 10, 50, 100), h24=H24, cv=CV, cs=CS):
    """返回 {重现期: 24h设计雨量mm}。"""
    out = {}
    for T, p in RETURN_P.items():
        if T not in periods:
            continue
        phi = p3_phi(cs, p)
        hp = h24 * (1.0 + cv * phi)
        out[T] = round(hp, 1)
    return out


if __name__ == "__main__":
    rains = design_rainfall()
    print("广州(珠江新城) P-III 24h 设计暴雨 (H24=%gmm Cv=%g Cs=%g):" % (H24, CV, CS))
    for T in (2, 5, 10, 50, 100):
        print("  %3d 年一遇: %7.1f mm" % (T, rains[T]))
    print("  校验: 100年应≈300mm, 得 %.1fmm (误差 %+.1f%%)"
          % (rains[100], 100.0 * (rains[100] - 300.0) / 300.0))
    # 供 bathtub_flood.py 使用
    with open(r"D:\Competiton\flood_out\design_storm_24h.json", "w", encoding="utf-8") as f:
        json.dump(rains, f, ensure_ascii=False, indent=2)
    print("已写 flood_out/design_storm_24h.json")
