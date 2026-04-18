#!/usr/bin/env python3
"""
哮天每日收盘报告工具  (FastAPI Web UI)
=============================================
马克·米勒维尼趋势策略 · 选股扫描 + 择时监测 + 每日报告图片
所有数据基于本地 K 线 CSV 文件

参考设计：sepa_vcp_app.py（FastAPI + 嵌入式 HTML）

用法:
    python3 daily_report_app.py
    访问: http://localhost:7878
"""
from __future__ import annotations

import os, json, warnings, time, math, csv, ssl, urllib.request
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any, Tuple
from collections import defaultdict

warnings.filterwarnings("ignore")
for _k in list(os.environ.keys()):
    if _k.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        del os.environ[_k]
os.environ["NO_PROXY"] = "*"

# ──────────────────────────────────────────────
# 依赖检查
# ──────────────────────────────────────────────
try:
    import pandas as pd
    import numpy as np
except Exception:
    print("需要 pandas/numpy: pip3 install pandas numpy")
    raise

try:
    import requests
    REQUESTS_OK = True
except Exception:
    REQUESTS_OK = False

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

# ═══════════════════════════════════════════════════════
# 路径配置
# ═══════════════════════════════════════════════════════
DEFAULT_CFG = {
    "a_kline_dir":   "/Users/tonyleung/Downloads/股票/A股/Kline",
    "a_fin_dir":     "/Users/tonyleung/Downloads/股票/A股/财报",
    "hk_kline_dir":  "/Users/tonyleung/Downloads/股票/港股/Kline",
    "hk_fin_dir":    "/Users/tonyleung/Downloads/股票/港股/财报",
    "scan_out_dir":  "/Users/tonyleung/Downloads/股票/每日报告/扫描结果",
    "timing_dir":    "/Users/tonyleung/.openclaw/Downloads/股票/择时回测",
    "report_dir":    "/Users/tonyleung/Downloads/股票/每日报告",
    "north_dir":     "/Users/tonyleung/.openclaw/Downloads/股票/北向资金",
    "south_dir":     "/Users/tonyleung/.openclaw/Downloads/股票/南向资金",
}

for _d in [DEFAULT_CFG["scan_out_dir"], DEFAULT_CFG["report_dir"]]:
    os.makedirs(_d, exist_ok=True)

# ═══════════════════════════════════════════════════════
# 名称缓存
# ═══════════════════════════════════════════════════════
_NAME_CACHE: Dict[str, str] = {}
_SECTOR_CACHE: Dict[str, str] = {}
_NAME_MAP_LOADED = False


def _load_name_map():
    global _NAME_MAP_LOADED
    if _NAME_MAP_LOADED:
        return
    _NAME_MAP_LOADED = True
    for market, fname in [("A股", "/Users/tonyleung/Downloads/股票/A股/list.txt"),
                          ("港股", "/Users/tonyleung/Downloads/股票/港股/list.txt")]:
        if not os.path.exists(fname):
            continue
        with open(fname, encoding="utf-8", errors="ignore") as f:
            for line in f:
                parts = line.strip().split(",")
                if len(parts) < 2:
                    continue
                code_raw = parts[0].strip()
                name = parts[1].strip()
                sector = parts[2].strip() if len(parts) >= 3 else "—"
                if market == "A股":
                    for sfx in [".SZ", ".SS"]:
                        _NAME_CACHE[code_raw + sfx] = name
                        _SECTOR_CACHE[code_raw + sfx] = sector
                else:
                    code_norm = code_raw.lstrip("0").zfill(5)
                    _NAME_CACHE["HK" + code_norm] = name
                    _NAME_CACHE[code_norm + ".HK"] = name
                    _SECTOR_CACHE["HK" + code_norm] = sector
                    _SECTOR_CACHE[code_norm + ".HK"] = sector


def enrich_names(results: List[Dict]) -> List[Dict]:
    if not results:
        return results
    _load_name_map()
    for r in results:
        code = r.get("code", "")
        if code.endswith(".SS") or code.endswith(".SZ"):
            r["name"] = _NAME_CACHE.get(code, code)
            r["sector"] = _SECTOR_CACHE.get(code, "—")
        elif code.endswith(".HK"):
            raw = code.replace(".HK", "").lstrip("0").zfill(5)
            r["name"] = _NAME_CACHE.get("HK" + raw, _NAME_CACHE.get(code, code))
            r["sector"] = _SECTOR_CACHE.get("HK" + raw, "—")
        else:
            r["name"] = _NAME_CACHE.get(code, code)
            r["sector"] = "—"
    return results


# ═══════════════════════════════════════════════════════
# K 线数据加载（修复编码问题）
# ═══════════════════════════════════════════════════════

def load_klines(path: str) -> List[Dict]:
    """返回 [{date, open, high, low, close, volume}]，按日期升序"""
    for enc in ("utf-8", "gbk", "gb2312"):
        try:
            df = pd.read_csv(path, dtype=str, encoding=enc)
            df.columns = [c.strip().lower() for c in df.columns]

            # 列名标准化（支持中文和英文列名）
            col_map = {}
            for c in df.columns:
                if c in ("open", "开盘", "开盘价(元)"): col_map[c] = "open"
                elif c in ("high", "最高", "最高价(元)"): col_map[c] = "high"
                elif c in ("low", "最低", "最低价(元)"): col_map[c] = "low"
                elif c in ("close", "收盘", "收盘价(元)"): col_map[c] = "close"
                elif c in ("volume", "vol", "成交", "成交量", "成交股数"): col_map[c] = "volume"
            df = df.rename(columns=col_map)

            need = {"open", "high", "low", "close", "volume"}
            if not need.issubset(set(df.columns)):
                # 港股格式: open, close, high, low, volume
                hk_need = {"open", "close", "high", "low", "volume"}
                if hk_need.issubset(set(df.columns)):
                    df = df.rename(columns={
                        "open": "open", "close": "close",
                        "high": "high", "low": "low", "volume": "volume"
                    })
                    need = hk_need
                    if not need.issubset(set(df.columns)):
                        return []
                else:
                    return []

            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"]).sort_values("date")
            for c in ["open", "high", "low", "close", "volume"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df = df.dropna(subset=["close"])
            return df[["date", "open", "high", "low", "close", "volume"]].to_dict("records")
        except Exception:
            continue
    return []


def list_csv(dir_path: str) -> List[str]:
    try:
        return sorted([f for f in os.listdir(dir_path) if f.endswith(".csv")])
    except Exception:
        return []


# ═══════════════════════════════════════════════════════
# 技术指标引擎
# ═══════════════════════════════════════════════════════

def _sma(vals: List[float], n: int) -> Optional[float]:
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def _std(vals: List[float], n: int) -> Optional[float]:
    if len(vals) < n:
        return None
    avg = sum(vals[-n:]) / n
    variance = sum((v - avg) ** 2 for v in vals[-n:]) / n
    return variance ** 0.5


def calc_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calc_rsi_series(closes: List[float], period: int = 14) -> List[Optional[float]]:
    result = []
    for i in range(len(closes)):
        if i < period:
            result.append(None)
        else:
            deltas = [closes[j] - closes[j - 1] for j in range(i - period + 1, i + 1)]
            gains = [d if d > 0 else 0 for d in deltas]
            losses = [-d if d < 0 else 0 for d in deltas]
            avg_gain = sum(gains) / period
            avg_loss = sum(losses) / period
            if avg_loss == 0:
                result.append(100.0)
            else:
                rs = avg_gain / avg_loss
                result.append(100 - (100 / (1 + rs)))
    return result


def calc_stoch_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    rsi_vals = calc_rsi_series(closes, period)
    rsi_valid = [v for v in rsi_vals if v is not None]
    if len(rsi_valid) < period:
        return None
    window = rsi_valid[-period:]
    mn, mx = min(window), max(window)
    if mx == mn:
        return 50.0
    last = rsi_valid[-1]
    return (last - mn) / (mx - mn) * 100


def calc_cci(highs: List[float], lows: List[float], closes: List[float], period: int = 20) -> Optional[float]:
    if len(closes) < period:
        return None
    tp = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(-period, 0)]
    sma_tp = sum(tp) / period
    mad = sum(abs(tp[i] - sma_tp) for i in range(period)) / period
    if mad == 0:
        return 0.0
    last_tp = (highs[-1] + lows[-1] + closes[-1]) / 3
    return (last_tp - sma_tp) / (0.015 * mad)


def calc_ema(prices: List[float], period: int) -> List[Optional[float]]:
    if len(prices) < period:
        return [None] * len(prices)
    if len(prices) == period:
        # 数据刚好够 period，取 SMA 作为初始 EMA
        result = [None] * (period - 1)
        result.append(sum(prices) / period)
        return result
    result = [None] * (period - 1)
    ema = sum(prices[:period]) / period
    result.append(ema)
    k = 2 / (period + 1)
    for i in range(period, len(prices)):
        ema = prices[i] * k + ema * (1 - k)
        result.append(ema)
    return result


def calc_macd(prices: List[float], fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = calc_ema(prices, fast)
    ema_slow = calc_ema(prices, slow)
    macd_line = []
    for f, s in zip(ema_fast, ema_slow):
        if f is None or s is None:
            macd_line.append(None)
        else:
            macd_line.append(f - s)
    macd_not_none = [m for m in macd_line if m is not None]
    signal_line = calc_ema(macd_not_none, signal)
    offset = len(macd_line) - len(macd_not_none)
    signal_aligned = [None] * offset + signal_line
    histogram = []
    for m, s in zip(macd_line, signal_aligned):
        if m is None or s is None:
            histogram.append(None)
        else:
            histogram.append(m - s)
    return macd_line, signal_aligned, histogram


def calc_bollinger(closes: List[float], period: int = 20, num_std: float = 2.0):
    if len(closes) < period:
        return None, None, None
    sma = _sma(closes, period)
    std = _std(closes, period)
    if sma is None or std is None:
        return None, None, None
    upper = sma + num_std * std
    lower = sma - num_std * std
    return upper, sma, lower


def calc_kdj(highs: List[float], lows: List[float], closes: List[float],
             n: int = 9, m1: int = 3, m2: int = 3):
    k_vals, d_vals, j_vals = [], [], []
    for i in range(len(closes)):
        if i < n - 1:
            k_vals.append(50.0); d_vals.append(50.0); j_vals.append(50.0)
            continue
        low_n = min(lows[i - n + 1:i + 1])
        high_n = max(highs[i - n + 1:i + 1])
        rsv = (closes[i] - low_n) / (high_n - low_n) * 100 if high_n != low_n else 50
        k = 2 / 3 * k_vals[-1] + 1 / 3 * rsv
        d = 2 / 3 * d_vals[-1] + 1 / 3 * k
        j = 3 * k - 2 * d
        k_vals.append(k); d_vals.append(d); j_vals.append(j)
    return k_vals, d_vals, j_vals


def calc_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < 2:
        return None
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    return _sma(trs, period)


def calc_williams_r(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period:
        return None
    highest = max(highs[-period:])
    lowest = min(lows[-period:])
    if highest == lowest:
        return -50.0
    return -100 * (highest - closes[-1]) / (highest - lowest)


def calc_obv(closes: List[float], volumes: List[float]) -> Optional[float]:
    if len(closes) < 2:
        return None
    obv = 0.0
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv += volumes[i]
        elif closes[i] < closes[i - 1]:
            obv -= volumes[i]
    return obv


def calc_adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if len(closes) < period + 2:
        return None, None, None
    tr_list, plus_dm, minus_dm = [], [], []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        tr_list.append(tr)
        plus_dm.append(max(highs[i] - highs[i - 1], 0) if highs[i] - highs[i - 1] > lows[i - 1] - lows[i] else 0)
        minus_dm.append(max(lows[i - 1] - lows[i], 0) if lows[i - 1] - lows[i] > highs[i] - highs[i - 1] else 0)
    if len(tr_list) < period:
        return None, None, None
    tr_smooth = sum(tr_list[:period])
    plus_smooth = sum(plus_dm[:period])
    minus_smooth = sum(minus_dm[:period])
    plus_di_list, minus_di_list, dx_list = [], [], []
    for i in range(period, len(tr_list)):
        if tr_smooth == 0:
            tr_smooth = tr_list[i]
        else:
            tr_smooth = tr_smooth - tr_smooth / period + tr_list[i]
        plus_smooth = plus_smooth - plus_smooth / period + plus_dm[i] if plus_smooth > 0 else plus_dm[i]
        minus_smooth = minus_smooth - minus_smooth / period + minus_dm[i] if minus_smooth > 0 else minus_dm[i]
        if tr_smooth == 0:
            continue
        pdi = 100 * plus_smooth / tr_smooth
        mdi = 100 * minus_smooth / tr_smooth
        dx = 100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) != 0 else 0
        plus_di_list.append(pdi)
        minus_di_list.append(mdi)
        dx_list.append(dx)
    if len(dx_list) < period:
        return None, None, None
    adx = sum(dx_list[-period:]) / min(len(dx_list), period)
    return adx, plus_di_list[-1] if plus_di_list else None, minus_di_list[-1] if minus_di_list else None


def calc_mfi(highs: List[float], lows: List[float], closes: List[float], volumes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    tp = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    mf_pos = sum(tp[i] * volumes[i] for i in range(-period, 0) if tp[i] > tp[i - 1])
    mf_neg = sum(tp[i] * volumes[i] for i in range(-period, 0) if tp[i] < tp[i - 1])
    if mf_neg == 0:
        return 100.0
    mr = mf_pos / mf_neg
    return 100 - (100 / (1 + mr))


def calc_roc(closes: List[float], period: int = 12) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    old = closes[-period - 1]
    if old == 0:
        return None
    return ((closes[-1] - old) / old) * 100


def calc_vwap(highs: List[float], lows: List[float], closes: List[float], volumes: List[float]) -> Optional[float]:
    if not volumes or sum(volumes) == 0:
        return None
    tp = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    return sum(tp[i] * volumes[i] for i in range(len(tp))) / sum(volumes)


def calc_pivot_points(highs: List[float], lows: List[float], closes: List[float]) -> Dict[str, float]:
    if not highs or not lows or not closes:
        return {}
    h, l, c = highs[-1], lows[-1], closes[-1]
    pivot = (h + l + c) / 3
    return {
        "r1": 2 * pivot - l, "r2": pivot + (h - l), "r3": h + 2 * (pivot - l),
        "pivot": pivot,
        "s1": 2 * pivot - h, "s2": pivot - (h - l), "s3": l - 2 * (h - pivot),
    }


def calc_supertrend(highs: List[float], lows: List[float], closes: List[float],
                    period: int = 10, multiplier: float = 3.0) -> Tuple[Optional[float], Optional[str]]:
    if len(closes) < period + 1:
        return None, None
    hl2 = [(highs[i] + lows[i]) / 2 for i in range(len(closes))]
    atr_vals = [None] * len(closes)
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        window = closes[max(0, i - period + 1):i + 1]
        atr_vals[i] = sum(abs(highs[j] - lows[j]) for j in range(max(0, i - period + 1), i + 1)) / min(period, i + 1)
    final_upper, final_lower, supert = [None] * len(closes), [None] * len(closes), [None] * len(closes)
    for i in range(period, len(closes)):
        atr = atr_vals[i] if atr_vals[i] else (highs[i] - lows[i])
        upper = hl2[i] + multiplier * atr
        lower = hl2[i] - multiplier * atr
        final_upper[i] = upper
        final_lower[i] = lower
        if supert[i - 1] is None:
            supert[i] = upper if closes[i] < upper else lower
        else:
            supert[i] = (max(lower, supert[i - 1]) if closes[i] > supert[i - 1]
                         else min(upper, supert[i - 1]))
    direction = None
    if supert[-1] is not None:
        direction = "做多" if closes[-1] > supert[-1] else "做空"
    return supert[-1], direction


def calc_darvas(highs: List[float], lows: List[float], closes: List[float], period: int = 20) -> Tuple[Optional[float], Optional[float]]:
    if len(closes) < period:
        return None, None
    box_top = max(highs[-period:])
    box_bottom = min(lows[-period:])
    return box_top, box_bottom


def calc_ichimoku(highs: List[float], lows: List[float], closes: List[float],
                  conv: int = 9, base: int = 26, span_b: int = 52) -> Dict[str, Optional[float]]:
    def _highest(hist, n): return max(hist[-n:]) if len(hist) >= n else None
    def _lowest(hist, n): return min(hist[-n:]) if len(hist) >= n else None
    if len(closes) < conv:
        return {}
    tenkan = (_highest(highs, conv) + _lowest(lows, conv)) / 2
    kijun = (_highest(highs, base) + _lowest(lows, base)) / 2
    senkou_a = (tenkan + kijun) / 2
    senkou_b = (_highest(highs, span_b) + _lowest(lows, span_b)) / 2
    return {"tenkan": tenkan, "kijun": kijun, "senkou_a": senkou_a, "senkou_b": senkou_b}


def calc_momentum(closes: List[float], period: int = 10) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    return closes[-1] - closes[-period - 1]


def calc_slope(closes: List[float], period: int = 20) -> Optional[float]:
    if len(closes) < period:
        return None
    recent = closes[-period:]
    n = len(recent)
    x_mean = (n - 1) / 2
    y_mean = sum(recent) / n
    num = sum((i - x_mean) * (recent[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    if den == 0:
        return 0.0
    return num / den


# ═══════════════════════════════════════════════════════
# 指标值快照
# ═══════════════════════════════════════════════════════

def snapshot_indicators(kl: List[Dict]) -> Dict[str, Any]:
    """计算截至最后一天的完整指标快照"""
    if len(kl) < 5:
        return {}
    closes = [float(k["close"]) for k in kl]
    highs  = [float(k["high"])  for k in kl]
    lows   = [float(k["low"])   for k in kl]
    vols   = [float(k["volume"]) for k in kl]
    price  = closes[-1]
    n = len(closes)

    ma5   = _sma(closes, 5)    if n >= 5   else None
    ma10  = _sma(closes, 10)   if n >= 10  else None
    ma20  = _sma(closes, 20)   if n >= 20  else None
    ma50  = _sma(closes, 50)   if n >= 50  else None
    ma150 = _sma(closes, 150)  if n >= 150 else None
    ma200 = _sma(closes, 200)  if n >= 200 else None

    ema12 = calc_ema(closes, 12)[-1] if n >= 12 else None
    ema26 = calc_ema(closes, 26)[-1] if n >= 26 else None

    rsi14 = calc_rsi(closes, 14) if n >= 15 else None
    rsi28 = calc_rsi(closes, 28) if n >= 29 else None
    stoch_rsi = calc_stoch_rsi(closes, 14) if n >= 29 else None

    cci20  = calc_cci(highs, lows, closes, 20) if n >= 20 else None
    cci14  = calc_cci(highs, lows, closes, 14) if n >= 14 else None

    macd_l, sig_l, hist = calc_macd(closes)
    macd_v  = macd_l[-1]
    sig_v   = sig_l[-1]
    hist_v  = hist[-1]
    macd_prev = macd_l[-2] if len(macd_l) >= 2 else None
    hist_prev = hist[-2] if len(hist) >= 2 else None

    upper_bb, mid_bb, lower_bb = calc_bollinger(closes, 20, 2.0)
    bb_width = (upper_bb - lower_bb) / mid_bb if (upper_bb and lower_bb and mid_bb) else None

    k_v, d_v, j_v = calc_kdj(highs, lows, closes, 9, 3, 3)
    k_prev = k_v[-2] if len(k_v) >= 2 else None
    j_prev = j_v[-2] if len(j_v) >= 2 else None
    k_v, d_v, j_v = k_v[-1], d_v[-1], j_v[-1]

    atr14 = calc_atr(highs, lows, closes, 14) if n >= 15 else None
    atr10 = calc_atr(highs, lows, closes, 10) if n >= 11 else None

    willr = calc_williams_r(highs, lows, closes, 14) if n >= 14 else None

    adx_v, plus_di, minus_di = calc_adx(highs, lows, closes, 14)

    mfi14 = calc_mfi(highs, lows, closes, vols, 14) if n >= 15 else None

    roc12 = calc_roc(closes, 12) if n >= 13 else None
    roc6  = calc_roc(closes, 6)  if n >= 7  else None

    vwap_v = calc_vwap(highs, lows, closes, vols)

    pivots = calc_pivot_points(highs, lows, closes)

    supert_v, supert_dir = calc_supertrend(highs, lows, closes, 10, 3.0)

    darvas_top, darvas_bot = calc_darvas(highs, lows, closes, 20)

    ichimoku = calc_ichimoku(highs, lows, closes) if n >= 52 else {}

    mom10   = calc_momentum(closes, 10) if n >= 11 else None
    slope20 = calc_slope(closes, 20) if n >= 20 else None

    vol10 = _sma(vols, 10) if n >= 10 else None
    vol20 = _sma(vols, 20) if n >= 20 else None
    vol_ratio = vols[-1] / vol10 if vol10 else None
    vol_rsi = vol10 / vol20 if (vol10 and vol20 and vol20 > 0) else None

    # 趋势阶段
    stage = "?"
    if all(x is not None for x in [ma200, ma150, ma50, ma20]):
        if price > ma200 > ma150 > ma50 > ma20:
            stage = "上升趋势(第二阶段)"
        elif price < ma200 and ma200 < ma150:
            stage = "下降趋势(第四阶段)"
        elif price > ma200:
            stage = "第一阶段(底部整理)"
        else:
            stage = "第三阶段(顶部区域)"
    elif ma50 and ma20:
        stage = "上升趋势" if price > ma50 > ma20 else "下降趋势"

    ma200_trend = None
    if n >= 201:
        ma200_trend = "上升" if closes[-1] > closes[-200] else "下降"

    recent30 = closes[-30:] if n >= 30 else closes
    cur_range  = (max(recent30) - min(recent30)) / price if price > 0 else 0
    full_range = (max(closes) - min(closes)) / price if price > 0 else 0
    is_contracting = cur_range < full_range * 0.6

    high52 = max(closes[-252:]) if n >= 252 else max(closes)
    near_52w_high = bool(price >= high52 * 0.95) if high52 else False

    # MA 交叉检测（收盘价 vs 均线）
    ma5_series  = calc_ema(closes, 5) if n >= 5 else [None] * n
    ma10_series = calc_ema(closes, 10) if n >= 10 else [None] * n
    ma20_series = calc_ema(closes, 20) if n >= 20 else [None] * n
    ma50_series = calc_ema(closes, 50) if n >= 50 else [None] * n
    ma5_prev  = ma5_series[-2]  if len(ma5_series) >= 2  and ma5_series[-2]  is not None else None
    ma10_prev = ma10_series[-2] if len(ma10_series) >= 2 and ma10_series[-2] is not None else None
    ma20_prev = ma20_series[-2] if len(ma20_series) >= 2 and ma20_series[-2] is not None else None
    ma50_prev = ma50_series[-2] if len(ma50_series) >= 2 and ma50_series[-2] is not None else None

    # MACD 交叉
    macd_cross_up = bool(macd_prev is not None and sig_v is not None and
                         macd_prev is not None and macd_prev < sig_v and macd_v > sig_v)
    # MACD 柱状图由负转正
    hist_positive = bool(hist_v is not None and hist_v > 0)
    hist_turn_pos = bool(hist_prev is not None and hist_prev < 0 and hist_v >= 0)
    # EMA 交叉
    ema_cross_up = bool(ema12 is not None and ema26 is not None and
                         ma5_prev is not None and ma10_prev is not None and
                         ma5_prev <= ma10_prev and ema12 > ema26)
    # KDJ 金叉（J从下往上穿过D/K）
    kdj_cross_up = bool(k_prev is not None and j_prev is not None and
                         j_prev < k_prev and j_v > k_v and j_v < 30)
    # 价格 vs 布林带
    near_bb_lower = bool(lower_bb is not None and price <= lower_bb * 1.03)
    near_bb_upper = bool(upper_bb is not None and price >= upper_bb * 0.97)
    # 价格 vs 均线
    price_above_ma20  = bool(ma20  is not None and price > ma20)
    price_above_ma50  = bool(ma50  is not None and price > ma50)
    price_above_ma200 = bool(ma200 is not None and price > ma200)
    # 多头排列
    ma_bullish = bool(ma20 is not None and ma50 is not None and ma20 > ma50)
    ma_bearish = bool(ma20 is not None and ma50 is not None and ma20 < ma50)

    return {
        "close": price,
        "ma5": round(ma5, 2) if ma5 else None, "ma10": round(ma10, 2) if ma10 else None,
        "ma20": round(ma20, 2) if ma20 else None, "ma50": round(ma50, 2) if ma50 else None,
        "ma150": round(ma150, 2) if ma150 else None, "ma200": round(ma200, 2) if ma200 else None,
        "ema12": round(ema12, 2) if ema12 else None, "ema26": round(ema26, 2) if ema26 else None,
        "rsi14": round(rsi14, 1) if rsi14 else None,
        "rsi28": round(rsi28, 1) if rsi28 else None,
        "stoch_rsi": round(stoch_rsi, 1) if stoch_rsi else None,
        "cci20": round(cci20, 1) if cci20 else None,
        "cci14": round(cci14, 1) if cci14 else None,
        "macd": round(macd_v, 4) if macd_v else None,
        "macd_signal": round(sig_v, 4) if sig_v else None,
        "macd_hist": round(hist_v, 4) if hist_v else None,
        "macd_cross_up": macd_cross_up, "hist_positive": hist_positive, "hist_turn_pos": hist_turn_pos,
        "k": round(k_v, 1) if k_v else None,
        "d": round(d_v, 1) if d_v else None,
        "j": round(j_v, 1) if j_v else None,
        "kdj_cross_up": kdj_cross_up,
        "bb_upper": round(upper_bb, 2) if upper_bb else None,
        "bb_mid": round(mid_bb, 2) if mid_bb else None,
        "bb_lower": round(lower_bb, 2) if lower_bb else None,
        "bb_width": round(bb_width, 4) if bb_width else None,
        "near_bb_lower": near_bb_lower, "near_bb_upper": near_bb_upper,
        "atr14": round(atr14, 3) if atr14 else None,
        "atr10": round(atr10, 3) if atr10 else None,
        "williams_r": round(willr, 1) if willr else None,
        "adx": round(adx_v, 1) if adx_v else None,
        "plus_di": round(plus_di, 1) if plus_di else None,
        "minus_di": round(minus_di, 1) if minus_di else None,
        "mfi14": round(mfi14, 1) if mfi14 else None,
        "roc12": round(roc12, 2) if roc12 else None,
        "roc6": round(roc6, 2) if roc6 else None,
        "vwap": round(vwap_v, 2) if vwap_v else None,
        "pivot": round(pivots.get("pivot"), 2) if pivots.get("pivot") else None,
        "pivot_r1": round(pivots.get("r1"), 2) if pivots.get("r1") else None,
        "pivot_r2": round(pivots.get("r2"), 2) if pivots.get("r2") else None,
        "pivot_s1": round(pivots.get("s1"), 2) if pivots.get("s1") else None,
        "pivot_s2": round(pivots.get("s2"), 2) if pivots.get("s2") else None,
        "supertrend": round(supert_v, 2) if supert_v else None,
        "supertrend_dir": supert_dir,
        "darvas_top": round(darvas_top, 2) if darvas_top else None,
        "darvas_bot": round(darvas_bot, 2) if darvas_bot else None,
        "ichimoku_tenkan": round(ichimoku.get("tenkan"), 2) if ichimoku.get("tenkan") else None,
        "ichimoku_kijun": round(ichimoku.get("kijun"), 2) if ichimoku.get("kijun") else None,
        "momentum10": round(mom10, 3) if mom10 else None,
        "slope20": round(slope20, 4) if slope20 else None,
        "vol10": round(vol10, 0) if vol10 else None,
        "vol20": round(vol20, 0) if vol20 else None,
        "vol_ratio": round(vol_ratio, 2) if vol_ratio else None,
        "vol_rsi": round(vol_rsi, 2) if vol_rsi else None,
        "stage": stage,
        "ma200_trend": ma200_trend,
        "is_contracting": is_contracting,
        "near_52w_high": near_52w_high,
        "price_above_ma20": price_above_ma20, "price_above_ma50": price_above_ma50,
        "price_above_ma200": price_above_ma200,
        "ma_bullish": ma_bullish, "ma_bearish": ma_bearish,
        "ema_cross_up": ema_cross_up,
        "last_date": str(kl[-1]["date"])[:10] if kl else None,
        "n": n,
    }


# ═══════════════════════════════════════════════════════
# 选股信号检测（清晰的条件列表，修复 lambda bug）
# ═══════════════════════════════════════════════════════

SIGNALS = [
    # RSI 系列
    ("RSI_14超卖",         lambda s: s.get("rsi14") is not None and s["rsi14"] < 30,
     "RSI(14)<30 超卖区域"),
    ("RSI_14超买",         lambda s: s.get("rsi14") is not None and s["rsi14"] > 70,
     "RSI(14)>70 超买区域"),
    ("RSI_14中性偏多",      lambda s: s.get("rsi14") is not None and 50 < s["rsi14"] <= 70,
     "RSI(14)在50-70之间偏多"),
    # CCI 系列
    ("CCI_20超卖",         lambda s: s.get("cci20") is not None and s["cci20"] < -100,
     "CCI(20)<-100 超卖"),
    ("CCI_20超买",         lambda s: s.get("cci20") is not None and s["cci20"] > 100,
     "CCI(20)>100 超买"),
    ("CCI_20强势超卖",      lambda s: s.get("cci20") is not None and s["cci20"] < -150,
     "CCI(20)<-150 强势超卖"),
    # MACD 系列
    ("MACD_零轴上方",       lambda s: s.get("macd") is not None and s["macd"] > 0,
     "MACD线在零轴上方"),
    ("MACD_金叉信号线",      lambda s: s.get("macd_cross_up"),
     "MACD线由下往上穿越信号线"),
    ("MACD_柱状图转正",      lambda s: s.get("hist_turn_pos"),
     "MACD柱状图由负转正"),
    # KDJ 系列
    ("KDJ_超卖金叉",       lambda s: s.get("kdj_cross_up"),
     "KDJ 在超卖区金叉（J从下往上）"),
    ("KDJ_高位金叉",       lambda s: s.get("kdj_cross_up") and s.get("rsi14", 0) > 50,
     "KDJ 金叉且 RSI>50"),
    # 布林带
    ("布林带_下轨支撑",      lambda s: s.get("near_bb_lower"),
     "价格逼近布林下轨（3%以内）"),
    ("布林带_上轨突破",      lambda s: s.get("near_bb_upper"),
     "价格逼近布林上轨（3%以内）"),
    ("布林带_收口",         lambda s: s.get("bb_width") is not None and s["bb_width"] < 0.05,
     "布林带宽度<5%（波动收缩）"),
    # Williams %R
    ("Williams%R超卖",     lambda s: s.get("williams_r") is not None and s["williams_r"] < -80,
     "Williams%R<-80 超卖"),
    ("Williams%R超买",     lambda s: s.get("williams_r") is not None and s["williams_r"] > -20,
     "Williams%R>-20 超买"),
    # 均线结构
    ("均线_价格>MA20",      lambda s: s.get("price_above_ma20"),
     "价格在20日均线上方"),
    ("均线_价格>MA50",      lambda s: s.get("price_above_ma50"),
     "价格在50日均线上方"),
    ("均线_多头排列",       lambda s: s.get("ma_bullish"),
     "MA20>MA50 均线多头"),
    ("均线_空头排列",       lambda s: s.get("ma_bearish"),
     "MA20<MA50 均线空头"),
    ("趋势_第二阶段",       lambda s: s.get("stage") and "第二阶段" in str(s["stage"]),
     "处于上升趋势第二阶段"),
    ("趋势_下降趋势",       lambda s: s.get("stage") and "第四阶段" in str(s["stage"]),
     "处于下降趋势第四阶段（禁止买入）"),
    # EMA 交叉
    ("EMA_金叉",           lambda s: s.get("ema_cross_up"),
     "EMA12 由下往上穿越 EMA26"),
    # 量能
    ("量比_明显放大",       lambda s: s.get("vol_ratio") is not None and s["vol_ratio"] > 2.0,
     "量比>2倍 明显放量"),
    ("量比_温和放大",       lambda s: s.get("vol_ratio") is not None and s["vol_ratio"] > 1.5,
     "量比>1.5倍 温和放量"),
    ("量比_缩量",          lambda s: s.get("vol_ratio") is not None and s["vol_ratio"] < 0.6,
     "量比<0.6倍 缩量"),
    ("量能RSI_放量",       lambda s: s.get("vol_rsi") is not None and s["vol_rsi"] > 1.5,
     "近10日均量>近20日均量1.5倍"),
    # 动量
    ("Momentum_正值",      lambda s: s.get("momentum10") is not None and s["momentum10"] > 0,
     "10日动量为正"),
    ("ROC_正增长",         lambda s: s.get("roc12") is not None and s["roc12"] > 0,
     "ROC(12)>0 价格动量为正"),
    # 52周
    ("52周_接近新高",       lambda s: s.get("near_52w_high"),
     "价格接近252日高点"),
    # VCP
    ("VCP_波动收缩",       lambda s: s.get("is_contracting"),
     "近30日波动<全程波动的60%"),
    # Supertrend
    ("Supertrend_做多",    lambda s: s.get("supertrend_dir") == "做多",
     "Supertrend 方向为做多"),
    ("Supertrend_做空",    lambda s: s.get("supertrend_dir") == "做空",
     "Supertrend 方向为做空"),
    # ADX
    ("ADX_强趋势",        lambda s: s.get("adx") is not None and s["adx"] > 25,
     "ADX>25 趋势明确"),
    ("ADX_+DI上穿-DI",    lambda s: s.get("plus_di") is not None and s.get("minus_di") is not None and s["plus_di"] > s["minus_di"],
     "+DI 上穿 -DI 趋势向上"),
    # MFI
    ("MFI_超卖",          lambda s: s.get("mfi14") is not None and s["mfi14"] < 20,
     "MFI<20 资金流超卖"),
    ("MFI_超买",          lambda s: s.get("mfi14") is not None and s["mfi14"] > 80,
     "MFI>80 资金流超买"),
    # ATR 波动率
    ("ATR_高波动",         lambda s: s.get("atr14") is not None and s.get("close") and (s["atr14"] / s["close"]) > 0.03,
     "ATR/价格>3% 高波动环境"),
    # StochRSI
    ("StochRSI_超卖",     lambda s: s.get("stoch_rsi") is not None and s["stoch_rsi"] < 20,
     "StochRSI<20 超卖"),
    ("StochRSI_超买",     lambda s: s.get("stoch_rsi") is not None and s["stoch_rsi"] > 80,
     "StochRSI>80 超买"),
    # ROC 强势
    ("ROC_强势上涨",       lambda s: s.get("roc12") is not None and s["roc12"] > 10,
     "ROC(12)>10% 强势"),
    # 趋势斜率
    ("slope20_正斜率",     lambda s: s.get("slope20") is not None and s["slope20"] > 0,
     "20日价格斜率为正"),
    # Pivot
    ("Pivot_接近R1",       lambda s: s.get("pivot_r1") and s.get("close") and s["close"] >= s["pivot_r1"] * 0.97,
     "价格接近第一阻力位"),
    ("Pivot_接近S1",       lambda s: s.get("pivot_s1") and s.get("close") and s["close"] <= s["pivot_s1"] * 1.03,
     "价格接近第一支撑位"),
    # Darvas
    ("Darvas_接近箱顶",     lambda s: s.get("darvas_top") and s.get("close") and s["close"] >= s["darvas_top"] * 0.97,
     "价格接近Darvas箱顶"),
    # Ichimoku
    ("Ichimoku_转多",      lambda s: s.get("ichimoku_tenkan") is not None and s.get("ichimoku_kijun") is not None and s["ichimoku_tenkan"] > s["ichimoku_kijun"],
     "转换线>基准线 云层转多"),
]

SIGNAL_NAMES = [s[0] for s in SIGNALS]

# 默认选股条件（参考 sepa_vcp_app.py：CCI + 布林带 + RSI）
DEFAULT_SIGNALS = ["CCI_20超卖", "CCI_20强势超卖", "布林带_下轨支撑",
                   "RSI_14超卖", "MACD_金叉信号线", "MACD_柱状图转正",
                   "KDJ_超卖金叉", "EMA_金叉", "VCP_波动收缩",
                   "均线_价格>MA20", "量比_明显放大"]


def detect_signals(snap: Dict[str, Any]) -> List[str]:
    """返回当前快照触发的信号名称列表"""
    if not snap:
        return []
    triggered = []
    for name, cond, desc in SIGNALS:
        try:
            if cond(snap):
                triggered.append(name)
        except Exception:
            pass
    return triggered


# ═══════════════════════════════════════════════════════
# 择时引擎（修复：全部使用腾讯财经 API）
# ═══════════════════════════════════════════════════════

def _qq_fetch(url: str, timeout: int = 8) -> Optional[str]:
    """腾讯财经 fetch（国内可直连）"""
    if not REQUESTS_OK:
        return None
    try:
        r = requests.get(url, timeout=timeout, headers={
            "Referer": "https://finance.qq.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        })
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return None


def fetch_tencent_realtime(codes: List[str]) -> Dict[str, Dict]:
    """腾讯财经批量实时行情（国内直连）"""
    if not codes or not REQUESTS_OK:
        return {}
    batch = ",".join(codes)
    text = _qq_fetch(f"https://qt.gtimg.cn/q={batch}")
    if not text:
        return {}
    result = {}
    for line in text.strip().split("\n"):
        if "=" not in line:
            continue
        key_part = line.split("=")[0].strip().replace("v_", "")
        rest = line.split("=", 1)[1].strip().strip('"')
        fields = rest.split("~")
        if len(fields) < 40:
            continue
        try:
            price = float(fields[3]) if fields[3] else None
            chg_pct = float(fields[32]) if fields[32] else 0.0
            result[key_part.upper()] = {"price": price, "chg_pct": chg_pct, "name": fields[1]}
        except Exception:
            continue
    return result


def fetch_tencent_index_kline(ticker: str, days: int = 300) -> Optional[pd.DataFrame]:
    """腾讯财经日K线（用于个股，指数用东方财富）"""
    today = pd.Timestamp.today().strftime("%Y%m%d")
    start = (pd.Timestamp.today() - pd.Timedelta(days=days)).strftime("%Y%m%d")
    text = _qq_fetch(
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=kline_dayqfq&param={ticker},day,{start},{today},500,qfq"
    )
    if not text:
        return None
    try:
        d = json.loads(text.replace("kline_dayqfq=", "", 1))
        data = d.get("data")
        if isinstance(data, list) and len(data) == 0:
            return None
        if isinstance(data, dict):
            key = list(data.keys())[0]
            klines = data[key].get("day") or data[key].get("qfqday") or []
        elif isinstance(data, list) and len(data) > 0:
            # 单日数据
            klines = data[0].get("day") if isinstance(data[0], dict) else data
        else:
            return None
        if not klines:
            return None
        rows = [{"date": k[0], "close": float(k[2]), "open": float(k[1]),
                 "high": float(k[3]), "low": float(k[4]), "volume": float(k[5])}
                for k in klines]
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        return df
    except Exception:
        return None


def fetch_quantclass_signal() -> Dict[str, Any]:
    """获取 quantclass.cn 黑色星期四策略信号"""
    url = "https://api.quantclass.cn/index/hs300-weekday/data"
    try:
        r = requests.get(url, timeout=8, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.quantclass.cn/service/stock/strategy/timing/hs300-weekday",
        })
        if r.status_code != 200:
            return {}
        d = r.json()
        sig = int(float(d.get("signal", -1)))  # 0=熊市, 1=牛市
        annual = d.get("annual", "N/A")
        today_ret = d.get("today", None)
        data_date = d.get("data_date", "")
        # 历史信号（最近5条）
        # hist2: [[end_date, signal_date, signal, return], ...]
        hist2 = d.get("hist2", []) or []
        recent_signals = []
        for row in hist2[:5]:
            if isinstance(row, (list, tuple)) and len(row) >= 4:
                recent_signals.append({
                    "date": str(row[0]),
                    "signal": "看涨" if float(row[2]) == 1 else "看跌",
                    "return_pct": f"{float(row[3])*100:.2f}%",
                    "market": "牛市" if float(row[2]) == 1 else "熊市"
                })
        # 关键统计数据
        recent_stats = d.get("recent", {})
        stats_summary = []
        if isinstance(recent_stats, dict):
            for k, v in recent_stats.items():
                if isinstance(v, dict) and "name" in v and "value" in v:
                    stats_summary.append({"name": v["name"], "value": v["value"]})
        return {
            "signal": sig,
            "signal_desc": "看涨" if sig == 1 else "看跌",
            "market_type": "牛市" if sig == 1 else "熊市",
            "annual": annual,
            "today_return": today_ret,
            "data_date": data_date,
            "stats": stats_summary,
            "recent_signals": recent_signals,
        }
    except Exception:
        return {}


def fetch_hk_index_kline(code: str, days: int = 500) -> Optional[pd.DataFrame]:
    """QQ Finance 港股指数日K线（国内可直连，返回前复权日线）"""
    # 东方财富 push2his 不支持港股指数，改用 QQ Finance
    # hkHSI=恒生指数, hkHSTECH=恒生科技指数
    url = (
        f"https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
        f"?_var=kline_dayfq&param={code},day,,,{days},qfq"
    )
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return None
        text = r.text
        if text.startswith("kline_dayfq="):
            text = text[len("kline_dayfq="):]
        d = json.loads(text)
        raw = d.get("data", {}).get(code, {}).get("day", [])
        if not raw:
            return None
        rows = []
        for k in raw:
            if len(k) >= 6:
                rows.append({
                    "date": k[0],
                    "open": float(k[1]),
                    "close": float(k[2]),
                    "high": float(k[3]),
                    "low": float(k[4]),
                    "volume": float(k[5]) if k[5] not in ("", None) else 0.0,
                })
        if not rows:
            return None
        df = pd.DataFrame(rows)
        # 过滤未来日期（QQ 有时返回假数据）
        df["date"] = pd.to_datetime(df["date"])
        df = df[df["date"] <= pd.Timestamp.today()]
        return df
    except Exception:
        return None


def fetch_eastmoney_index_kline(code: str, days: int = 300) -> Optional[pd.DataFrame]:
    """东方财富A股指数K线（国内可直连）
    港股指数（恒生/恒生科技）改用 fetch_hk_index_kline（QQ Finance）
    """
    # 港股指数走 QQ Finance（东方财富不支持）
    if code in ("hkHSI", "hkHSTECH"):
        return fetch_hk_index_kline(code, days=days)

    today = pd.Timestamp.today().strftime("%Y%m%d")
    start = (pd.Timestamp.today() - pd.Timedelta(days=days)).strftime("%Y%m%d")
    secid_map = {"sh000300": "1.000300"}
    secid = secid_map.get(code)
    if not secid:
        return None
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&klt=101&fqt=1&beg={start}&end={today}&smplmt=460&lmt=1000000"
    )
    if not REQUESTS_OK:
        return None
    try:
        r = requests.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://data.eastmoney.com/",
        })
        if r.status_code != 200:
            return None
        d = r.json()
        klines = d.get("data", {}).get("klines", [])
        if not klines:
            return None
        rows = []
        for k in klines:
            parts = k.split(",")
            if len(parts) >= 6:
                rows.append({
                    "date": parts[0], "open": float(parts[1]),
                    "close": float(parts[2]), "high": float(parts[3]),
                    "low": float(parts[4]), "volume": float(parts[5])
                })
        df = pd.DataFrame(rows)
        return df
    except Exception:
        return None


def calc_ma_timing(closes: List[float], dates: List[str], period: int = 200) -> Dict[str, Any]:
    """均线择时：累积和算法保证窗口一致性，检测所有 MA 穿越信号"""
    n = len(closes)
    if n < 5:
        return {"signal": "数据不足", "ma": None, "close": closes[-1] if closes else None,
                "pct_above": None, "above": None, "period": n, "recent_signals": []}

    # 累积和：cum[i+1] = sum(closes[0..i])
    cum = [0.0]
    for c in closes:
        cum.append(cum[-1] + c)

    # 计算每个有足够数据的日期的 MA 和信号
    p = min(period, n)
    ma_signals = []  # [(date_str, above)]  从 period 索引开始
    for i in range(p - 1, n):
        ma_val = (cum[i + 1] - cum[i + 1 - p]) / p
        above = closes[i] > ma_val
        ma_signals.append((str(dates[i])[:10], above, ma_val))

    if not ma_signals:
        return {"signal": "数据不足", "ma": None, "close": round(closes[-1], 2),
                "pct_above": None, "above": None, "period": p, "recent_signals": []}

    # 检测 flip（信号切换）
    flips = []
    for i in range(1, len(ma_signals)):
        prev_ab = ma_signals[i - 1][1]
        cur_ab  = ma_signals[i][1]
        if prev_ab != cur_ab:
            day_idx = p - 1 + i      # 在 closes 中的索引
            ret = (closes[day_idx - 1] - closes[day_idx]) / closes[day_idx] * 100 if day_idx > 0 else None
            flips.append({
                "date": ma_signals[i][0],
                "signal": "看涨" if cur_ab else "看跌",
                "return_pct": f"{ret:+.2f}%" if ret is not None else "—",
            })

    # 当前 MA 和价格
    cur_ma = ma_signals[-1][2]
    price = closes[-1]
    above = ma_signals[-1][1]

    return {
        "signal": "看涨(持有)" if above else "看跌(空仓)",
        "ma": round(cur_ma, 2),
        "close": round(price, 2),
        "pct_above": round((price / cur_ma - 1) * 100, 2),
        "above": above,
        "period": p,
        "recent_signals": list(reversed(flips[-5:])),
    }


def calc_ma_cross_timing(df: pd.DataFrame, short: int = 5, long: int = 20) -> Dict[str, Any]:
    """均线交叉择时：短期均线上穿长期均线 = 看涨"""
    if df.empty or len(df) < long + 1:
        return {"signal": "数据不足", "short_ma": None, "long_ma": None, "recent_signals": []}
    closes = df["close"].values.tolist()
    dates  = df["date"].values.tolist()
    if len(closes) < long:
        return {"signal": "数据不足", "short_ma": None, "long_ma": None, "recent_signals": []}
    short_ma = sum(closes[-(short):]) / short
    long_ma  = sum(closes[-(long):]) / long
    prev_short = sum(closes[-(short + 1):-1]) / short
    prev_long  = sum(closes[-(long + 1):-1]) / long
    cross_up = prev_short <= prev_long and short_ma > long_ma
    # 计算近5次交叉信号
    recent_signals = []
    for i in range(1, len(closes)):
        s_s = sum(closes[i-(short):i]) / short
        l_l = sum(closes[i-(long):i]) / long
        ps_s = sum(closes[i-(short+1):i-1]) / short if i > short else None
        ps_l = sum(closes[i-(long+1):i-1]) / long  if i > long  else None
        if ps_s is None or ps_l is None:
            continue
        prev_cross_up = ps_s <= ps_l and s_s > l_l
        if prev_cross_up != (ps_s > ps_l):
            # 交叉发生了
            ret = (closes[i-1] - closes[i]) / closes[i] * 100
            recent_signals.append({
                "date": str(dates[i])[:10] if i < len(dates) else f"T-{i}",
                "signal": "金叉(看涨)" if s_s > l_l else "死叉(看跌)",
                "return_pct": f"{ret:+.2f}%",
            })
        if len(recent_signals) >= 5:
            break
    return {
        "signal": "看涨(持有)" if cross_up else "看跌(空仓)",
        "short_ma": round(short_ma, 2),
        "long_ma": round(long_ma, 2),
        "cross_up": cross_up,
        "close": round(closes[-1], 2),
        "recent_signals": recent_signals,
    }


def calc_north_money_local() -> Dict[str, Any]:
    """
    北向资金：tushare_only.csv 历史 + akshare fund_flow_summary 今日数据
    数据源：akshare stock_hsgt_hist_em (历史) + stock_hsgt_fund_flow_summary_em (今日)
    tushare_only.csv 列: date, net_buy (单位：万元)
    """
    NORTH_TUSHARE = os.path.join(DEFAULT_CFG["north_dir"], "tushare_only.csv")
    try:
        import akshare as ak
        import pandas as pd

        # 1. 加载历史数据（tushare_only.csv, net_buy 单位万元）
        hist_df = None
        if os.path.exists(NORTH_TUSHARE):
            try:
                hist_df = pd.read_csv(NORTH_TUSHARE)
                hist_df.columns = [c.strip() for c in hist_df.columns]
                hist_df["date"] = pd.to_datetime(hist_df["date"], errors="coerce")
                hist_df["net_yi"] = pd.to_numeric(hist_df["net_buy"], errors="coerce") / 10000.0
                hist_df = hist_df.dropna(subset=["date", "net_yi"]).sort_values("date")
            except Exception:
                hist_df = None

        # 2. 获取今日数据（fund_flow_summary_em）
        summary = ak.stock_hsgt_fund_flow_summary_em()
        today_yi = 0.0
        today_date = str(pd.Timestamp.today().date())
        if summary is not None and not summary.empty:
            north_rows = summary[
                (summary["板块"].astype(str).isin(["沪股通", "深股通"])) &
                (summary["资金方向"] == "北向")
            ]
            for _, row in north_rows.iterrows():
                val = row.get("成交净买额", 0)
                if isinstance(val, (int, float)):
                    today_yi += float(val)

        # 3. 构建完整 DataFrame
        today_row = pd.DataFrame({"date": [pd.Timestamp.today()], "net_yi": [round(today_yi, 4)]})
        if hist_df is not None and not hist_df.empty:
            combined = pd.concat([hist_df[["date", "net_yi"]], today_row], ignore_index=True)
        else:
            combined = today_row
        combined = combined.drop_duplicates("date").sort_values("date").tail(60).reset_index(drop=True)
        if combined.empty:
            return {}

        # 4. 计算指标
        window = 5
        combined["cum5"] = combined["net_yi"].rolling(window, min_periods=window).sum()
        combined["ma5"] = combined["net_yi"].rolling(window, min_periods=window).mean()
        last = combined.iloc[-1]
        last_date = str(last["date"].date())
        today_net = round(float(last["net_yi"]), 2)
        cum_net = round(float(last["cum5"]), 2) if pd.notna(last["cum5"]) else 0.0
        ma_net = round(float(last["ma5"]), 2) if pd.notna(last["ma5"]) else 0.0

        # 5. 信号
        if cum_net > 50:
            signal = "看涨"
        elif cum_net < -50:
            signal = "看跌"
        elif cum_net > 20:
            signal = "看涨"
        elif cum_net < -20:
            signal = "看跌"
        else:
            signal = "震荡"

        # 6. 近5次翻转
        recent_signals = []
        for i in range(1, len(combined)):
            p = combined.iloc[i-1]["net_yi"] > 0
            c = combined.iloc[i]["net_yi"] > 0
            if p != c:
                recent_signals.append({
                    "date": str(combined.iloc[i]["date"])[:10],
                    "signal": "看涨" if c else "看跌",
                    "net_buy": round(float(combined.iloc[i]["net_yi"]), 2),
                })
        recent_signals = list(reversed(recent_signals[-20:]))
        if len(recent_signals) < 5:
            for _, row in combined.tail(5).iloc[::-1].iterrows():
                recent_signals.append({
                    "date": str(row["date"])[:10],
                    "signal": "看涨" if row["net_yi"] > 0 else "看跌",
                    "net_buy": round(float(row["net_yi"]), 2),
                })

        return {
            "today_net": today_net,
            "cum_net": cum_net,
            "ma_net": ma_net,
            "signal": signal,
            "last_date": last_date,
            "recent_signals": recent_signals[:5],
        }
    except Exception:
        return {}


"""Missing sections for daily_report_app.py"""
# This file is appended to the first 1261 lines of the truncated file

def calc_south_money_local() -> Dict[str, Any]:
    try:
        import akshare as ak
        import pandas as pd
        summary = ak.stock_hsgt_fund_flow_summary_em()
        today_yi = 0.0
        if summary is not None and not summary.empty:
            south_rows = summary[
                (summary["板块"].astype(str).isin(["港股通(沪)", "港股通(深)"])) &
                (summary["资金方向"] == "南向")
            ]
            for _, row in south_rows.iterrows():
                val = row.get("成交净买额", 0)
                if isinstance(val, (int, float)):
                    today_yi += float(val)
        south_csv = os.path.join(DEFAULT_CFG.get("south_dir", "/tmp"), "south_fund.csv")
        hist_df = None
        if os.path.exists(south_csv):
            try:
                hist_df = pd.read_csv(south_csv)
                hist_df.columns = [c.strip() for c in hist_df.columns]
                hist_df["date"] = pd.to_datetime(hist_df["date"], errors="coerce")
                hist_df["net_yi"] = pd.to_numeric(hist_df["net_buy"], errors="coerce") / 10000.0
                hist_df = hist_df.dropna(subset=["date", "net_yi"]).sort_values("date")
            except Exception:
                hist_df = None
        today_row = pd.DataFrame({"date": [pd.Timestamp.today()], "net_yi": [round(today_yi, 4)]})
        if hist_df is not None and not hist_df.empty:
            combined = pd.concat([hist_df[["date", "net_yi"]], today_row], ignore_index=True)
        else:
            combined = today_row
        combined = combined.drop_duplicates("date").sort_values("date").tail(60).reset_index(drop=True)
        if combined.empty:
            return {}
        window = 5
        combined["cum5"] = combined["net_yi"].rolling(window, min_periods=window).sum()
        combined["ma5"] = combined["net_yi"].rolling(window, min_periods=window).mean()
        last = combined.iloc[-1]
        last_date = str(last["date"].date())
        today_net = round(float(last["net_yi"]), 2)
        cum_net = round(float(last["cum5"]), 2) if pd.notna(last["cum5"]) else 0.0
        ma_net = round(float(last["ma5"]), 2) if pd.notna(last["ma5"]) else 0.0
        if cum_net > 80:
            signal = "看涨"
        elif cum_net < -80:
            signal = "看跌"
        elif cum_net > 30:
            signal = "看涨"
        elif cum_net < -30:
            signal = "看跌"
        else:
            signal = "震荡"
        recent_signals = []
        for i in range(1, len(combined)):
            p = combined.iloc[i-1]["net_yi"] > 0
            c = combined.iloc[i]["net_yi"] > 0
            if p != c:
                recent_signals.append({
                    "date": str(combined.iloc[i]["date"])[:10],
                    "signal": "看涨" if c else "看跌",
                    "net_buy": round(float(combined.iloc[i]["net_yi"]), 2),
                })
        recent_signals = list(reversed(recent_signals[-20:]))
        if len(recent_signals) < 5:
            for _, row in combined.tail(5).iloc[::-1].iterrows():
                recent_signals.append({
                    "date": str(row["date"])[:10],
                    "signal": "看涨" if row["net_yi"] > 0 else "看跌",
                    "net_buy": round(float(row["net_yi"]), 2),
                })
        return {
            "today_net": today_net, "cum_net": cum_net, "ma_net": ma_net,
            "signal": signal, "last_date": last_date, "recent_signals": recent_signals[:5],
        }
    except Exception:
        return {}


def fetch_vix() -> Dict[str, Any]:
    try:
        import requests
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX"
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        r = requests.get(url, timeout=8, headers=headers)
        if r.status_code == 200:
            d = r.json()
            result = d.get("chart", {}).get("result", [])
            if result:
                meta = result[0].get("meta", {})
                current_price = meta.get("regularMarketPrice")
                prev_close = meta.get("previousClose")
                return {"vix": round(float(current_price), 2) if current_price else None,
                        "prev_close": round(float(prev_close), 2) if prev_close else None,
                        "source": "Yahoo Finance"}
    except Exception:
        pass
    try:
        df = fetch_eastmoney_index_kline("sh000300", days=20)
        if df is not None and len(df) >= 15:
            closes = df["close"].tolist()
            highs = df["high"].tolist()
            lows = df["low"].tolist()
            atr = calc_atr(highs, lows, closes, 14)
            if atr and closes[-1]:
                vix_est = round((atr / closes[-1]) * 100 * 4, 1)
                return {"vix": vix_est, "source": "沪深300ATR估算(备选)", "prev_close": None}
    except Exception:
        pass
    return {}


def run_timing(force: bool = False) -> Dict[str, Any]:
    cache_key = "/tmp/timing_cache_v3.json"
    cache_ttl = 30 * 60
    if not force:
        try:
            if os.path.exists(cache_key):
                age = time.time() - os.path.getmtime(cache_key)
                if age < cache_ttl:
                    with open(cache_key) as f:
                        return json.load(f)
        except Exception:
            pass
    result: Dict[str, Any] = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    try:
        df = fetch_eastmoney_index_kline("sh000300", days=300)
        if df is not None and not df.empty:
            closes = df["close"].tolist()
            dates = df["date"].tolist()
            result["hs300_ma200"] = calc_ma_timing(closes, dates, period=200)
        else:
            result["hs300_ma200"] = {"signal": "数据获取失败"}
    except Exception as e:
        result["hs300_ma200"] = {"signal": f"错误: {e}"}
    try:
        df_hk = fetch_hk_index_kline("hkHSI", days=500)
        if df_hk is not None and not df_hk.empty:
            result["hk_hs300"] = calc_ma_timing(df_hk["close"].tolist(), df_hk["date"].dt.strftime("%Y-%m-%d").tolist(), period=200)
        else:
            result["hk_hs300"] = {"signal": "数据获取失败"}
    except Exception as e:
        result["hk_hs300"] = {"signal": f"错误: {e}"}
    try:
        df_ht = fetch_hk_index_kline("hkHSTECH", days=500)
        if df_ht is not None and not df_ht.empty:
            result["hk_hstech"] = calc_ma_timing(df_ht["close"].tolist(), df_ht["date"].dt.strftime("%Y-%m-%d").tolist(), period=200)
        else:
            result["hk_hstech"] = {"signal": "数据获取失败"}
    except Exception as e:
        result["hk_hstech"] = {"signal": f"错误: {e}"}
    try:
        result["black_thursday"] = fetch_quantclass_signal()
    except Exception:
        result["black_thursday"] = {}
    try:
        result["vix"] = fetch_vix()
    except Exception:
        result["vix"] = {}
    try:
        result["north_money"] = calc_north_money_local()
    except Exception:
        result["north_money"] = {}
    try:
        result["south_money"] = calc_south_money_local()
    except Exception:
        result["south_money"] = {}
    try:
        result["realtime"] = fetch_tencent_realtime(["sh000300", "sh000001", "sz399001", "hkHSI", "hkHSTECH"])
    except Exception:
        result["realtime"] = {}
    try:
        with open(cache_key, "w") as f:
            json.dump(result, f, ensure_ascii=False, default=str)
    except Exception:
        pass
    return result


def _signal_to_方向(sig):
    if not sig:
        return None
    sig_str = str(sig)
    if any(x in sig_str for x in ["看涨", "持有", "看多", "买入"]):
        return "看涨"
    if any(x in sig_str for x in ["看跌", "空仓", "看空", "卖出"]):
        return "看跌"
    return "震荡"


def _votes(signals):
    counts = {"看涨": 0, "看跌": 0, "震荡": 0}
    for s in signals:
        d = _signal_to_方向(s)
        if d:
            counts[d] += 1
    if not any(counts.values()):
        return "震荡"
    return max(counts, key=counts.get)


def build_timing_predictions(raw: Dict[str, Any]) -> Dict[str, Any]:
    predictions: Dict[str, Any] = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    sub = {}

    hs300 = raw.get("hs300_ma200", {})
    hs300_pct = hs300.get("pct_above")
    if hs300_pct is not None and abs(hs300_pct) > 5:
        hs300_conf, hs300_reason = "高置信", f"价格偏离MA200 {hs300_pct:+.2f}%，趋势明确"
    elif hs300.get("signal"):
        hs300_conf, hs300_reason = "中置信", f"MA200择时信号：{hs300.get('signal')}"
    else:
        hs300_conf, hs300_reason = "低置信", "数据不足"
    sub["hs300_ma200"] = {
        "prediction": _signal_to_方向(hs300.get("signal", "")) or "震荡",
        "confidence": hs300_conf, "reasoning": hs300_reason,
        "detail": hs300.get("signal", ""), "pct_above": hs300_pct,
    }

    north = raw.get("north_money", {})
    cum_net = north.get("cum_net", 0)
    if cum_net > 50:
        north_pred, north_conf, north_reason = "看涨", "高置信", f"5日累计净买入 {cum_net:.1f}亿元，资金大幅流入"
    elif cum_net > 20:
        north_pred, north_conf, north_reason = "看涨", "中置信", f"5日累计净买入 {cum_net:.1f}亿元，资金温和流入"
    elif cum_net < -50:
        north_pred, north_conf, north_reason = "看跌", "高置信", f"5日累计净卖出 {abs(cum_net):.1f}亿元，资金大幅流出"
    elif cum_net < -20:
        north_pred, north_conf, north_reason = "看跌", "中置信", f"5日累计净卖出 {abs(cum_net):.1f}亿元，资金温和流出"
    else:
        north_pred, north_conf, north_reason = "震荡", "低置信", f"5日累计 {cum_net:.1f}亿元，方向不明"
    sub["north_money"] = {
        "prediction": north_pred, "confidence": north_conf, "reasoning": north_reason,
        "detail": f"今日净买入 {north.get('today_net', 0):.2f}亿 | 5日均线 {north.get('ma_net', 0):.2f}亿",
        "cum_net": cum_net, "today_net": north.get("today_net", 0),
    }

    bt = raw.get("black_thursday", {})
    bt_sig = bt.get("signal")
    if bt_sig == 1:
        bt_pred, bt_conf, bt_reason = "看涨", "中置信", f"黑色星期四策略：{bt.get('market_type', '牛市')}信号，年化收益 {bt.get('annual', 'N/A')}%"
    elif bt_sig == 0:
        bt_pred, bt_conf, bt_reason = "看跌", "中置信", f"黑色星期四策略：{bt.get('market_type', '熊市')}信号"
    else:
        bt_pred, bt_conf, bt_reason = "震荡", "低置信", "黑色星期四信号获取失败"
    sub["black_thursday"] = {
        "prediction": _signal_to_方向(bt_pred) if bt_pred != "震荡" else "震荡",
        "confidence": bt_conf, "reasoning": bt_reason,
        "detail": bt.get("signal_desc", ""), "annual": bt.get("annual", ""),
        "recent_signals": bt.get("recent_signals", []),
    }

    vix_data = raw.get("vix", {})
    vix_val = vix_data.get("vix")
    if vix_val is not None:
        if vix_val >= 30:
            vix_pred, vix_conf, vix_reason = "看跌", "高置信", f"VIX={vix_val:.1f}，市场恐慌情绪极高，风险资产承压"
        elif vix_val >= 25:
            vix_pred, vix_conf, vix_reason = "看跌", "中置信", f"VIX={vix_val:.1f}，市场波动较大，谨慎观望"
        elif vix_val >= 15:
            vix_pred, vix_conf, vix_reason = "震荡", "中置信", f"VIX={vix_val:.1f}，市场正常波动"
        else:
            vix_pred, vix_conf, vix_reason = "看涨", "中置信", f"VIX={vix_val:.1f}，市场平静，风险偏好较高"
    else:
        vix_pred, vix_conf, vix_reason = "震荡", "低置信", "VIX数据获取失败"
    sub["vix"] = {
        "prediction": vix_pred, "confidence": vix_conf, "reasoning": vix_reason,
        "detail": f"VIX={vix_val}" if vix_val is not None else "无数据",
        "source": vix_data.get("source", ""),
    }

    south = raw.get("south_money", {})
    south_cum = south.get("cum_net", 0)
    if south_cum > 80:
        south_pred, south_conf, south_reason = "看涨", "高置信", f"南向资金5日累计净买入 {south_cum:.1f}亿港元，大幅流入港股"
    elif south_cum > 30:
        south_pred, south_conf, south_reason = "看涨", "中置信", f"南向资金5日累计净买入 {south_cum:.1f}亿港元，温和流入"
    elif south_cum < -80:
        south_pred, south_conf, south_reason = "看跌", "高置信", f"南向资金5日累计净卖出 {abs(south_cum):.1f}亿港元，大幅流出"
    elif south_cum < -30:
        south_pred, south_conf, south_reason = "看跌", "中置信", f"南向资金5日累计净卖出 {abs(south_cum):.1f}亿港元，温和流出"
    else:
        south_pred, south_conf, south_reason = "震荡", "低置信", f"南向资金5日累计 {south_cum:.1f}亿港元，方向不明"
    sub["south_money"] = {
        "prediction": south_pred, "confidence": south_conf, "reasoning": south_reason,
        "detail": f"今日净买入 {south.get('today_net', 0):.2f}亿 | 5日均线 {south.get('ma_net', 0):.2f}亿",
        "cum_net": south_cum,
    }

    hk_ma = raw.get("hk_hs300", {})
    hk_pct = hk_ma.get("pct_above")
    if hk_pct is not None and abs(hk_pct) > 5:
        hk_conf, hk_reason = "高置信", f"恒生指数价格偏离MA200 {hk_pct:+.2f}%，趋势明确"
    elif hk_ma.get("signal"):
        hk_conf, hk_reason = "中置信", f"恒生指数MA200择时：{hk_ma.get('signal')}"
    else:
        hk_conf, hk_reason = "低置信", "数据不足"
    sub["hk_ma200"] = {
        "prediction": _signal_to_方向(hk_ma.get("signal", "")) or "震荡",
        "confidence": hk_conf, "reasoning": hk_reason,
        "detail": hk_ma.get("signal", ""), "pct_above": hk_pct,
    }

    predictions["sub_predictions"] = sub

    a股_signals = [
        sub["north_money"]["prediction"], sub["black_thursday"]["prediction"],
        sub["vix"]["prediction"], sub["hs300_ma200"]["prediction"],
    ]
    a股_final = _votes(a股_signals)
    a股_conf_values = [sub["north_money"]["confidence"], sub["black_thursday"]["confidence"],
                       sub["vix"]["confidence"], sub["hs300_ma200"]["confidence"]]
    high_count = a股_conf_values.count("高置信")
    if high_count >= 2:
        a股_final_conf = "高置信"
    elif high_count == 1 or a股_conf_values.count("中置信") >= 2:
        a股_final_conf = "中置信"
    else:
        a股_final_conf = "低置信"
    predictions["a_share"] = {
        "prediction": a股_final, "confidence": a股_final_conf,
        "reasoning": f"综合 {len(a股_signals)} 项指标投票：{a股_final}（{' '.join(a股_signals)}）",
        "votes": dict(zip(["北向资金", "黑色星期四", "VIX", "沪深300MA200"], a股_signals)),
    }

    hk_signals = [sub["south_money"]["prediction"], sub["hk_ma200"]["prediction"]]
    hk_final = _votes(hk_signals)
    hk_conf_values = [sub["south_money"]["confidence"], sub["hk_ma200"]["confidence"]]
    if "高置信" in hk_conf_values:
        hk_final_conf = "高置信"
    elif "中置信" in hk_conf_values:
        hk_final_conf = "中置信"
    else:
        hk_final_conf = "低置信"
    predictions["hk_share"] = {
        "prediction": hk_final, "confidence": hk_final_conf,
        "reasoning": f"综合南向资金和恒生MA200投票：{hk_final}",
        "votes": dict(zip(["南向资金", "恒生MA200"], hk_signals)),
    }
    return predictions


from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

app = FastAPI(title="哮天每日收盘报告", version="3.0")


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_CONTENT


@app.get("/api/timing")
async def get_timing():
    try:
        return JSONResponse(run_timing())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/timing/refresh")
async def refresh_timing():
    try:
        return JSONResponse({"status": "ok", "data": run_timing(force=True)})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/timing/predictions")
async def get_predictions():
    try:
        raw = run_timing()
        return JSONResponse(build_timing_predictions(raw))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/timing/realtime")
async def get_realtime():
    try:
        return JSONResponse(run_timing().get("realtime", {}))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/timing/ma")
async def get_ma():
    try:
        raw = run_timing()
        return JSONResponse({"hs300": raw.get("hs300_ma200", {}), "hk": raw.get("hk_hs300", {}), "hk_tech": raw.get("hk_hstech", {})})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/timing/fund")
async def get_fund():
    try:
        raw = run_timing()
        return JSONResponse({"north": raw.get("north_money", {}), "south": raw.get("south_money", {})})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/timing/vix")
async def get_vix():
    try:
        return JSONResponse(run_timing().get("vix", {}))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/timing/black_thursday")
async def get_black_thursday():
    try:
        return JSONResponse(run_timing().get("black_thursday", {}))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/timing")
async def post_timing(request: Request):
    try:
        body = await request.json()
        return JSONResponse(run_timing(force=body.get("force", False)))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/generate_report")
async def generate_report(request: Request):
    try:
        body = await request.json()
        return HTMLResponse(render_report_html(body.get("scan_results", []), run_timing()))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stocks")
async def search_stocks(q: str = ""):
    if not q or len(q) < 1:
        return JSONResponse([])
    _load_name_map()
    results = []
    q_upper = q.upper()
    for code, name in list(_NAME_CACHE.items())[:200]:
        if q_upper in code.upper() or q_upper in name.upper():
            results.append({"code": code, "name": name, "sector": _SECTOR_CACHE.get(code, "—")})
            if len(results) >= 20:
                break
    return JSONResponse(results)


@app.post("/api/scan")
async def run_stock_scan(request: Request):
    try:
        body = await request.json() or {}
        signal_filter = body.get("signals", [])
        market = body.get("market", "A")
        cfg = DEFAULT_CFG
        kline_dir = cfg["a_kline_dir"] if market in ("A", "SZ", "SS") else cfg["hk_kline_dir"]
        csv_files = list_csv(kline_dir)
        results = []
        for fname in csv_files[:500]:
            if market == "SZ" and not fname.startswith("SZ"):
                continue
            if market == "SS" and not fname.startswith("SH"):
                continue
            path = os.path.join(kline_dir, fname)
            kl = load_klines(path)
            if len(kl) < 60:
                continue
            snap = snapshot_indicators(kl)
            signals = detect_signals(snap)
            if signal_filter and not any(s in signals for s in signal_filter):
                continue
            rsi = snap.get("rsi14", 0) or 0
            if rsi < body.get("min_rsi", 0) or rsi > body.get("max_rsi", 100):
                continue
            code = fname.replace(".csv", "")
            results.append({"code": code, "signals": signals, "snapshot": snap})
            if len(results) >= 100:
                break
        enrich_names(results)
        return JSONResponse(results)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sepa")
async def sepa_analysis(code: str = ""):
    try:
        if not code:
            raise HTTPException(status_code=400, detail="股票代码不能为空")
        for d, _ in [(DEFAULT_CFG["a_kline_dir"], ""), (DEFAULT_CFG["hk_kline_dir"], "")]:
            for fname in list_csv(d):
                if code.upper() in fname.upper():
                    kl = load_klines(os.path.join(d, fname))
                    if len(kl) >= 60:
                        snap = snapshot_indicators(kl)
                        signals = detect_signals(snap)
                        result_code = fname.replace(".csv", "")
                        return JSONResponse({
                            "code": result_code, "name": _NAME_CACHE.get(result_code, result_code),
                            "snapshot": snap, "signals": signals,
                        })
        raise HTTPException(status_code=404, detail=f"未找到股票 {code}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def render_report_html(scan_results, timing_data):
    date_str = datetime.now().strftime("%Y-%m-%d")
    predictions = build_timing_predictions(timing_data)
    a_share = predictions.get("a_share", {})
    hk_share = predictions.get("hk_share", {})
    bull_c, bear_c, mid_c = "#3ddc84", "#ff6b6b", "#888aa0"
    def pc(p):
        return bull_c if p == "看涨" else bear_c if p == "看跌" else mid_c
    sub = predictions.get("sub_predictions", {})
    html = (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><title>每日报告 ' + date_str + '</title>'
        '<style>'
        'body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0f1117;color:#c5c9d8;padding:20px;margin:0}'
        'h1{color:#e07c3e;font-size:24px;margin:0 0 20px}'
        'h2{color:#e07c3e;font-size:16px;margin:20px 0 10px}'
        '.section{background:#171b26;border-radius:10px;padding:20px;margin:15px 0;border:1px solid #2a2f3e}'
        '.flex{display:flex;gap:20px;flex-wrap:wrap}'
        '.card{background:#1e2330;border-radius:8px;padding:15px;flex:1;min-width:280px}'
        '.prediction{font-size:28px;font-weight:700}'
        '.bull{color:' + bull_c + '}.bear{color:' + bear_c + '}.mid{color:' + mid_c + '}'
        'table{width:100%;border-collapse:collapse;font-size:13px}'
        'th,td{padding:8px 12px;text-align:left;border-bottom:1px solid #2a2f3e}'
        'th{color:#8890a4;font-weight:600}'
        '.kv{display:flex;justify-content:space-between;padding:4px 0;font-size:13px}'
        '.footer{text-align:center;padding:20px;color:#8890a4;font-size:11px}'
        '</style></head><body>'
        '<h1>🐕 哮天每日收盘报告 ' + date_str + '</h1>'
        '<div class="flex">'
        '<div class="card"><h2>🇨🇳 A股综合预测</h2>'
        '<div class="prediction" style="color:' + pc(a_share.get('prediction', '震荡')) + '">' + str(a_share.get('prediction', '--')) + '</div>'
        '<div style="font-size:14px;margin-top:8px">' + str(a_share.get('confidence', '--')) + '</div>'
        '<div style="font-size:12px;color:#8890a4;margin-top:8px">' + str(a_share.get('reasoning', '')) + '</div></div>'
        '<div class="card"><h2>🇭🇰 恒生综合预测</h2>'
        '<div class="prediction" style="color:' + pc(hk_share.get('prediction', '震荡')) + '">' + str(hk_share.get('prediction', '--')) + '</div>'
        '<div style="font-size:14px;margin-top:8px">' + str(hk_share.get('confidence', '--')) + '</div>'
        '<div style="font-size:12px;color:#8890a4;margin-top:8px">' + str(hk_share.get('reasoning', '')) + '</div></div>'
        '</div>'
        '<div class="section"><h2>📊 择时摘要</h2><div class="flex">'
        '<div class="card">'
        '<div class="kv"><span>沪深300 MA200</span><span class="' + pc(timing_data.get('hs300_ma200', {}).get('signal', 'N/A')) + '">' + str(timing_data.get('hs300_ma200', {}).get('signal', 'N/A')) + '</span></div>'
        '<div class="kv"><span>恒生指数 MA200</span><span class="' + pc(timing_data.get('hk_hs300', {}).get('signal', 'N/A')) + '">' + str(timing_data.get('hk_hs300', {}).get('signal', 'N/A')) + '</span></div>'
        '<div class="kv"><span>恒生科技 MA200</span><span class="' + pc(timing_data.get('hk_hstech', {}).get('signal', 'N/A')) + '">' + str(timing_data.get('hk_hstech', {}).get('signal', 'N/A')) + '</span></div>'
        '</div><div class="card">'
        '<div class="kv"><span>北向资金</span><span class="' + pc(timing_data.get('north_money', {}).get('signal', 'N/A')) + '">' + str(timing_data.get('north_money', {}).get('signal', 'N/A')) + ' (' + '{:.1f}'.format(timing_data.get('north_money', {}).get('cum_net', 0)) + '亿)</span></div>'
        '<div class="kv"><span>南向资金</span><span class="' + pc(timing_data.get('south_money', {}).get('signal', 'N/A')) + '">' + str(timing_data.get('south_money', {}).get('signal', 'N/A')) + ' (' + '{:.1f}'.format(timing_data.get('south_money', {}).get('cum_net', 0)) + '亿)</span></div>'
        '<div class="kv"><span>VIX</span><span>' + str(timing_data.get('vix', {}).get('vix', 'N/A')) + '</span></div>'
        '<div class="kv"><span>黑色星期四</span><span class="' + pc(timing_data.get('black_thursday', {}).get('signal_desc', 'N/A')) + '">' + str(timing_data.get('black_thursday', {}).get('signal_desc', 'N/A')) + '</span></div>'
        '</div></div></div>'
        '<div class="section"><h2>📋 选股扫描 (' + str(len(scan_results)) + ' 只)</h2>'
        '<table><thead><tr><th>代码</th><th>名称</th><th>行业</th><th>RSI</th><th>CCI</th><th>MACD</th><th>趋势</th><th>信号</th></tr></thead><tbody>'
    )
    for r in scan_results[:30]:
        s = r.get("snapshot", {})
        signals = r.get("signals", [])
        rsi = s.get("rsi14", 0) or 0
        cci = s.get("cci20", 0) or 0
        macd = s.get("macd", 0) or 0
        html += '<tr><td>' + str(r.get('code', '')) + '</td><td>' + str(r.get('name', '')) + '</td><td>' + str(r.get('sector', '--')) + '</td>'
        html += '<td class="' + ('bull' if rsi < 30 else 'bear' if rsi > 70 else '') + '">' + str(s.get('rsi14', '--')) + '</td>'
        html += '<td class="' + ('bull' if cci < -100 else 'bear' if cci > 100 else '') + '">' + str(s.get('cci20', '--')) + '</td>'
        html += '<td class="' + ('bull' if macd > 0 else 'bear') + '">' + str(s.get('macd', '--')) + '</td>'
        html += '<td>' + str(s.get('stage', '--')) + '</td><td>' + ', '.join(signals[:3]) + '</td></tr>'
    html += '</tbody></table></div><div class="section"><h2>🎯 分项预测明细</h2><table><thead><tr><th>指标</th><th>预测</th><th>置信度</th><th>理由</th></tr></thead><tbody>'
    for label, key in [("沪深300MA200", "hs300_ma200"), ("北向资金→A股", "north_money"), ("黑色星期四→A股", "black_thursday"), ("VIX→A股", "vix"), ("南向资金→恒生", "south_money"), ("恒生MA200", "hk_ma200")]:
        st = sub.get(key, {})
        html += '<tr><td>' + label + '</td><td class="' + pc(st.get('prediction', '--')) + '">' + str(st.get('prediction', '--')) + '</td><td>' + str(st.get('confidence', '--')) + '</td><td style="font-size:12px;color:#8890a4">' + str(st.get('reasoning', '')) + '</td></tr>'
    html += '</tbody></table></div><div class="footer">哮天每日收盘报告 · 基于马克·米勒维尼趋势策略 · 数据仅供参考，不构成投资建议</div></body></html>'
    return html


HTML_CONTENT = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>哮天每日收盘报告</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0f1117;--card:#171b26;--card2:#1e2330;--card3:#232840;--border:#2a2f3e;--text:#c5c9d8;--text2:#8890a4;--bull:#3ddc84;--bear:#ff6b6b;--mid:#888aa0;--accent:#e07c3e;--blue:#4d9de0;--yellow:#fbbf24}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--bg);color:var(--text);min-height:100vh;font-size:14px}
.container{max-width:1400px;margin:0 auto;padding:10px}
.header{display:flex;align-items:center;justify-content:space-between;padding:14px 20px;background:var(--card);border-bottom:1px solid var(--border);border-radius:10px;margin-bottom:12px}
.header h1{font-size:20px;color:var(--accent);font-weight:700;display:flex;align-items:center;gap:8px}
.header .subtitle{font-size:12px;color:var(--text2);margin-top:2px}
.header-right{display:flex;align-items:center;gap:12px}
.last-update{font-size:12px;color:var(--text2)}
.spinner{display:none;width:18px;height:18px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .6s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.tabs{display:flex;gap:4px;background:var(--card);padding:6px;border-radius:10px;margin-bottom:12px;flex-wrap:wrap}
.tab{padding:8px 20px;border-radius:6px;cursor:pointer;font-size:14px;color:var(--text2);transition:all .2s;border:none;background:transparent;font-weight:500}
.tab:hover{background:var(--card2);color:var(--text)}
.tab.active{background:var(--card3);color:#fff;font-weight:600}
.content{background:var(--card);border-radius:12px;padding:18px;min-height:600px}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;align-items:center}
.toolbar .spacer{flex:1}
select,input{padding:7px 12px;border-radius:6px;border:1px solid var(--border);background:var(--card2);color:var(--text);font-size:13px;outline:none}
select:focus,input:focus{border-color:var(--accent)}
button{padding:7px 16px;border-radius:6px;border:none;cursor:pointer;font-size:13px;font-weight:600;transition:all .15s}
button.primary{background:var(--accent);color:#fff}
button.primary:hover{filter:brightness(1.1)}
button.secondary{background:var(--card2);color:var(--text);border:1px solid var(--border)}
button.secondary:hover{background:var(--card3)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.card{background:var(--card2);border-radius:10px;padding:16px;border:1px solid var(--border);transition:border-color .2s}
.card:hover{border-color:#3a4050}
.card-title{font-size:13px;font-weight:600;color:var(--text);margin-bottom:12px;display:flex;align-items:center;gap:6px;padding-bottom:8px;border-bottom:1px solid var(--border)}
.stat-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.stat{background:var(--card);border-radius:6px;padding:8px 10px}
.stat .label{font-size:11px;color:var(--text2);margin-bottom:3px}
.stat .value{font-size:17px;font-weight:700}
.stat .sub{font-size:11px;color:var(--text2);margin-top:2px}
.stat-row{display:flex;justify-content:space-between;align-items:center;padding:4px 0;font-size:12px}
.stat-row .label{color:var(--text2)}
.stat-row .value{font-weight:600}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}
.badge.bull{background:rgba(61,220,132,.15);color:var(--bull)}
.badge.bear{background:rgba(255,107,107,.15);color:var(--bear)}
.badge.mid{background:rgba(136,138,160,.15);color:var(--mid)}
.badge.blue{background:rgba(77,157,224,.15);color:var(--blue)}
.prediction-big{background:var(--card);border-radius:8px;padding:14px;text-align:center;margin-top:10px}
.prediction-big .pred{font-size:32px;font-weight:800;line-height:1.2}
.prediction-big .conf{font-size:13px;margin-top:6px;color:var(--text2)}
.prediction-big .reason{font-size:11px;color:var(--text2);margin-top:4px}
.section-title{font-size:12px;font-weight:600;color:var(--text2);margin:14px 0 8px;padding-bottom:4px;border-bottom:1px solid var(--border)}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{padding:7px 10px;text-align:left;border-bottom:1px solid var(--border)}
th{color:var(--text2);font-weight:600;font-size:11px;text-transform:uppercase}
tr:hover{background:rgba(255,255,255,.02)}
.signal-list{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
.signal-tag{padding:2px 8px;border-radius:4px;font-size:11px;background:rgba(77,157,224,.12);color:var(--blue)}
.pred-item{background:var(--card);border-radius:6px;padding:10px}
.pred-item .pred-label{font-size:11px;color:var(--text2);margin-bottom:4px}
.pred-item .pred-value{font-size:13px;font-weight:600}
.conf-high{color:var(--bear)}.conf-mid{color:var(--yellow)}.conf-low{color:var(--text2)}
.progress-bar{height:4px;background:var(--border);border-radius:2px;margin-top:6px;overflow:hidden}
.progress-fill{height:100%;border-radius:2px;transition:width .3s}
.progress-fill.bull{background:var(--bull)}.progress-fill.bear{background:var(--bear)}.progress-fill.mid{background:var(--mid)}
.loading{color:var(--text2);font-size:13px;padding:30px;text-align:center}
.error{color:var(--bear);font-size:13px;padding:12px;background:rgba(255,107,107,.08);border-radius:6px;border:1px solid rgba(255,107,107,.2)}
.hidden{display:none!important}
.inline-loading{display:inline-flex;align-items:center;gap:4px;font-size:12px;color:var(--text2)}
.inline-loading .sp2{width:12px;height:12px;border:1.5px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .6s linear infinite}
.realtime-table{width:100%;font-size:12px}
.realtime-table th{color:var(--text2);font-size:10px;text-transform:uppercase;padding:4px 8px}
.realtime-table td{padding:5px 8px}
.realtime-table .price{font-size:15px;font-weight:700}
.realtime-table .chg{font-size:12px;font-weight:600}
.footer{text-align:center;padding:24px;color:var(--text2);font-size:11px}
.toast{position:fixed;bottom:20px;right:20px;background:var(--card3);color:var(--text);padding:10px 16px;border-radius:8px;border:1px solid var(--border);font-size:13px;z-index:1000;opacity:0;transition:opacity .3s;pointer-events:none}
.toast.show{opacity:1}
#spinner{display:none;width:20px;height:20px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .6s linear infinite;margin-left:8px}
::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
@media(max-width:768px){.cards{grid-template-columns:1fr}.toolbar{flex-direction:column;align-items:stretch}select,input{width:100%}button{width:100%}}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div><h1>🐕 哮天每日收盘报告</h1><div class="subtitle">马克·米勒维尼趋势策略 · 选股扫描 + 择时监测 + 每日报告</div></div>
    <div class="header-right"><span id="lastUpdate" class="last-update"></span><div id="spinner"></div></div>
  </div>
  <div class="tabs">
    <button class="tab active" onclick="showTab('scan')">📈 选股扫描</button>
    <button class="tab" onclick="showTab('sepa')">🎯 SEPA选股</button>
    <button class="tab" onclick="showTab('timing')">⏱️ 择时监测</button>
    <button class="tab" onclick="showTab('report')">📊 每日报告</button>
  </div>

  <div id="tab-scan" class="content">
    <div class="toolbar">
      <select id="marketSel" onchange="loadStocks()"><option value="A">全部 A股</option><option value="SZ">深圳主板</option><option value="SS">上海主板</option><option value="HK">港股</option></select>
      <input id="stockSearch" placeholder="搜索股票代码/名称…" oninput="debounceSearch()">
      <select id="signalFilter"><option value="">全部信号</option><option value="RSI_14超卖">RSI超卖</option><option value="MACD_金叉信号线">MACD金叉</option><option value="KDJ_超卖金叉">KDJ超卖金叉</option><option value="CCI_20超卖">CCI超卖</option><option value="布林带_下轨支撑">布林下轨</option><option value="VCP_波动收缩">VCP收缩</option><option value="趋势_第二阶段">第二阶段</option><option value="52周_接近新高">52周新高</option><option value="均线_价格>MA20">价格>MA20</option><option value="量比_明显放大">明显放量</option></select>
      <button class="primary" onclick="runScan()">🔍 执行扫描</button>
      <div class="spacer"></div>
      <button class="secondary" onclick="exportCSV()">📥 导出CSV</button>
    </div>
    <div id="scanStatus" style="font-size:12px;color:var(--text2);margin-bottom:10px"></div>
    <div id="scanResults"></div>
  </div>

  <div id="tab-sepa" class="content hidden">
    <div class="toolbar">
      <input id="sepaStock" placeholder="输入股票代码或名称，如 000001、00700" style="flex:1">
      <button class="primary" onclick="runSEPA()">🔍 SEPA分析</button>
    </div>
    <div id="sepaResults">
      <div style="text-align:center;padding:60px;color:var(--text2)"><div style="font-size:48px;margin-bottom:12px">🎯</div><div>输入股票代码，点击"SEPA分析"开始深度分析</div><div style="font-size:12px;margin-top:8px">SEPA: Stock Evaluation Profile & Analysis</div></div>
    </div>
  </div>

  <div id="tab-timing" class="content hidden">
    <div class="toolbar">
      <button class="primary" onclick="refreshTiming()">🔄 刷新数据</button>
      <span id="timingUpdate" style="font-size:12px;color:var(--text2)"></span>
      <div class="spacer"></div>
      <div class="inline-loading" id="timingLoading" style="display:none"><div class="sp2"></div>加载中…</div>
    </div>
    <div class="cards">
      <div class="card" id="t_realtime"><div class="card-title">📡 实时行情</div><div id="tr_loading" class="loading">加载中…</div><div id="tr_content" style="display:none"></div></div>
      <div class="card" id="t_ma"><div class="card-title">📈 MA200 择时</div><div id="tma_loading" class="loading">加载中…</div><div id="tma_content" style="display:none"></div></div>
      <div class="card" id="t_fund"><div class="card-title">💰 南北向资金</div><div id="tf_loading" class="loading">加载中…</div><div id="tf_content" style="display:none"></div></div>
      <div class="card" id="t_vix"><div class="card-title">😱 VIX 恐慌指数</div><div id="tvix_loading" class="loading">加载中…</div><div id="tvix_content" style="display:none"></div></div>
      <div class="card" id="t_predictions" style="grid-column:span 2"><div class="card-title">🎯 综合预测信号</div><div id="tp_loading" class="loading">加载中…</div><div id="tp_content" style="display:none"></div></div>
      <div class="card" id="t_bt" style="grid-column:span 2"><div class="card-title">🐺 黑色星期四</div><div id="tbt_loading" class="loading">加载中…</div><div id="tbt_content" style="display:none"></div></div>
    </div>
  </div>

  <div id="tab-report" class="content hidden">
    <div class="toolbar">
      <button class="primary" onclick="generateDailyReport()">📊 生成今日报告</button>
      <button class="secondary" onclick="downloadReportImage()">🖼️ 下载图片报告</button>
    </div>
    <div id="reportContent">
      <div style="text-align:center;padding:60px;color:var(--text2)"><div style="font-size:48px;margin-bottom:12px">📊</div><div>点击"生成今日报告"查看详细分析</div></div>
    </div>
  </div>
</div>
<div class="footer">哮天每日收盘报告 · 基于马克·米勒维尼趋势策略 · 数据仅供参考，不构成投资建议</div>
<div class="toast" id="toast"></div>

<script>
var _stocks=[],_timingData=null,_predictionsData=null,_scanResults=[],_searchTimer=null;

function showToast(msg){var t=document.getElementById('toast');t.textContent=msg;t.classList.add('show');setTimeout(function(){t.classList.remove('show');},2500);}
function debounceSearch(){clearTimeout(_searchTimer);_searchTimer=setTimeout(renderStocks,300);}

function showTab(name){
  document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active');});
  document.querySelectorAll('.content').forEach(function(c){c.classList.add('hidden');});
  var tabMap={scan:'tab-scan',sepa:'tab-sepa',timing:'tab-timing',report:'tab-report'};
  var btn=document.querySelector('.tab[onclick="showTab(\\''+name+'\\')"]');
  if(btn)btn.classList.add('active');
  var el=document.getElementById(tabMap[name]);
  if(el)el.classList.remove('hidden');
  if(name==='timing'&&!_timingData)loadAllTiming();
  document.getElementById('lastUpdate').textContent=new Date().toLocaleString('zh-CN');
}

async function loadAllTiming(){
  document.getElementById('timingLoading').style.display='inline-flex';
  try{
    _timingData=await fetchJSON('/api/timing');
    document.getElementById('timingUpdate').textContent='更新: '+(_timingData.timestamp||'');
    loadTimingRealtime();loadTimingMA();loadTimingFund();loadTimingVIX();loadTimingPredictions();loadTimingBT();
  }catch(e){showToast('加载择时数据失败');}
  document.getElementById('timingLoading').style.display='none';
}

function refreshTiming(){
  document.getElementById('spinner').style.display='inline-block';
  document.getElementById('timingLoading').style.display='inline-flex';
  fetch('/api/timing/refresh',{method:'POST'}).then(function(r){return r.json();}).then(function(d){
    _timingData=d.data||d;
    document.getElementById('timingUpdate').textContent='更新: '+(_timingData.timestamp||'');
    loadTimingRealtime();loadTimingMA();loadTimingFund();loadTimingVIX();loadTimingPredictions();loadTimingBT();
    document.getElementById('spinner').style.display='none';
    document.getElementById('timingLoading').style.display='none';
    showToast('数据已刷新');
  }).catch(function(){document.getElementById('spinner').style.display='none';document.getElementById('timingLoading').style.display='none';showToast('刷新失败');});
}

function loadTimingRealtime(){
  var d=_timingData&&_timingData.realtime||{};
  var el=document.getElementById('tr_content'),loading=document.getElementById('tr_loading');
  if(!el)return;
  var items={'sh000300':'沪深300','sh000001':'上证指数','sz399001':'深证成指','hkHSI':'恒生指数','hkHSTECH':'恒生科技'};
  var html='<table class="realtime-table"><thead><tr><th>指数</th><th>价格</th><th>涨跌幅</th></tr></thead><tbody>';
  for(var code in items){
    var info=d[code]||{};
    var pct=info.chg_pct||0;
    var cls=pct>0?'bull':pct<0?'bear':'mid';
    html+='<tr><td style="color:var(--text2)">'+items[code]+'</td><td class="price '+cls+'">'+(info.price||'--')+'</td><td class="chg '+cls+'">'+(pct>=0?'+':'')+pct.toFixed(2)+'%</td></tr>';
  }
  html+='</tbody></table>';
  el.innerHTML=html;el.style.display='block';loading.style.display='none';
}

function loadTimingMA(){
  var hs300=_timingData&&_timingData.hs300_ma200||{},hk=_timingData&&_timingData.hk_hs300||{},hktech=_timingData&&_timingData.hk_hstech||{};
  var el=document.getElementById('tma_content'),loading=document.getElementById('tma_loading');
  if(!el)return;
  function makeBlock(data,label){
    var sig=data.signal||'';var cls=sig.indexOf('看涨')>-1?'bull':sig.indexOf('看跌')>-1?'bear':'mid';
    var pct=data.pct_above;
    return '<div style="margin-bottom:12px"><div class="section-title">'+label+'</div>'+
      '<div class="stat-row"><span class="label">信号</span><span class="value '+cls+'">'+(sig||'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">价格</span><span class="value">'+(data.close||'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">MA200</span><span class="value">'+(data.ma||'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">偏离度</span><span class="value '+(pct>0?'bull':pct<0?'bear':'')+'">'+(pct!=null?pct.toFixed(2)+'%':'--')+'</span></div>'+
      renderFlip(data.recent_signals)+'</div>';
  }
  el.innerHTML=makeBlock(hs300,'沪深300')+makeBlock(hk,'恒生指数')+makeBlock(hktech,'恒生科技');
  el.style.display='block';loading.style.display='none';
}

function renderFlip(signals){
  if(!signals||!signals.length)return'';
  var html='<div style="margin-top:6px;font-size:11px;color:var(--text2)">近期翻转:</div>';
  for(var i=0;i<Math.min(signals.length,3);i++){
    var s=signals[i];
    var cls=s.signal&&s.signal.indexOf('看涨')>-1?'bull':'bear';
    html+='<div style="display:flex;justify-content:space-between;font-size:11px;padding:1px 0"><span>'+s.date+'</span><span class="'+cls+'">'+(s.signal||'')+'</span><span>'+(s.return_pct||'')+'</span></div>';
  }
  return html;
}

function loadTimingFund(){
  var north=_timingData&&_timingData.north_money||{},south=_timingData&&_timingData.south_money||{};
  var el=document.getElementById('tf_content'),loading=document.getElementById('tf_loading');
  if(!el)return;
  var nc=north.cum_net>=0?'bull':'bear',sc=south.cum_net>=0?'bull':'bear';
  function makeBar(val,max){var pct=Math.min(Math.abs(val)/Math.max(Math.abs(max),1)*100,100);return'<div class="progress-bar"><div class="progress-fill '+(val>=0?'bull':'bear')+'" style="width:'+pct+'%"></div></div>';}
  var recentnorth=north.recent_signals||[];
  var sigHtml='';
  for(var i=0;i<Math.min(recentnorth.length,3);i++){
    var s=recentnorth[i];
    sigHtml+='<span class="signal-tag '+(s.signal==='看涨'?'badge bull':'badge bear')+'">'+s.signal+' '+s.net_buy+'亿</span>';
  }
  el.innerHTML='<div style="margin-bottom:14px">'+
    '<div class="section-title">🇨🇳 北向资金（沪股通+深股通）</div>'+
    '<div class="stat-grid">'+
      '<div class="stat"><div class="label">今日净买入</div><div class="value '+nc+'">'+((north.today_net||0).toFixed(2))+'亿</div></div>'+
      '<div class="stat"><div class="label">5日累计</div><div class="value '+nc+'">'+((north.cum_net||0).toFixed(2))+'亿</div></div>'+
      '<div class="stat"><div class="label">5日均线</div><div class="value mid">'+((north.ma_net||0).toFixed(2))+'亿</div></div>'+
      '<div class="stat"><div class="label">信号</div><div class="value '+nc+'">'+(north.signal||'--')+'</div></div>'+
    '</div>'+makeBar(north.cum_net||0,100)+
    '<div class="signal-list" style="margin-top:8px">'+sigHtml+'</div></div>'+
    '<div>'+
    '<div class="section-title">🇭🇰 南向资金（港股通）</div>'+
    '<div class="stat-grid">'+
      '<div class="stat"><div class="label">今日净买入</div><div class="value '+sc+'">'+((south.today_net||0).toFixed(2))+'亿</div></div>'+
      '<div class="stat"><div class="label">5日累计</div><div class="value '+sc+'">'+((south.cum_net||0).toFixed(2))+'亿</div></div>'+
      '<div class="stat"><div class="label">5日均线</div><div class="value mid">'+((south.ma_net||0).toFixed(2))+'亿</div></div>'+
      '<div class="stat"><div class="label">信号</div><div class="value '+sc+'">'+(south.signal||'--')+'</div></div>'+
    '</div>'+makeBar(south.cum_net||0,150)+'</div>';
  el.style.display='block';loading.style.display='none';
}

function loadTimingVIX(){
  var vix=_timingData&&_timingData.vix||{};
  var el=document.getElementById('tvix_content'),loading=document.getElementById('tvix_loading');
  if(!el)return;
  var v=vix.vix;
  var cls='mid',label='正常波动',desc='';
  if(v!==null&&v!==undefined){
    if(v>=30){cls='bear';label='极度恐慌';desc='市场恐慌情绪极高，风险资产承压';}
    else if(v>=25){cls='bear';label='波动较大';desc='市场波动较大，保持谨慎';}
    else if(v>=15){cls='mid';label='正常区间';desc='市场正常波动';}
    else{cls='bull';label='低波动';desc='市场平静，风险偏好较高';}
  }
  var barWidth=v!==null&&v!==undefined?Math.min(v/40*100,100):0;
  var barCls=v>=30?'bear':v>=15?'mid':'bull';
  el.innerHTML='<div class="stat-grid">'+
    '<div class="stat"><div class="label">VIX 当前值</div><div class="value '+cls+'" style="font-size:32px;font-weight:800">'+(v??'--')+'</div><div class="sub">'+(vix.source||'')+'</div></div>'+
    '<div class="stat"><div class="label">市场状态</div><div class="value '+cls+'" style="font-size:18px">'+label+'</div><div class="progress-bar"><div class="progress-fill '+barCls+'" style="width:'+barWidth+'%"></div></div></div>'+
  '</div>'+(v!==null&&v!==undefined?'<div style="margin-top:10px;font-size:12px;color:var(--text2)">解读: '+desc+'</div>':'')+
  '<div style="margin-top:8px;font-size:11px;color:var(--text2)"><div>VIX&gt;30: 极度恐慌 🔴</div><div>VIX 25-30: 波动较大 🟡</div><div>VIX 15-25: 正常 ⚪</div><div>VIX&lt;15: 低波动 🟢</div></div>';
  el.style.display='block';loading.style.display='none';
}

async function loadTimingPredictions(){
  var el=document.getElementById('tp_content'),loading=document.getElementById('tp_loading');
  if(!el)return;
  try{
    var resp=await fetch('/api/timing/predictions');
    var d=await resp.json();_predictionsData=d;renderPredictions(d);
  }catch(e){el.innerHTML='<div class="error">加载预测数据失败: '+e+'</div>';el.style.display='block';loading.style.display='none';}
}

function renderPredictions(d){
  var el=document.getElementById('tp_content'),loading=document.getElementById('tp_loading');
  if(!el)return;
  var sub=d.sub_predictions||{},a_share=d.a_share||{},hk_share=d.hk_share||{};
  function confIcon(c){return c==='高置信'?'🔴':c==='中置信'?'🟡':'⚪';}
  function confCls(c){return c==='高置信'?'conf-high':c==='中置信'?'conf-mid':'conf-low';}
  function predCls(p){return p==='看涨'?'bull':p==='看跌'?'bear':'mid';}
  var html='<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px">';
  for(var i=0;i<2;i++){
    var item=i===0?a_share:hk_share,label=i===0?'🇨🇳 A股综合预测':'🇭🇰 恒生综合预测';
    var pc=predCls(item.prediction);
    html+='<div class="prediction-big" style="background:'+(i===0?'rgba(61,220,132,.05)':'rgba(77,157,224,.05)')+'">'+
      '<div style="font-size:12px;color:var(--text2);margin-bottom:8px">'+label+'</div>'+
      '<div class="pred '+pc+'">'+(item.prediction||'--')+'</div>'+
      '<div class="conf">'+confIcon(item.confidence||'')+' '+(item.confidence||'')+'</div>'+
      '<div style="font-size:11px;color:var(--text2);margin-top:6px;text-align:left">'+(item.reasoning||'')+'</div></div>';
  }
  html+='</div>';
  html+='<div class="section-title">📊 分项预测明细</div>';
  var subRows=[
    {label:'沪深300MA200→A股',key:'hs300_ma200'},{label:'北向资金→A股',key:'north_money'},
    {label:'黑色星期四→A股',key:'black_thursday'},{label:'VIX恐慌指数→A股',key:'vix'},
    {label:'南向资金→恒生',key:'south_money'},{label:'恒生MA200→恒生',key:'hk_ma200'},
  ];
  html+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">';
  for(var j=0;j<subRows.length;j++){
    var row=subRows[j],s=sub[row.key]||{},pc=predCls(s.prediction);
    html+='<div class="pred-item">'+
      '<div class="pred-label">'+(s.reasoning||row.label)+'</div>'+
      '<div style="display:flex;align-items:center;gap:8px">'+
        '<span class="pred-value '+pc+'">'+(s.prediction||'--')+'</span>'+
        '<span class="'+confCls(s.confidence||'')+'" style="font-size:11px">'+confIcon(s.confidence||'')+'</span>'+
      '</div>'+
      (s.detail?'<div style="font-size:10px;color:var(--text2);margin-top:3px">'+s.detail+'</div>':'')+'</div>';
  }
  html+='</div>';
  el.innerHTML=html;el.style.display='block';loading.style.display='none';
}

function loadTimingBT(){
  var bt=_timingData&&_timingData.black_thursday||{};
  var el=document.getElementById('tbt_content'),loading=document.getElementById('tbt_loading');
  if(!el)return;renderBT(bt);el.style.display='block';loading.style.display='none';
}

function renderBT(bt){
  var el=document.getElementById('tbt_content');
  if(!el)return;
  var sig=bt.signal,sigCls=sig===1?'bull':sig===0?'bear':'mid',sigLabel=bt.signal_desc||'--';
  var recent=bt.recent_signals||[];
  var html='<div class="stat-grid">'+
    '<div class="stat"><div class="label">当前信号</div><div class="value '+sigCls+'" style="font-size:22px;font-weight:800">'+sigLabel+'</div><div class="sub">市场: '+(bt.market_type||'--')+'</div></div>'+
    '<div class="stat"><div class="label">年化收益</div><div class="value bull" style="font-size:22px">'+(bt.annual||'--')+'</div><div class="sub">日期: '+(bt.data_date||'--')+'</div></div>'+
  '</div>';
  if(recent.length>0){
    html+='<div class="section-title">📋 近'+Math.min(recent.length,5)+'次信号历史</div>'+
      '<table><thead><tr><th>日期</th><th>信号</th><th>收益率</th><th>市场</th></tr></thead><tbody>';
    for(var i=0;i<Math.min(recent.length,5);i++){
      var r=recent[i];
      var cls=r.signal==='看涨'?'bull':r.signal==='看跌'?'bear':'mid';
      html+='<tr><td style="font-size:11px">'+r.date+'</td><td class="value '+cls+'" style="font-size:12px">'+r.signal+'</td><td style="font-size:12px">'+(r.return_pct||'--')+'</td><td style="font-size:11px;color:var(--text2)">'+(r.market||'')+'</td></tr>';
    }
    html+='</tbody></table>';
  }
  var vix_val=_timingData&&_timingData.vix&&_timingData.vix.vix;
  if(vix_val!==null&&vix_val!==undefined){
    html+='<div style="margin-top:10px;font-size:11px;color:var(--text2)">💡 当前VIX='+vix_val+'，'+(vix_val>=25?'市场波动较大，黑色星期四策略信号需重点关注':vix_val>=15?'市场正常，注意周五收盘前调整仓位':'策略信号参考性较强')+'</div>';
  }
  el.innerHTML=html;
}

async function loadStocks(){
  document.getElementById('spinner').style.display='inline-block';
  try{
    var market=document.getElementById('marketSel').value;
    var r=await fetch('/api/stocks?q='+market);
    _stocks=await r.json();renderStocks();
  }catch(e){console.error(e);}
  document.getElementById('spinner').style.display='none';
}

function renderStocks(){
  var q=(document.getElementById('stockSearch')&&document.getElementById('stockSearch').value||'').toUpperCase();
  var sigFilter=document.getElementById('signalFilter')&&document.getElementById('signalFilter').value||'';
  var rows=_stocks;
  if(q)rows=rows.filter(function(s){return s.code.toUpperCase().indexOf(q)>-1||(s.name||'').toUpperCase().indexOf(q)>-1;});
  var el=document.getElementById('scanResults');
  if(!el)return;
  if(!rows.length){el.innerHTML='<div style="text-align:center;padding:40px;color:var(--text2)">暂无数据，请先点击"执行扫描"</div>';return;}
  var html='<div class="cards">';
  for(var i=0;i<Math.min(rows.length,60);i++){
    var s=rows[i],sigs=s.signals||[],rsi=s.snapshot&&s.snapshot.rsi14;
    var cls=(rsi!==null&&rsi!==undefined)?(rsi<30?'bull':rsi>70?'bear':'mid'):'mid';
    var rsiStr=(rsi!==null&&rsi!==undefined)?'<div style="text-align:right;margin-top:4px"><span class="value '+cls+'" style="font-size:14px">RSI '+rsi+'</span></div>':'';
    var sigTags='';
    for(var j=0;j<Math.min(sigs.length,5);j++)sigTags+='<span class="signal-tag">'+sigs[j]+'</span>';
    html+='<div class="card" style="cursor:pointer" onclick="showStockDetail(\''+s.code+'\')">'+
      '<div style="display:flex;justify-content:space-between;align-items:flex-start">'+
        '<div><div style="font-weight:700;font-size:15px">'+(s.name||s.code)+'</div><div style="font-size:11px;color:var(--text2);margin-top:2px">'+s.code+' · '+(s.sector||'--')+'</div></div>'+
        '<div>'+(sigs.length>0?'<span class="badge '+cls+'">'+sigs.length+'信号</span>':'')+rsiStr+'</div>'+
      '</div>'+(sigTags?'<div class="signal-list">'+sigTags+'</div>':'')+'</div>';
  }
  html+='</div>';
  el.innerHTML=html||'<div style="text-align:center;padding:40px;color:var(--text2)">未找到股票</div>';
}

function showStockDetail(code){document.getElementById('sepaStock').value=code;showTab('sepa');runSEPA();}

async function runScan(){
  document.getElementById('spinner').style.display='inline-block';
  document.getElementById('scanStatus').textContent='正在扫描…';
  try{
    var sigFilter=document.getElementById('signalFilter').value;
    var body=sigFilter?{signals:[sigFilter]}:{};
    var r=await fetch('/api/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    _scanResults=await r.json();
    _stocks=_scanResults.map(function(s){return {code:s.code,name:s.name,sector:s.sector,signals:s.signals,snapshot:s.snapshot};});
    document.getElementById('scanStatus').textContent='扫描完成: '+_scanResults.length+' 只符合条件';
    renderStocks();
  }catch(e){document.getElementById('scanStatus').textContent='扫描失败: '+e;}
  document.getElementById('spinner').style.display='none';
}

function exportCSV(){
  if(!_scanResults.length){showToast('无数据可导出');return;}
  var rows=[['代码','名称','行业','信号','RSI','CCI','MACD','MA20','MA200','趋势阶段','最后日期']];
  for(var i=0;i<_scanResults.length;i++){
    var s=_scanResults[i],snap=s.snapshot||{};
    rows.push([s.code,s.name,s.sector||'',(s.signals||[]).join(';'),snap.rsi14!=null?snap.rsi14:'',snap.cci20!=null?snap.cci20:'',snap.macd!=null?snap.macd:'',snap.ma20!=null?snap.ma20:'',snap.ma200!=null?snap.ma200:'',snap.stage||'',snap.last_date||'']);
  }
  var csv=rows.map(function(r){return r.map(function(v){return'"'+(v===null||v===undefined?'':v)+'"';}).join(',');}).join('\n');
  var blob=new Blob(['\ufeff'+csv],{type:'text/csv;charset=utf-8'});
  var url=URL.createObjectURL(blob);var a=document.createElement('a');a.href=url;a.download='scan_results_'+new Date().toISOString().slice(0,10)+'.csv';a.click();URL.revokeObjectURL(url);showToast('CSV已导出');
}

async function runSEPA(){
  var stock=(document.getElementById('sepaStock')&&document.getElementById('sepaStock').value||'').trim();
  if(!stock){showToast('请输入股票代码');return;}
  var el=document.getElementById('sepaResults');el.innerHTML='<div class="loading">SEPA分析中…</div>';
  try{
    var r=await fetch('/api/sepa?code='+encodeURIComponent(stock));
    if(!r.ok)throw new Error('股票未找到');
    var d=await r.json();renderSEPA(d);
  }catch(e){el.innerHTML='<div class="error">分析失败: '+e+'</div>';}
}

function renderSEPA(d){
  var el=document.getElementById('sepaResults');
  if(!d||!d.snapshot){el.innerHTML='<div class="error">未找到数据</div>';return;}
  var s=d.snapshot,signals=d.signals||[];
  var bullSignals=[],bearSignals=[];
  for(var i=0;i<signals.length;i++){
    var sg=signals[i];
    if(sg.indexOf('超买')===-1&&sg.indexOf('空头')===-1&&sg.indexOf('下降')===-1)bullSignals.push(sg);
    else bearSignals.push(sg);
  }
  var rsi=s.rsi14,rsiCls=(rsi!==null&&rsi!==undefined)?(rsi<30?'bull':rsi>70?'bear':'mid'):'mid';
  var cci=s.cci20,cciCls=(cci!==null&&cci!==undefined)?(cci<-100?'bull':cci>100?'bear':'mid'):'mid';
  var macdCls=(s.macd||0)>0?'bull':'bear';
  var stageCls=(s.stage||'').indexOf('上升')>-1?'bull':(s.stage||'').indexOf('下降')>-1?'bear':'mid';
  var bullTag='',bearTag='';
  for(var i=0;i<bullSignals.length;i++)bullTag+='<span class="signal-tag" style="background:rgba(61,220,132,.12);color:var(--bull)">'+bullSignals[i]+'</span>';
  for(var i=0;i<bearSignals.length;i++)bearTag+='<span class="signal-tag" style="background:rgba(255,107,107,.12);color:var(--bear)">'+bearSignals[i]+'</span>';
  el.innerHTML='<div style="margin-bottom:14px"><div style="font-size:18px;font-weight:700">'+(d.name||d.code)+' <span style="font-size:13px;color:var(--text2)">'+d.code+'</span></div><div style="font-size:12px;color:var(--text2);margin-top:4px">'+(s.stage||'--')+' | RSI='+(rsi??'--')+' | MACD='+(s.macd!=null?s.macd.toFixed(4):'--')+'</div></div>'+
  '<div class="cards">'+
    '<div class="card"><div class="card-title">📊 价格与均线</div>'+
      '<div class="stat-row"><span class="label">当前价格</span><span class="value bull" style="font-size:20px">'+(s.close||'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">MA5</span><span class="value">'+(s.ma5||'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">MA20</span><span class="value">'+(s.ma20||'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">MA50</span><span class="value">'+(s.ma50||'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">MA150</span><span class="value">'+(s.ma150||'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">MA200</span><span class="value">'+(s.ma200||'--')+'</span></div></div>'+
    '<div class="card"><div class="card-title">📈 技术指标</div>'+
      '<div class="stat-row"><span class="label">RSI(14)</span><span class="value '+rsiCls+'">'+(rsi??'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">RSI(28)</span><span class="value '+((s.rsi28||0)<30?'bull':(s.rsi28||0)>70?'bear':'')+'">'+(s.rsi28!=null?s.rsi28:'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">CCI(20)</span><span class="value '+cciCls+'">'+(cci??'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">MACD</span><span class="value '+macdCls+'">'+(s.macd!=null?s.macd.toFixed(4):'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">MACD柱</span><span class="value '+((s.macd_hist||0)>0?'bull':'bear')+'">'+(s.macd_hist!=null?s.macd_hist.toFixed(4):'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">KDJ K</span><span class="value">'+(s.k!=null?s.k:'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">KDJ D</span><span class="value">'+(s.d!=null?s.d:'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">KDJ J</span><span class="value '+((s.j||0)<20?'bull':(s.j||0)>80?'bear':'')+'">'+(s.j!=null?s.j:'--')+'</span></div></div>'+
    '<div class="card"><div class="card-title">🎯 趋势与动量</div>'+
      '<div class="stat-row"><span class="label">趋势阶段</span><span class="value '+stageCls+'">'+(s.stage||'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">MA200趋势</span><span class="value '+((s.ma200_trend||'')==='上升'?'bull':'bear')+'">'+(s.ma200_trend||'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">趋势斜率</span><span class="value '+((s.slope20||0)>0?'bull':'bear')+'">'+(s.slope20!=null?s.slope20.toFixed(4):'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">ROC(12)</span><span class="value '+((s.roc12||0)>0?'bull':'bear')+'">'+(s.roc12!=null?s.roc12.toFixed(2)+'%':'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">ADX</span><span class="value '+((s.adx||0)>25?'bull':'')+'">'+(s.adx!=null?s.adx:'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">Supertrend</span><span class="value '+(s.supertrend_dir==='做多'?'bull':s.supertrend_dir==='做空'?'bear':'')+'">'+(s.supertrend_dir||'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">52周新高</span><span class="value">'+(s.near_52w_high?'✅ 是':'❌ 否')+'</span></div>'+
      '<div class="stat-row"><span class="label">VCP收缩</span><span class="value">'+(s.is_contracting?'✅ 是':'❌ 否')+'</span></div></div>'+
    '<div class="card"><div class="card-title">📉 风险与波动</div>'+
      '<div class="stat-row"><span class="label">ATR(14)</span><span class="value">'+(s.atr14!=null?s.atr14:'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">布林宽度</span><span class="value">'+(s.bb_width!=null?(s.bb_width*100).toFixed(1)+'%':'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">Williams%R</span><span class="value '+((s.williams_r||0)<-80?'bull':(s.williams_r||0)>-20?'bear':'')+'">'+(s.williams_r!=null?s.williams_r:'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">MFI(14)</span><span class="value '+((s.mfi14||0)<20?'bull':(s.mfi14||0)>80?'bear':'')+'">'+(s.mfi14!=null?s.mfi14:'--')+'</span></div>'+
      '<div class="stat-row"><span class="label">量比</span><span class="value '+((s.vol_ratio||0)>2?'bull':'')+'">'+(s.vol_ratio!=null?s.vol_ratio:'--')+'</span></div></div>'+
    '<div class="card"><div class="card-title">✅ 看涨信号 ('+bullSignals.length+')</div>'+(bullSignals.length>0?'<div class="signal-list">'+bullTag+'</div>':'<div style="font-size:12px;color:var(--text2)">暂无明显看涨信号</div>')+'</div>'+
    '<div class="card"><div class="card-title">⚠️ 看跌信号 ('+bearSignals.length+')</div>'+(bearSignals.length>0?'<div class="signal-list">'+bearTag+'</div>':'<div style="font-size:12px;color:var(--text2)">暂无明显看跌信号</div>')+'</div>'+
  '</div>'+
  '<div class="card" style="margin-top:14px"><div class="card-title">📐 枢轴点与支撑阻力</div>'+
    '<div class="stat-grid">'+
      '<div class="stat"><div class="label">Pivot</div><div class="value">'+(s.pivot!=null?s.pivot:'--')+'</div></div>'+
      '<div class="stat"><div class="label">R1</div><div class="value bear">'+(s.pivot_r1!=null?s.pivot_r1:'--')+'</div></div>'+
      '<div class="stat"><div class="label">R2</div><div class="value bear">'+(s.pivot_r2!=null?s.pivot_r2:'--')+'</div></div>'+
      '<div class="stat"><div class="label">S1</div><div class="value bull">'+(s.pivot_s1!=null?s.pivot_s1:'--')+'</div></div>'+
      '<div class="stat"><div class="label">S2</div><div class="value bull">'+(s.pivot_s2!=null?s.pivot_s2:'--')+'</div></div>'+
      '<div class="stat"><div class="label">StochRSI</div><div class="value '+((s.stoch_rsi||0)<20?'bull':(s.stoch_rsi||0)>80?'bear':'')+'">'+(s.stoch_rsi!=null?s.stoch_rsi:'--')+'</div></div>'+
    '</div></div>';
}

async function generateDailyReport(){
  var el=document.getElementById('reportContent');el.innerHTML='<div class="loading">正在生成报告…</div>';
  try{
    var r=await fetch('/api/generate_report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scan_results:_scanResults})});
    var html=await r.text();el.innerHTML=html;showToast('报告已生成');
  }catch(e){el.innerHTML='<div class="error">生成失败: '+e+'</div>';}
}

function downloadReportImage(){showToast('图片报告需要后端生成，请使用HTML版本');}

async function fetchJSON(url){var r=await fetch(url);if(!r.ok)throw new Error('HTTP '+r.status);return r.json();}

document.addEventListener('DOMContentLoaded',function(){loadStocks();document.getElementById('lastUpdate').textContent=new Date().toLocaleString('zh-CN');});
</script>
</body>
</html>'''


def generate_report_image(scan_results, timing_data, output_dir):
    if not PIL_AVAILABLE:
        return None
    try:
        W, H = 1200, 1600
        img = Image.new("RGB", (W, H), "#0f1117")
        draw = ImageDraw.Draw(img)
        font_paths=["/System/Library/Fonts/PingFang.ttc","/System/Library/Fonts/STHeiti Light.ttc","/Library/Fonts/Arial.ttf","/System/Library/Fonts/Helvetica.ttc"]
        font_large=font_med=font_small=None
        for fp in font_paths:
            if os.path.exists(fp):
                try:
                    font_large=ImageFont.truetype(fp,36);font_med=ImageFont.truetype(fp,24);font_small=ImageFont.truetype(fp,18);break
                except:continue
        if font_large is None:
            font_large=font_med=font_small=ImageFont.load_default()
        predictions=build_timing_predictions(timing_data)
        date_str=datetime.now().strftime("%Y-%m-%d");y=20
        draw.text((40,y),"🐕 哮天每日收盘报告  "+date_str,"#e07c3e",font=font_large);y+=60
        a_share=predictions.get("a_share",{});a_pred=a_share.get("prediction","震荡")
        a_color="#3ddc84" if a_pred=="看涨" else "#ff6b6b" if a_pred=="看跌" else "#888aa0"
        draw.text((40,y),"🇨🇳 A股综合: "+str(a_pred)+"  "+str(a_share.get('confidence','')),a_color,font=font_med);y+=45
        draw.text((40,y),"   "+str(a_share.get('reasoning','')),"#c5c9d8",font=font_small);y+=50
        hk_share=predictions.get("hk_share",{});hk_pred=hk_share.get("prediction","震荡")
        hk_color="#3ddc84" if hk_pred=="看涨" else "#ff6b6b" if hk_pred=="看跌" else "#888aa0"
        draw.text((40,y),"🇭🇰 恒生综合: "+str(hk_pred)+"  "+str(hk_share.get('confidence','')),hk_color,font=font_med);y+=45
        draw.text((40,y),"   "+str(hk_share.get('reasoning','')),"#c5c9d8",font=font_small);y+=50
        draw.line([(40,y),(W-40,y)],"#2a2f3e",width=2);y+=20
        draw.text((40,y),"📊 择时摘要","#e07c3e",font=font_med);y+=40
        timing_items=[
            ("沪深300 MA200",timing_data.get("hs300_ma200",{}).get("signal","N/A")),
            ("恒生指数 MA200",timing_data.get("hk_hs300",{}).get("signal","N/A")),
            ("北向资金",str(timing_data.get('north_money',{}).get('signal','N/A'))+" ("+"{:.1f}".format(timing_data.get('north_money',{}).get('cum_net',0))+"亿)"),
            ("南向资金",str(timing_data.get('south_money',{}).get('signal','N/A'))+" ("+"{:.1f}".format(timing_data.get('south_money',{}).get('cum_net',0))+"亿)"),
            ("VIX",str(timing_data.get("vix",{}).get("vix","N/A"))),
            ("黑色星期四",timing_data.get("black_thursday",{}).get("signal_desc","N/A")),
        ]
        for label,value in timing_items:
            draw.text((60,y),"  "+label+": "+str(value),"#c5c9d8",font=font_small);y+=30
            if y>H-200:break
        draw.line([(40,y),(W-40,y)],"#2a2f3e",width=2);y+=20
        draw.text((40,y),"📈 选股扫描 ("+str(len(scan_results))+" 只)","#e07c3e",font=font_med);y+=40
        for r in scan_results[:15]:
            name=r.get("name",r.get("code","?"));signals=(r.get("signals",[])or[])[:3]
            draw.text((60,y),"  "+name+": "+', '.join(signals),"#c5c9d8",font=font_small);y+=28
            if y>H-80:break
        draw.line([(40,H-40),(W-40,H-40)],"#2a2f3e",width=1)
        draw.text((40,H-30),"基于马克·米勒维尼趋势策略 · 数据仅供参考，不构成投资建议","#8890a4",font=font_small)
        os.makedirs(output_dir,exist_ok=True);out_path=os.path.join(output_dir,"daily_report_"+date_str+".png")
        img.save(out_path,"PNG");return out_path
    except Exception as e:
        import sys;sys.stderr.write("generate_report_image error: "+str(e)+"\n");return None


if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser(description="哮天每日收盘报告工具")
    parser.add_argument("--port",type=int,default=7878,help="服务端口")
    parser.add_argument("--host",type=str,default="0.0.0.0",help="监听地址")
    parser.add_argument("--reload",action="store_true",help="开发模式热重载")
    args=parser.parse_args()
    print("🚀 启动哮天每日收盘报告服务...")
    print("   端口: "+str(args.port)+" | 访问: http://localhost:"+str(args.port))
    print("   择时数据缓存: 30分钟 | 按 Ctrl+C 停止")
    uvicorn.run("daily_report_app:app",host=args.host,port=args.port,reload=args.reload)
