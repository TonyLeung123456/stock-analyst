#!/usr/bin/env python3
"""
哮天每日收盘报告工具 (FastAPI Web UI)
=====================================
马克·米勒维尼趋势策略 · 选股系统 + 个股分析 + 择时监测 + 每日报告
所有数据基于本地 K 线 CSV 文件

用法:
    python3 daily_report_app.py
    访问: http://localhost:7878
"""
from __future__ import annotations

import os, json, warnings, time, math, csv, ssl, urllib.request, glob
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any, Tuple
from collections import defaultdict

warnings.filterwarnings("ignore")
# 保留代理设置，以便访问 Yahoo Finance
# for _k in list(os.environ.keys()):
#     if _k.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
#         del os.environ[_k]
# os.environ["NO_PROXY"] = "*"

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
    "hk_kline_dir":  "/Users/tonyleung/Downloads/股票/港股/Kline",
    "north_dir":     "./data/north",
    "south_dir":     "./data/south",
    "report_dir":    "/Users/tonyleung/Downloads/股票/每日报告",
}

for _d in [DEFAULT_CFG["report_dir"]]:
    os.makedirs(_d, exist_ok=True)

# ═══════════════════════════════════════════════════════
# K 线数据加载
# ═══════════════════════════════════════════════════════

def _read_klines(csv_path: str) -> List[Dict]:
    """返回 [{date, open, high, low, close, volume}]，按日期升序"""
    for enc in ("utf-8", "gbk", "gb2312"):
        try:
            with open(csv_path, encoding=enc, errors="ignore") as f:
                reader = csv.DictReader(f)
                rows = []
                for row in reader:
                    try:
                        rows.append({
                            "date":   row.get("date", ""),
                            "open":   float(row.get("open", 0)),
                            "high":   float(row.get("high", 0)),
                            "low":    float(row.get("low", 0)),
                            "close":  float(row.get("close", 0)),
                            "volume": float(row.get("volume", 0)),
                        })
                    except (ValueError, KeyError):
                        # 尝试处理不同的列名顺序
                        try:
                            rows.append({
                                "date":   row.get("date", ""),
                                "open":   float(row.get("open", 0)),
                                "high":   float(row.get("high", 0) or row.get("HIGH", 0)),
                                "low":    float(row.get("low", 0) or row.get("LOW", 0)),
                                "close":  float(row.get("close", 0) or row.get("CLOSE", 0)),
                                "volume": float(row.get("volume", 0) or row.get("VOLUME", 0)),
                            })
                        except (ValueError, KeyError):
                            continue
                rows.sort(key=lambda x: x["date"])
                return rows
        except Exception:
            continue
    return []


def load_klines(path: str) -> List[Dict]:
    return _read_klines(path)


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


def calc_ema(prices: List[float], period: int) -> List[Optional[float]]:
    if len(prices) < period:
        return [None] * len(prices)
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
    ma20  = _sma(closes, 20)   if n >= 20  else None
    ma50  = _sma(closes, 50)   if n >= 50  else None
    ma150 = _sma(closes, 150)  if n >= 150 else None
    ma200 = _sma(closes, 200)  if n >= 200 else None

    rsi14 = calc_rsi(closes, 14) if n >= 15 else None

    macd_l, sig_l, hist = calc_macd(closes)
    macd_v  = macd_l[-1]
    sig_v   = sig_l[-1]
    hist_v  = hist[-1]
    macd_prev = macd_l[-2] if len(macd_l) >= 2 else None
    hist_prev = hist[-2] if len(hist) >= 2 else None

    cci20 = calc_cci(highs, lows, closes, 20) if n >= 20 else None

    upper_bb, mid_bb, lower_bb = calc_bollinger(closes, 20, 2.0)

    k_v, d_v, j_v = calc_kdj(highs, lows, closes, 9, 3, 3)
    k_prev = k_v[-2] if len(k_v) >= 2 else None
    j_prev = j_v[-2] if len(j_v) >= 2 else None
    k_v, d_v, j_v = k_v[-1], d_v[-1], j_v[-1]

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

    # MA 交叉检测
    ma5_series  = calc_ema(closes, 5) if n >= 5 else [None] * n
    ma20_series = calc_ema(closes, 20) if n >= 20 else [None] * n
    ma5_prev  = ma5_series[-2]  if len(ma5_series) >= 2  and ma5_series[-2]  is not None else None

    # MACD 金叉
    macd_cross_up = bool(macd_prev is not None and sig_v is not None and
                         macd_prev < sig_v and macd_v > sig_v)
    # MACD 柱状图由负转正
    hist_turn_pos = bool(hist_prev is not None and hist_prev < 0 and hist_v >= 0)
    # KDJ 金叉
    kdj_cross_up = bool(k_prev is not None and j_prev is not None and
                         j_prev < k_prev and j_v > k_v and j_v < 30)
    # 布林下轨
    near_bb_lower = bool(lower_bb is not None and price <= lower_bb * 1.03)
    price_above_ma20  = bool(ma20  is not None and price > ma20)
    price_above_ma50  = bool(ma50  is not None and price > ma50)
    price_above_ma200 = bool(ma200 is not None and price > ma200)
    ma_bullish = bool(ma20 is not None and ma50 is not None and ma20 > ma50)

    vol10 = _sma(vols, 10) if n >= 10 else None
    vol_ratio = vols[-1] / vol10 if vol10 else None

    return {
        "close": round(price, 2),
        "ma5": round(ma5, 2) if ma5 else None,
        "ma20": round(ma20, 2) if ma20 else None,
        "ma50": round(ma50, 2) if ma50 else None,
        "ma150": round(ma150, 2) if ma150 else None,
        "ma200": round(ma200, 2) if ma200 else None,
        "rsi14": round(rsi14, 1) if rsi14 else None,
        "cci20": round(cci20, 1) if cci20 else None,
        "macd": round(macd_v, 4) if macd_v else None,
        "macd_signal": round(sig_v, 4) if sig_v else None,
        "macd_hist": round(hist_v, 4) if hist_v else None,
        "macd_cross_up": macd_cross_up,
        "hist_turn_pos": hist_turn_pos,
        "k": round(k_v, 1) if k_v else None,
        "d": round(d_v, 1) if d_v else None,
        "j": round(j_v, 1) if j_v else None,
        "kdj_cross_up": kdj_cross_up,
        "bb_upper": round(upper_bb, 2) if upper_bb else None,
        "bb_mid": round(mid_bb, 2) if mid_bb else None,
        "bb_lower": round(lower_bb, 2) if lower_bb else None,
        "near_bb_lower": near_bb_lower,
        "stage": stage,
        "price_above_ma20": price_above_ma20,
        "price_above_ma50": price_above_ma50,
        "price_above_ma200": price_above_ma200,
        "ma_bullish": ma_bullish,
        "vol_ratio": round(vol_ratio, 2) if vol_ratio else None,
        "last_date": str(kl[-1]["date"])[:10] if kl else None,
        "n": n,
    }


def detect_signals(snap: Dict[str, Any]) -> List[str]:
    """返回当前快照触发的信号名称列表"""
    if not snap:
        return []
    triggered = []
    if snap.get("rsi14") is not None and snap["rsi14"] < 30:
        triggered.append("RSI超卖")
    if snap.get("macd_cross_up"):
        triggered.append("MACD金叉")
    if snap.get("cci20") is not None and snap["cci20"] < -100:
        triggered.append("CCI超卖")
    if snap.get("kdj_cross_up"):
        triggered.append("KDJ金叉")
    if snap.get("near_bb_lower"):
        triggered.append("布林下轨")
    if snap.get("stage") and "第二阶段" in str(snap["stage"]):
        triggered.append("趋势第二阶段")
    return triggered


# ═══════════════════════════════════════════════════════
# 选股扫描（从 stock_scanner.py 复用逻辑）
# ═══════════════════════════════════════════════════════

def scan_stock(kl: List[Dict], market: str = "hk") -> Optional[Dict[str, Any]]:
    """对单只股票计算指标快照，返回结果或None"""
    if len(kl) < 20:
        return None
    snap = snapshot_indicators(kl)
    if not snap:
        return None
    signals = detect_signals(snap)
    if not signals:
        return None
    return {
        "market": market,
        "close": snap.get("close"),
        "rsi14": snap.get("rsi14"),
        "signals": signals,
        "snap": snap,
    }


def scan_market(market: str = "hk", filter_signal: str = "全部") -> List[Dict]:
    """扫描市场，返回符合条件的股票列表"""
    results = []
    if market in ("hk", "all"):
        hk_dir = DEFAULT_CFG["hk_kline_dir"]
        csv_files = list_csv(hk_dir)
        for fname in csv_files:
            code = fname.replace(".csv", "")
            kl = _read_klines(os.path.join(hk_dir, fname))
            if not kl:
                continue
            for k in kl:
                k["symbol"] = code
                k["market"] = "hk"
            result = scan_stock(kl, "hk")
            if result:
                result["code"] = code
                # 提取股票名称
                name = code.lstrip("0").zfill(5)
                result["name"] = _hk_name_cache.get(name, name)
                if filter_signal == "全部" or filter_signal in result.get("signals", []):
                    results.append(result)
    if market in ("a", "all"):
        a_dir = DEFAULT_CFG["a_kline_dir"]
        csv_files = list_csv(a_dir)
        for fname in csv_files:
            code = fname.replace(".csv", "")
            kl = _read_klines(os.path.join(a_dir, fname))
            if not kl:
                continue
            for k in kl:
                k["symbol"] = code
                k["market"] = "a"
            result = scan_stock(kl, "a")
            if result:
                result["code"] = code
                result["name"] = _a_name_cache.get(code, code)
                if filter_signal == "全部" or filter_signal in result.get("signals", []):
                    results.append(result)
    return results


# 股票名称缓存
_hk_name_cache: Dict[str, str] = {}
_a_name_cache: Dict[str, str] = {}
_name_map_loaded = False


def _load_name_map():
    global _name_map_loaded, _hk_name_cache, _a_name_cache
    if _name_map_loaded:
        return
    _name_map_loaded = True

    # A股
    a_path = "/Users/tonyleung/Downloads/股票/A股/list.txt"
    if os.path.exists(a_path):
        with open(a_path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                parts = line.strip().split(",")
                if len(parts) < 2:
                    continue
                code = parts[0].strip()
                name = parts[1].strip()
                _a_name_cache[code] = name
                _a_name_cache[code + ".SZ"] = name
                _a_name_cache[code + ".SS"] = name

    # 港股
    hk_path = "/Users/tonyleung/Downloads/股票/港股/list.txt"
    if os.path.exists(hk_path):
        with open(hk_path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                parts = line.strip().split(",")
                if len(parts) < 2:
                    continue
                code = parts[0].strip().lstrip("0").zfill(5)
                name = parts[1].strip()
                _hk_name_cache[code] = name


_load_name_map()


# ═══════════════════════════════════════════════════════
# 腾讯财经 API
# ═══════════════════════════════════════════════════════

# ============================================================================
# VCP评分 + 港股财务数据（从 sepa_vcp_app.py 移植）
# ============================================================================

_HK_FIN_CACHE: Dict[str, Dict] = {}
_VCP_CACHE: Dict[str, Dict] = {}  # code -> vcp result
_NAME_MAP: Optional[Dict] = None
_SECTOR_MAP: Optional[Dict] = None

def _load_name_map() -> Dict:
    global _NAME_MAP
    if _NAME_MAP is None:
        _NAME_MAP = {}
        for path in glob.glob(os.path.join(DEFAULT_CFG["hk_kline_dir"], "*.csv")):
            code = os.path.basename(path).replace(".HK.csv", "").replace(".csv", "")
            for enc in ("utf-8", "gbk", "gb2312"):
                try:
                    with open(path, encoding=enc, errors="ignore") as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            if row.get("symbol"):
                                _NAME_MAP[code] = row.get("symbol", "").replace(".HK", "")
                                break
                    break
                except UnicodeDecodeError:
                    continue
    return _NAME_MAP

def load_hk_fin(code: str, fin_dir: str = "") -> Optional[Dict]:
    """返回港股真实基本面数据 dict 或 None（从财报CSV读取）"""
    if code in _HK_FIN_CACHE:
        return _HK_FIN_CACHE[code]
    if not fin_dir or not os.path.exists(fin_dir):
        return None
    fpath = os.path.join(fin_dir, code + ".csv")
    if not os.path.exists(fpath):
        return None
    try:
        with open(fpath, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        if len(rows) < 2:
            return None
        r0 = rows[0]
        r1 = rows[1]
        r2 = rows[2] if len(rows) > 2 else None

        def get_num(row, key, default=0.0):
            v = row.get(key, '')
            try:
                return float(v) if v not in ('', 'N/A', 'None') else default
            except (ValueError, TypeError):
                return default

        rev0 = get_num(r0, 'IS_营业额')
        rev1 = get_num(r1, 'IS_营业额')
        rev2 = get_num(r2, 'IS_营业额') if r2 else None
        profit0 = get_num(r0, 'IS_股东应占溢利')
        profit1 = get_num(r1, 'IS_股东应占溢利')
        equity0 = get_num(r0, 'BS_总权益')
        equity1 = get_num(r1, 'BS_总权益')

        rev_yoy = ((rev0 - rev1) / abs(rev1) * 100) if rev1 and rev1 != 0 else 0.0
        profit_yoy = ((profit0 - profit1) / abs(profit1) * 100) if profit1 and profit1 != 0 else 0.0
        roe = (profit0 / abs(equity0) * 100) if equity0 and equity0 != 0 else 0.0

        # 3年CAGR（需要3年营收数据）
        cagr_3y = None
        if rev2 and rev2 > 0 and rev0 > 0:
            try:
                cagr_3y = ((rev0 / rev2) ** (1/2) - 1) * 100
            except (ValueError, ZeroDivisionError):
                cagr_3y = None

        fin = {
            "code": code,
            "name": (_NAME_MAP or {}).get(code, code),
            "revenue_yoy": round(rev_yoy, 2) if rev_yoy is not None else 0,
            "net_profit_yoy": round(profit_yoy, 2) if profit_yoy is not None else 0,
            "roe": round(roe, 2) if roe is not None else 0,
            "cagr_3y": round(cagr_3y, 2) if cagr_3y is not None else 0,
        }
        _HK_FIN_CACHE[code] = fin
        return fin
    except Exception:
        return None

def calc_vcp_score(kl: List[Dict]) -> Dict[str, Any]:
    """计算VCP评分（0-100），结果缓存"""
    if not kl or len(kl) < 60:
        return {"vcp_score": 0, "vcp_grade": "N/A", "is_contracting": False, "volume_ratio": 1.0}
    closes = [r["close"] for r in kl]
    volumes = [r["volume"] for r in kl]
    price = closes[-1]
    ma20 = sum(closes[-20:]) / 20
    ma50 = sum(closes[-50:]) / 50
    is_contracting = price > ma20 > ma50
    vol_5 = sum(volumes[-5:]) / 5
    vol_prev = sum(volumes[-20:-5]) / 15 if len(volumes) >= 20 else vol_5
    volume_ratio = vol_5 / vol_prev if vol_prev > 0 else 1.0
    breakouts = sum(1 for i in range(-20, 0)
                    if closes[i] > closes[i-1] and volumes[i] > volumes[i-1])
    score = 0
    if is_contracting:
        score += 30
    score += min(30, volume_ratio * 15)
    score += min(40, breakouts * 4)
    rs = 0
    if len(closes) >= 252:
        annual_return = (closes[-1] / closes[-252] - 1) * 100
        rs = max(0, min(100, annual_return + 50))
        score += rs * 0.1
    grade = "A" if score >= 70 else "B" if score >= 50 else "C" if score >= 30 else "D"
    return {
        "vcp_score": round(score, 1),
        "vcp_grade": grade,
        "is_contracting": is_contracting,
        "volume_ratio": round(volume_ratio, 2),
        "breakouts": breakouts,
        "rs": round(rs, 1),
    }

# ============================================================================
# 名称/板块补全（从 sepa_vcp_app.py 移植）
# ============================================================================

_NAME_CACHE: Dict[str, str] = {}
_SECTOR_CACHE: Dict[str, str] = {}
_NAME_MAP_LOADED = False

def _load_sector_map():
    global _NAME_MAP_LOADED
    if _NAME_MAP_LOADED:
        return
    _NAME_MAP_LOADED = True
    cache_path = "/tmp/stock_sector_map.json"
    if os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                _SECTOR_CACHE.update(json.load(f))
        except:
            pass

def _load_name_map():
    """
    加载股票名称和板块缓存。
    返回 Dict[code, name]，也填充全局 _NAME_CACHE/_SECTOR_CACHE（供 enrich_names_sectors 使用）
    和 _a_name_cache/_hk_name_cache（供 run_screening 原始逻辑使用）。
    """
    global _NAME_MAP_LOADED, _a_name_cache, _hk_name_cache
    if _NAME_MAP_LOADED:
        return _NAME_CACHE
    _NAME_MAP_LOADED = True
    try:
        a_path = '/Users/tonyleung/Downloads/股票/A股/list.txt'
        if os.path.exists(a_path):
            with open(a_path, encoding='utf-8') as f:
                for line in f:
                    parts = line.strip().split(',')
                    if len(parts) >= 2:
                        c = parts[0].strip()
                        name = parts[1].strip()
                        sector = parts[2].strip() if len(parts) >= 3 else '—'
                        _NAME_CACHE[c + '.SZ'] = name
                        _NAME_CACHE[c + '.SS'] = name
                        _a_name_cache[c] = name
                        _a_name_cache[c + '.SZ'] = name
                        _a_name_cache[c + '.SS'] = name
                        _SECTOR_CACHE[c + '.SZ'] = sector
                        _SECTOR_CACHE[c + '.SS'] = sector
        hk_path = '/Users/tonyleung/Downloads/股票/港股/list.txt'
        if os.path.exists(hk_path):
            with open(hk_path, encoding='utf-8') as f:
                for line in f:
                    parts = line.strip().split(',')
                    if len(parts) >= 2:
                        c = parts[0].strip().lstrip('0').zfill(5)
                        name = parts[1].strip()
                        sector = parts[2].strip() if len(parts) >= 3 else '—'
                        _NAME_CACHE['HK' + c] = name
                        _NAME_CACHE[c + '.HK'] = name
                        _hk_name_cache[c] = name
                        _SECTOR_CACHE['HK' + c] = sector
                        _SECTOR_CACHE[c + '.HK'] = sector
    except Exception:
        pass
    return _NAME_CACHE

# 初始化我的 _NAME_CACHE（调用新版本以获取返回值）
_NAME_CACHE = _load_name_map()

def _norm_code(code):
    """Normalize code format for cache lookup.
    A股: 688667:SS → 688667.SS (strip leading zeros not needed)
    港股: 0290.HK → 00029.HK (pad to 5 digits), HK0290 → HK00029
    """
    if ':SS' in code:
        return code.replace(':SS', '.SS')
    if ':SZ' in code:
        return code.replace(':SZ', '.SZ')
    if code.endswith('.HK'):
        num = code.replace('.HK', '').lstrip('0').zfill(5)
        return num + '.HK'
    if code.startswith('HK') and not code.endswith('.HK'):
        num = code[2:].lstrip('0').zfill(5)
        return 'HK' + num
    return code

def enrich_names_sectors(results, cfg=None):
    if not results:
        return results
    _load_name_map()
    for r in results:
        code = r.get('code', '')
        norm = _norm_code(code)
        r['name'] = _NAME_CACHE.get(norm, r.get('name', code))
        r['sector'] = _SECTOR_CACHE.get(norm, '—')
    return results


# ============================================================================
# 选股系统核心（统一多条件筛选）
# ============================================================================
def run_screening(params: Dict) -> Tuple[List[Dict], Dict[str, int]]:
    """
    统一选股引擎，支持技术面+基本面+资金面+估值面+情绪面
    params:
        market: "hk" | "a" | "all"
        # 技术面
        ma50_above: bool
        ma150_above: bool
        ma200_above: bool
        min_vol_ratio: float
        min_vcp_score: float
        rsi_max: float | None
        rsi_min: float | None
        # 基本面（港股）
        min_rev_yoy: float
        min_profit_yoy: float
        min_roe: float
        min_cagr: float
        # 资金面
        north_dir: "all" | "buy" | "sell"
        south_dir: "all" | "buy" | "sell"
        # 估值面
        pe_max: float | None
        pb_max: float | None
        # 情绪面
        vix_max: float | None
        vix_calm: bool
    返回: (结果列表, 漏斗计数dict)
    """
    p = params or {}
    market = p.get("market", "all")
    hk_fin_dir = p.get("hk_fin_dir", "")
    # 加载K线
    hk_stocks = {}
    a_stocks = {}
    if market in ("hk", "all"):
        hk_kline_dir = p.get("hk_kline_dir", DEFAULT_CFG["hk_kline_dir"])
        for path in glob.glob(os.path.join(hk_kline_dir, "*.csv")):
            code = os.path.basename(path).replace(".HK.csv", "").replace(".csv", "")
            kl = _read_klines(path)
            if kl:
                hk_stocks[code] = kl
    if market in ("a", "all"):
        a_kline_dir = p.get("a_kline_dir", DEFAULT_CFG["a_kline_dir"])
        for path in glob.glob(os.path.join(a_kline_dir, "*.csv")):
            # 处理 A 股文件名格式：000001.SZ.csv 或 000001.SS.csv
            code = os.path.basename(path).replace(".csv", "")
            kl = _read_klines(path)
            if kl:
                a_stocks[code] = kl

    # 加载南北资金最新方向
    north_buy = None
    south_buy = None
    try:
        north_df = pd.read_csv(os.path.join(DEFAULT_CFG["north_dir"], "tushare_only.csv"))
        if not north_df.empty:
            north_buy = north_df.iloc[-1]["net_buy"] if "net_buy" in north_df.columns else 0
    except Exception:
        pass
    try:
        south_df = pd.read_csv(os.path.join(DEFAULT_CFG["south_dir"], "south_money_daily.csv"))
        if not south_df.empty:
            south_buy = south_df.iloc[-1]["net_buy"] if "net_buy" in south_df.columns else 0
    except Exception:
        pass

    # VIX
    vix_val = fetch_vix().get("value", None)
    vix_calm = vix_val is not None and vix_val <= 25

    # 名称映射
    name_map = _load_name_map()

    results = []
    funnel = {"total": 0, "ma50": 0, "ma150": 0, "vol_ratio": 0, "vcp": 0, "fundamental": 0, "final": 0}

    # 扫描港股
    for code, kl in hk_stocks.items():
        funnel["total"] += 1
        
        # 次新股筛选：上市不满1年（K线数据少于250个交易日）
        if len(kl) < 250:
            continue
            
        snap = snapshot_indicators(kl)
        vcp = calc_vcp_score(kl)
        fin = load_hk_fin(code, hk_fin_dir)
        
        # 技术面筛选
        ma50_ok = not p.get("ma50_above") or (snap.get("ma50") and snap["close"] > snap["ma50"])
        # 如果ma150不存在，且K线数据不足150天，则跳过ma150筛选
        ma150_ok = not p.get("ma150_above") or (snap.get("ma50") and (snap.get("ma150") is not None and snap["ma50"] > snap["ma150"]) or len(kl) < 150)
        ma200_ok = not p.get("ma200_above") or (snap.get("ma200") is None or snap["close"] > snap["ma200"])
        vol_ok = vcp["volume_ratio"] >= p.get("min_vol_ratio", 1.0)
        vcp_ok = vcp["vcp_score"] >= p.get("min_vcp_score", 0)
        rsi = snap.get("rsi14", 50)
        rsi_max = p.get("rsi_max")
        rsi_min = p.get("rsi_min")
        rsi_ok = (rsi_max is None or (rsi is not None and rsi <= rsi_max)) and (rsi_min is None or (rsi is not None and rsi >= rsi_min))
        
        # 追踪每个过滤器单独通过的数量
        if ma50_ok:   funnel["ma50"] += 1
        if ma150_ok:  funnel["ma150"] += 1
        if vol_ok:    funnel["vol_ratio"] += 1
        if vcp_ok:    funnel["vcp"] += 1
        
        if not (ma50_ok and ma150_ok and vol_ok and vcp_ok and rsi_ok):
            continue

        # 基本面筛选
        fin_ok = True
        if fin:
            # 营业收入同比增长率 > 25%
            if (fin.get("revenue_yoy", 0) or 0) < p.get("min_rev_yoy", 0):
                fin_ok = False
            # 净利润同比增长率 > 30%
            if (fin.get("net_profit_yoy", 0) or 0) < p.get("min_profit_yoy", 0):
                fin_ok = False
            # 净利润环比增长为正（如果有数据）
            if fin.get("net_profit_qoq") is not None and (fin.get("net_profit_qoq", 0) or 0) <= 0:
                fin_ok = False
            # ROE > 15%
            if (fin.get("roe", 0) or 0) < p.get("min_roe", 0):
                fin_ok = False
            # 3年CAGR > 20%
            if (fin.get("cagr_3y", 0) or 0) < p.get("min_cagr", 0):
                fin_ok = False
        # 当没有财务数据时，默认通过基本面筛选
        
        if not fin_ok:
            continue
            
        funnel["fundamental"] += 1

        # 资金面（港股跳过南北资金过滤）
        nd = p.get("north_dir", "all")
        nd_all = nd == "all" or nd is None
        north_ok = nd_all or (north_buy is not None and (nd == "buy") == (north_buy > 0))
        sd = p.get("south_dir", "all")
        sd_all = sd == "all" or sd is None
        south_ok = sd_all or (south_buy is not None and (sd == "buy") == (south_buy > 0))
        if not (north_ok and south_ok):
            continue

        # 情绪面（港股跳过VIX过滤，由全局vix_val/vix_calm控制）

        funnel["final"] += 1
        results.append({
            "code": code + ".HK",
            "name": name_map.get(code, code),
            "market": "港股",
            "vcp_score": vcp["vcp_score"],
            "vcp_grade": vcp["vcp_grade"],
            "volume_ratio": vcp["volume_ratio"],
            "breakouts": vcp.get("breakouts", 0),
            "ma50_ok": ma50_ok,
            "ma150_ok": ma150_ok,
            "ma200_ok": ma200_ok,
            "rsi14": rsi,
            "close": snap.get("close"),
            "ma50": round(snap.get("ma50"), 2) if snap.get("ma50") else None,
            "ma150": round(snap.get("ma150"), 2) if snap.get("ma150") else None,
            "ma200": round(snap.get("ma200"), 2) if snap.get("ma200") else None,
            "rev_yoy": fin.get("revenue_yoy") if fin else None,
            "profit_yoy": fin.get("net_profit_yoy") if fin else None,
            "profit_qoq": fin.get("net_profit_qoq") if fin else None,
            "roe": fin.get("roe") if fin else None,
            "cagr_3y": fin.get("cagr_3y") if fin else None,
            "north_dir": "净买入" if north_buy and north_buy > 0 else "净卖出" if north_buy and north_buy < 0 else "—",
            "south_dir": "净买入" if south_buy and south_buy > 0 else "净卖出" if south_buy and south_buy < 0 else "—",
            "vix_level": "平静" if vix_calm else "紧张" if vix_val and vix_val > 25 else "—",
            "vix": vix_val,
        })


    # 扫描A股
    for code, kl in a_stocks.items():
        funnel["total"] += 1
        
        # 次新股筛选：上市不满1年（K线数据少于250个交易日）
        if len(kl) < 250:
            continue
            
        # ST股票筛选：代码中包含ST
        if "ST" in code or "st" in code:
            continue
            
        snap = snapshot_indicators(kl)
        vcp = calc_vcp_score(kl)
        
        # 技术面筛选
        ma50_ok = not p.get("ma50_above") or (snap.get("ma50") and snap["close"] > snap["ma50"])
        # 如果ma150不存在，且K线数据不足150天，则跳过ma150筛选
        ma150_ok = not p.get("ma150_above") or (snap.get("ma50") and (snap.get("ma150") is not None and snap["ma50"] > snap["ma150"]) or len(kl) < 150)
        ma200_ok = not p.get("ma200_above") or (snap.get("ma200") is None or snap["close"] > snap["ma200"])
        vol_ok = vcp["volume_ratio"] >= p.get("min_vol_ratio", 1.0)
        vcp_ok = vcp["vcp_score"] >= p.get("min_vcp_score", 0)
        rsi = snap.get("rsi14", 50)
        rsi_max = p.get("rsi_max")
        rsi_min = p.get("rsi_min")
        rsi_ok = (rsi_max is None or (rsi is not None and rsi <= rsi_max)) and (rsi_min is None or (rsi is not None and rsi >= rsi_min))
        
        # 追踪每个过滤器单独通过的数量
        if ma50_ok:   funnel["ma50"] += 1
        if ma150_ok:  funnel["ma150"] += 1
        if vol_ok:    funnel["vol_ratio"] += 1
        if vcp_ok:    funnel["vcp"] += 1
        if rsi_ok:    funnel["rsi"] = funnel.get("rsi", 0) + 1
        
        if not (ma50_ok and ma150_ok and vol_ok and vcp_ok and rsi_ok):
            continue

        # A股无南北资金/基本面数据，默认通过
        north_ok = p.get("north_dir") == "all"
        south_ok = p.get("south_dir") == "all"
        if not (north_ok and south_ok):
            continue

        vix_ok = p.get("vix_max") is None or (vix_val is not None and p.get("vix_max") is not None and vix_val <= p.get("vix_max")) or vix_val is None
        vix_calm_ok = not p.get("vix_calm", False) or vix_calm or vix_val is None
        if not (vix_ok and vix_calm_ok):
            continue

        # A股默认通过基本面筛选
        funnel["fundamental"] += 1
        funnel["final"] += 1
        results.append({
            "code": code,
            "name": code,
            "market": "A股",
            "vcp_score": vcp["vcp_score"],
            "vcp_grade": vcp["vcp_grade"],
            "volume_ratio": vcp["volume_ratio"],
            "breakouts": vcp.get("breakouts", 0),
            "ma50_ok": ma50_ok,
            "ma150_ok": ma150_ok,
            "ma200_ok": ma200_ok,
            "rsi14": rsi,
            "close": snap.get("close"),
            "ma50": round(snap.get("ma50"), 2) if snap.get("ma50") else None,
            "ma150": round(snap.get("ma150"), 2) if snap.get("ma150") else None,
            "ma200": round(snap.get("ma200"), 2) if snap.get("ma200") else None,
            "rev_yoy": None, "profit_yoy": None, "profit_qoq": None, "roe": None, "cagr_3y": None,
            "north_dir": "—", "south_dir": "—",
            "vix_level": "平静" if vix_calm else "紧张",
            "vix": vix_val,
        })

    # 排序：VCP评分降序
    results.sort(key=lambda x: x["vcp_score"], reverse=True)
    # 不要覆盖vcp计数
    # funnel["vcp"] = funnel["final"]
    results = enrich_names_sectors(results)
    return results, funnel
def _qq_fetch(url: str, timeout: int = 8) -> Optional[str]:
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
    """腾讯财经批量实时行情"""
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


def fetch_eastmoney_index_kline(code: str, days: int = 300) -> Optional[pd.DataFrame]:
    """东方财富A股指数K线"""
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
    except Exception as e:
        import traceback; traceback.print_exc(); return None


def fetch_hk_index_kline(code: str, days: int = 500) -> Optional[pd.DataFrame]:
    """QQ Finance 港股指数日K线"""
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
        df["date"] = pd.to_datetime(df["date"])
        df = df[df["date"] <= pd.Timestamp.today()]
        return df
    except Exception as e:
        import traceback; traceback.print_exc(); return None


# ═══════════════════════════════════════════════════════
# 黑色星期四策略（从 黑色星期四择时策略.py 复制）
# ═══════════════════════════════════════════════════════

MA20_WINDOW = 20


def generate_weekday_signal(df: pd.DataFrame):
    """根据牛熊+星期几生成信号"""
    df = df.copy()
    df["is_bull"] = np.where(df["close"] > df["ma20"], 1, 0)

    def get_signal(row):
        weekday = row["weekday"]
        is_bull = row["is_bull"]
        if is_bull == 1:
            return 1 if weekday in [1, 3, 5] else 0
        else:
            return 1 if weekday in [2, 3] else 0

    df["signal"] = df.apply(get_signal, axis=1)
    df["signal"] = df["signal"].fillna(0)
    df["signal_desc"] = np.where(df["signal"] == 1, "看涨", "看跌")
    df["market_type"] = np.where(df["is_bull"] == 1, "牛市", "熊市")
    return df


def black_thursday_signal() -> Dict[str, Any]:
    """获取黑色星期四信号"""
    try:
        df = fetch_eastmoney_index_kline("sh000300", days=500)
        if df is None or df.empty:
            return {"signal": -1, "desc": "数据获取失败"}
        closes = df["close"].tolist()
        dates = df["date"].tolist()
        if len(closes) < MA20_WINDOW:
            return {"signal": -1, "desc": "数据不足"}
        ma20_vals = pd.Series(closes).rolling(MA20_WINDOW).mean().tolist()
        today = pd.Timestamp.today()
        weekday = today.dayofweek + 1  # 1=周一...7=周日
        close = closes[-1]
        ma20 = ma20_vals[-1]
        is_bull = 1 if close > ma20 else 0
        if is_bull == 1:
            signal = 1 if weekday in [1, 3, 5] else 0
        else:
            signal = 1 if weekday in [2, 3] else 0
        signal_desc = "看涨(持有)" if signal == 1 else "看跌(空仓)"
        market_type = "牛市" if is_bull == 1 else "熊市"
        return {
            "signal": signal,
            "desc": signal_desc,
            "market_type": market_type,
            "weekday": weekday,
            "close": round(close, 2),
            "ma20": round(ma20, 2),
            "pct_above": round((close / ma20 - 1) * 100, 2),
        }
    except Exception as e:
        return {"signal": -1, "desc": f"错误: {e}"}


# ═══════════════════════════════════════════════════════
# 南北资金策略
# ═══════════════════════════════════════════════════════

def north_money_signal() -> Dict[str, Any]:
    """北向资金择时：5日累计 vs 20日均线"""
    try:
        north_csv = os.path.join(DEFAULT_CFG["north_dir"], "tushare_only.csv")
        if not os.path.exists(north_csv):
            return {"signal": "震荡", "desc": "数据文件不存在"}
        df = pd.read_csv(north_csv)
        df.columns = [c.strip() for c in df.columns]
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["net_yi"] = pd.to_numeric(df.get("net_buy", df.get("net_buy", 0)), errors="coerce") / 10000.0
        df = df.dropna(subset=["date", "net_yi"]).sort_values("date").tail(60).reset_index(drop=True)
        if len(df) < 25:
            return {"signal": "震荡", "desc": "数据不足"}
        df["cum5"] = df["net_yi"].rolling(5, min_periods=5).sum()
        df["ma20"] = df["net_yi"].rolling(20, min_periods=20).mean() * 5
        last = df.iloc[-1]
        cum5 = float(last["cum5"]) if pd.notna(last["cum5"]) else 0.0
        ma20 = float(last["ma20"]) if pd.notna(last["ma20"]) else 0.0
        if cum5 > ma20:
            sig = "看涨"
        elif cum5 < ma20:
            sig = "看跌"
        else:
            sig = "震荡"
        return {
            "signal": sig,
            "desc": f"5日累计{cum5:.1f}亿 vs 20日均线{ma20:.1f}亿",
            "cum5": round(cum5, 2),
            "ma20": round(ma20, 2),
            "today_net": round(float(last["net_yi"]), 2),
            "last_date": str(last["date"].date()),
        }
    except Exception as e:
        return {"signal": "震荡", "desc": f"错误: {e}"}


def south_money_signal() -> Dict[str, Any]:
    """南向资金择时：5日累计 vs 20日均线"""
    try:
        south_csv = os.path.join(DEFAULT_CFG["south_dir"], "south_money_daily.csv")
        if not os.path.exists(south_csv):
            return {"signal": "震荡", "desc": "数据文件不存在"}
        df = pd.read_csv(south_csv)
        df.columns = [c.strip() for c in df.columns]
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["net_yi"] = pd.to_numeric(df.get("net_buy", 0), errors="coerce")
        df = df.dropna(subset=["date", "net_yi"]).sort_values("date").tail(60).reset_index(drop=True)
        if len(df) < 25:
            return {"signal": "震荡", "desc": "数据不足"}
        df["cum5"] = df["net_yi"].rolling(5, min_periods=5).sum()
        df["ma20"] = df["net_yi"].rolling(20, min_periods=20).mean() * 5
        last = df.iloc[-1]
        cum5 = float(last["cum5"]) if pd.notna(last["cum5"]) else 0.0
        ma20 = float(last["ma20"]) if pd.notna(last["ma20"]) else 0.0
        if cum5 > ma20:
            sig = "看涨"
        elif cum5 < ma20:
            sig = "看跌"
        else:
            sig = "震荡"
        return {
            "signal": sig,
            "desc": f"5日累计{cum5:.1f}亿 vs 20日均线{ma20:.1f}亿",
            "cum5": round(cum5, 2),
            "ma20": round(ma20, 2),
            "today_net": round(float(last["net_yi"]), 2),
            "last_date": str(last["date"].date()),
        }
    except Exception as e:
        return {"signal": "震荡", "desc": f"错误: {e}"}


def read_north_money_csv() -> List[Dict]:
    """读取北向资金CSV（用于Dashboard显示）"""
    try:
        north_csv = os.path.join(DEFAULT_CFG["north_dir"], "tushare_only.csv")
        if not os.path.exists(north_csv):
            return []
        df = pd.read_csv(north_csv)
        df.columns = [c.strip() for c in df.columns]
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"]).sort_values("date").tail(30)
        return [
            {"date": str(row["date"].date()), "net_buy": round(float(row.get("net_buy", 0)) / 10000.0, 4)}
            for _, row in df.iterrows()
        ]
    except Exception:
        return []


def read_south_money_csv() -> List[Dict]:
    """读取南向资金CSV（用于Dashboard显示）"""
    try:
        south_csv = os.path.join(DEFAULT_CFG["south_dir"], "south_money_daily.csv")
        if not os.path.exists(south_csv):
            return []
        df = pd.read_csv(south_csv)
        df.columns = [c.strip() for c in df.columns]
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"]).sort_values("date").tail(30)
        return [
            {"date": str(row["date"].date()), "net_buy": round(float(row.get("net_buy", 0)), 4)}
            for _, row in df.iterrows()
        ]
    except Exception:
        return []


# ═══════════════════════════════════════════════════════
# 指数 MA200 择时
# ═══════════════════════════════════════════════════════

def calc_ma_timing(closes: List[float], period: int = 200) -> Dict[str, Any]:
    n = len(closes)
    if n < 5:
        return {"signal": "数据不足", "ma": None, "close": closes[-1] if closes else None,
                "pct_above": None, "above": None}
    p = min(period, n)
    ma_val = sum(closes[-p:]) / p
    price = closes[-1]
    above = price > ma_val
    return {
        "signal": "看涨(持有)" if above else "看跌(空仓)",
        "ma": round(ma_val, 2),
        "close": round(price, 2),
        "pct_above": round((price / ma_val - 1) * 100, 2),
        "above": above,
        "period": p,
    }


# ═══════════════════════════════════════════════════════
# VIX 获取
# ═══════════════════════════════════════════════════════

def fetch_vix() -> Dict[str, Any]:
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX"
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        r = requests.get(url, timeout=8, headers=headers)
        if r.status_code == 200:
            d = r.json()
            result = d.get("chart", {}).get("result", [])
            if result:
                meta = result[0].get("meta", {})
                current_price = meta.get("regularMarketPrice")
                return {"vix": round(float(current_price), 2) if current_price else None,
                        "source": "Yahoo Finance"}
    except Exception:
        pass
    # 备用：用ATR估算
    try:
        df = fetch_eastmoney_index_kline("sh000300", days=20)
        if df is not None and len(df) >= 15:
            closes = df["close"].tolist()
            highs = df["high"].tolist()
            lows = df["low"].tolist()
            atr = calc_atr(highs, lows, closes, 14)
            if atr and closes[-1]:
                vix_est = round((atr / closes[-1]) * 100 * 4, 1)
                return {"vix": vix_est, "source": "ATR估算"}
    except Exception:
        pass
    return {"vix": None, "source": "获取失败"}


# ═══════════════════════════════════════════════════════
# 综合行情（Dashboard用）
# ═══════════════════════════════════════════════════════

def get_dashboard_data() -> Dict[str, Any]:
    """获取Dashboard所需的全部数据"""
    cache_key = "/tmp/dashboard_cache_v2.json"
    cache_ttl = 30 * 60
    try:
        if os.path.exists(cache_key):
            age = time.time() - os.path.getmtime(cache_key)
            if age < cache_ttl:
                with open(cache_key) as f:
                    return json.load(f)
    except Exception:
        pass

    result: Dict[str, Any] = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    # 实时行情
    try:
        rt = fetch_tencent_realtime(["sh000300", "hkHSI", "usNDX", "sh000001", "SHOO"])
        # 统一代码格式
        normalized_rt = {}
        for key, value in rt.items():
            normalized_key = key
            if key == "USNDX":
                normalized_key = "NDX"
            normalized_rt[normalized_key] = value
        result["realtime"] = normalized_rt
    except Exception:
        result["realtime"] = {}

    # 沪深300 MA200
    try:
        df = fetch_eastmoney_index_kline("sh000300", days=300)
        if df is not None and not df.empty:
            closes = df["close"].tolist()
            result["hs300_ma200"] = calc_ma_timing(closes, 200)
        else:
            result["hs300_ma200"] = {"signal": "数据获取失败"}
    except Exception:
        result["hs300_ma200"] = {"signal": "错误"}

    # 恒生指数 MA200
    try:
        df_hk = fetch_hk_index_kline("hkHSI", days=500)
        if df_hk is not None and not df_hk.empty:
            result["hk_ma200"] = calc_ma_timing(df_hk["close"].tolist(), 200)
        else:
            result["hk_ma200"] = {"signal": "数据获取失败"}
    except Exception:
        result["hk_ma200"] = {"signal": "错误"}

    # 黑色星期四
    result["black_thursday"] = black_thursday_signal()

    # 北向资金
    result["north_money"] = north_money_signal()

    # 南向资金
    result["south_money"] = south_money_signal()

    # VIX
    result["vix"] = fetch_vix()

    # 南北资金历史
    result["north_history"] = read_north_money_csv()
    result["south_history"] = read_south_money_csv()

    try:
        with open(cache_key, "w") as f:
            json.dump(result, f, ensure_ascii=False, default=str)
    except Exception:
        pass

    return result


# ═══════════════════════════════════════════════════════
# SEPA分析
# ═══════════════════════════════════════════════════════

def sepa_analysis(code: str) -> Dict[str, Any]:
    """SEPA个股分析"""
    _load_name_map()
    code = code.strip().upper()

    # 判断市场
    is_hk = False
    is_a = False
    if code.endswith(".HK") or code.upper().startswith("HK"):
        is_hk = True
    elif code.endswith(".SS") or code.endswith(".SZ") or code.isdigit():
        is_a = True

    kl = []
    if is_hk:
        fname = code.replace(".HK", "").lstrip("0").zfill(5) + ".csv"
        kl = _read_klines(os.path.join(DEFAULT_CFG["hk_kline_dir"], fname))
        raw_name = code.replace(".HK", "").lstrip("0").zfill(5)
        name = _hk_name_cache.get(raw_name, code)
    elif is_a:
        # 尝试不同后缀
        for suffix in ["", ".SZ", ".SS"]:
            c = code.replace(".SZ", "").replace(".SS", "")
            fname = c + suffix + ".csv"
            kl = _read_klines(os.path.join(DEFAULT_CFG["a_kline_dir"], fname))
            if kl:
                name = _a_name_cache.get(c, code)
                break
        if not kl:
            # 尝试直接用code作为文件名
            kl = _read_klines(os.path.join(DEFAULT_CFG["a_kline_dir"], code + ".csv"))
            name = _a_name_cache.get(code, code)
    else:
        # 尝试港股
        fname = code.lstrip("0").zfill(5) + ".csv"
        kl = _read_klines(os.path.join(DEFAULT_CFG["hk_kline_dir"], fname))
        if kl:
            is_hk = True
            raw_name = code.lstrip("0").zfill(5)
            name = _hk_name_cache.get(raw_name, code)
        else:
            # 尝试A股
            kl = _read_klines(os.path.join(DEFAULT_CFG["a_kline_dir"], code + ".csv"))
            if kl:
                is_a = True
                name = _a_name_cache.get(code, code)

    if not kl:
        return {"error": f"找不到股票 {code} 的K线数据"}

    snap = snapshot_indicators(kl)
    if not snap:
        return {"error": "K线数据不足，无法计算指标"}

    signals = detect_signals(snap)
    return {
        "code": code,
        "name": name,
        "market": "港股" if is_hk else "A股",
        "indicators": snap,
        "signals": signals,
        "last_date": snap.get("last_date"),
    }


# ═══════════════════════════════════════════════════════
# 择时监测
# ═══════════════════════════════════════════════════════

def get_timing_monitor() -> Dict[str, Any]:
    """6个子卡片的择时监测数据"""
    data = get_dashboard_data()
    result = {}

    # 沪深300 MA200
    hs = data.get("hs300_ma200", {})
    sig = hs.get("signal", "震荡")
    pct = hs.get("pct_above")
    if pct is not None and abs(pct) > 5:
        conf = "高置信"
        reason = f"价格偏离MA200 {pct:+.2f}%，趋势明确"
    else:
        conf = "中置信"
        reason = f"MA200择时信号：{sig}"
    result["hs300_ma200"] = {
        "direction": "看涨" if "看涨" in sig else "看跌" if "看跌" in sig else "震荡",
        "confidence": conf,
        "reason": reason,
        "detail": f"MA200={hs.get('ma')} | 价格={hs.get('close')} | 偏离={pct:+.2f}%" if pct is not None else sig,
    }

    # 北向资金
    nm = data.get("north_money", {})
    nm_sig = nm.get("signal", "震荡")
    nm_cum = nm.get("cum5", 0)
    if nm_cum > 20:
        nm_dir, nm_conf, nm_reason = "看涨", "中置信", f"5日累计净买入 {nm_cum:.1f}亿元"
    elif nm_cum < -20:
        nm_dir, nm_conf, nm_reason = "看跌", "中置信", f"5日累计净卖出 {abs(nm_cum):.1f}亿元"
    else:
        nm_dir, nm_conf, nm_reason = "震荡", "低置信", f"5日累计 {nm_cum:.1f}亿元，方向不明"
    result["north_money"] = {
        "direction": nm_dir,
        "confidence": nm_conf,
        "reason": nm_reason,
        "detail": nm.get("desc", ""),
    }

    # 南向资金
    sm = data.get("south_money", {})
    sm_sig = sm.get("signal", "震荡")
    sm_cum = sm.get("cum5", 0)
    if sm_cum > 20:
        sm_dir, sm_conf, sm_reason = "看涨", "中置信", f"5日累计净买入 {sm_cum:.1f}亿港元"
    elif sm_cum < -20:
        sm_dir, sm_conf, sm_reason = "看跌", "中置信", f"5日累计净卖出 {abs(sm_cum):.1f}亿港元"
    else:
        sm_dir, sm_conf, sm_reason = "震荡", "低置信", f"5日累计 {sm_cum:.1f}亿港元，方向不明"
    result["south_money"] = {
        "direction": sm_dir,
        "confidence": sm_conf,
        "reason": sm_reason,
        "detail": sm.get("desc", ""),
    }

    # VIX
    vix_data = data.get("vix", {})
    vix_val = vix_data.get("vix")
    if vix_val is not None:
        if vix_val >= 30:
            vix_dir, vix_conf, vix_reason = "看跌", "高置信", f"VIX={vix_val:.1f}，市场恐慌情绪极高"
        elif vix_val >= 20:
            vix_dir, vix_conf, vix_reason = "震荡", "中置信", f"VIX={vix_val:.1f}，市场正常波动"
        else:
            vix_dir, vix_conf, vix_reason = "看涨", "中置信", f"VIX={vix_val:.1f}，市场平静"
    else:
        vix_dir, vix_conf, vix_reason = "震荡", "低置信", "VIX数据获取失败"
    result["vix"] = {
        "direction": vix_dir,
        "confidence": vix_conf,
        "reason": vix_reason,
        "detail": f"VIX={vix_val}" if vix_val is not None else "无数据",
    }

    # 黑色星期四
    bt = data.get("black_thursday", {})
    bt_sig = bt.get("signal", -1)
    if bt_sig == 1:
        bt_dir, bt_conf, bt_reason = "看涨", "中置信", f"黑色星期四策略：{bt.get('market_type','牛市')}信号"
    elif bt_sig == 0:
        bt_dir, bt_conf, bt_reason = "看跌", "中置信", f"黑色星期四策略：{bt.get('market_type','熊市')}信号"
    else:
        bt_dir, bt_conf, bt_reason = "震荡", "低置信", bt.get("desc", "信号获取失败")
    result["black_thursday"] = {
        "direction": bt_dir,
        "confidence": bt_conf,
        "reason": bt_reason,
        "detail": f"{bt.get('market_type','')} | {bt.get('desc','')}",
    }

    # 综合信号
    dirs = [
        result["hs300_ma200"]["direction"],
        result["north_money"]["direction"],
        result["vix"]["direction"],
        result["black_thursday"]["direction"],
    ]
    counts = {"看涨": dirs.count("看涨"), "看跌": dirs.count("看跌"), "震荡": dirs.count("震荡")}
    final_dir = max(counts, key=counts.get)
    final_conf = "高置信" if counts["看涨"] + counts["看跌"] >= 3 else "中置信"
    result["composite"] = {
        "direction": final_dir,
        "confidence": final_conf,
        "reason": f"综合4项指标：看涨{counts['看涨']}项 看跌{counts['看跌']}项 震荡{counts['震荡']}项",
        "detail": f"{counts}",
    }

    return result


# ═══════════════════════════════════════════════════════
# 每日报告图片生成
# ═══════════════════════════════════════════════════════

def generate_report_image() -> Optional[str]:
    """生成每日报告图片，返回图片路径"""
    if not PIL_AVAILABLE:
        return None

    try:
        data = get_dashboard_data()
        timing = get_timing_monitor()

        W, H = 1200, 800
        img = Image.new("RGB", (W, H), "#0a0e17")
        draw = ImageDraw.Draw(img)

        # 字体（尝试系统字体）
        try:
            font_large = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 32)
            font_medium = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 22)
            font_small = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 16)
        except Exception:
            font_large = ImageFont.load_default()
            font_medium = ImageFont.load_default()
            font_small = ImageFont.load_default()

        # 标题栏
        draw.rectangle([(0, 0), (W, 60)], fill="#131a2a")
        title = f"哮天每日报告 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        draw.text((30, 15), title, fill="#00d4aa", font=font_large)

        # 大盘卡片
        rt = data.get("realtime", {})
        cards = [
            ("沪深300", rt.get("SH000300", {})),
            ("恒生指数", rt.get("HKHSI", rt.get("HKHSI", {}))),
            ("纳斯达克", {}),
            ("A50期货", {}),
        ]

        card_y = 80
        for i, (name, info) in enumerate(cards):
            x = 20 + i * 295
            draw.rectangle([(x, card_y), (x + 280, card_y + 90)], fill="#131a2a")
            draw.text((x + 10, card_y + 8), name, fill="#ffffff", font=font_medium)
            price = info.get("price")
            chg = info.get("chg_pct", 0)
            if price:
                draw.text((x + 10, card_y + 40), f"{price:.2f}", fill="#00d4aa", font=font_medium)
                color = "#00d4aa" if chg >= 0 else "#ff4444"
                draw.text((x + 10, card_y + 65), f"{chg:+.2f}%", fill=color, font=font_small)
            else:
                draw.text((x + 10, card_y + 40), "—", fill="#888888", font=font_medium)

        # 择时信号区
        sig_y = 190
        draw.rectangle([(0, sig_y - 10), (W, sig_y + 160)], fill="#0d1220")
        draw.text((20, sig_y), "择时信号", fill="#00d4aa", font=font_medium)

        timing_items = [
            ("沪深300MA200", timing.get("hs300_ma200", {})),
            ("北向资金", timing.get("north_money", {})),
            ("南向资金", timing.get("south_money", {})),
            ("VIX恐慌指数", timing.get("vix", {})),
            ("黑色星期四", timing.get("black_thursday", {})),
            ("综合信号", timing.get("composite", {})),
        ]

        for i, (label, info) in enumerate(timing_items):
            x = 20 + (i % 3) * 390
            y = sig_y + 35 + (i // 3) * 60
            dir_txt = info.get("direction", "—")
            conf = info.get("confidence", "")
            color = "#00d4aa" if dir_txt == "看涨" else "#ff4444" if dir_txt == "看跌" else "#ffaa00"
            draw.text((x, y), f"{label}：", fill="#aaaaaa", font=font_small)
            draw.text((x + 90, y), dir_txt, fill=color, font=font_small)
            draw.text((x + 170, y), f"({conf})", fill="#888888", font=font_small)

        # 北向/南向资金
        fund_y = 360
        draw.text((20, fund_y), "北向资金（近30日）", fill="#00d4aa", font=font_medium)
        north_hist = data.get("north_history", [])
        if north_hist:
            max_val = max(abs(x["net_buy"]) for x in north_hist) or 1
            bar_w = (W // 2 - 40) // len(north_hist)
            for i, row in enumerate(north_hist[-30:]):
                x = 20 + i * bar_w
                val = row["net_buy"]
                bar_h = int(abs(val) / max_val * 80)
                y_top = fund_y + 30 + 80 - bar_h if val >= 0 else fund_y + 30 + 80
                color = "#00d4aa" if val >= 0 else "#ff4444"
                draw.rectangle([(x, y_top), (x + bar_w - 2, y_top + bar_h)], fill=color)

        draw.text((W // 2 + 10, fund_y), "南向资金（近30日）", fill="#00d4aa", font=font_medium)
        south_hist = data.get("south_history", [])
        if south_hist:
            max_val = max(abs(x["net_buy"]) for x in south_hist) or 1
            bar_w = (W // 2 - 40) // len(south_hist)
            for i, row in enumerate(south_hist[-30:]):
                x = W // 2 + 10 + i * bar_w
                val = row["net_buy"]
                bar_h = int(abs(val) / max_val * 80)
                y_top = fund_y + 30 + 80 - bar_h if val >= 0 else fund_y + 30 + 80
                color = "#00d4aa" if val >= 0 else "#ff4444"
                draw.rectangle([(x, y_top), (x + bar_w - 2, y_top + bar_h)], fill=color)

        # 底部声明
        draw.rectangle([(0, H - 40), (W, H)], fill="#0d1220")
        draw.text((20, H - 30), "本报告仅供参考，不构成投资建议。市场有风险，投资需谨慎。", fill="#555555", font=font_small)

        # 保存
        report_dir = DEFAULT_CFG["report_dir"]
        fname = os.path.join(report_dir, f"daily_report_{date.today().isoformat()}.png")
        img.save(fname)
        return fname
    except Exception as e:
        import sys; sys.stderr.write(f"生成报告图片失败: {e}\n")
        return None


# ═══════════════════════════════════════════════════════
# FastAPI 应用
# ═══════════════════════════════════════════════════════

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
import uvicorn

app = FastAPI(title="哮天每日收盘报告", version="4.0")


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_CONTENT


@app.get("/favicon.ico")
async def favicon():
    return Response(content=b"", status_code=204)


@app.get("/api/dashboard")
async def get_dashboard():
    try:
        return JSONResponse(get_dashboard_data())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/scan")
async def api_scan(market: str = "all", signal: str = "全部"):
    try:
        results = scan_market(market, signal)
        return JSONResponse({"total": len(results), "results": results})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/api/screening")
async def api_screening(params: Optional[Dict[str, Any]] = None):
    """统一选股API，支持技术面+基本面+资金面+情绪面多条件"""
    try:
        results, funnel = run_screening(params or {})
        return JSONResponse({"total": len(results), "results": results, "funnel": funnel})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/screen")
async def api_screen(params: Optional[Dict[str, Any]] = None):
    """
    SEPA-VCP 格式选股 API（兼容 sepa_vcp_app.py 前端）
    params: { config, market, fundamental, technical, vcp }
    """
    try:
        p = params or {}
        market_map = {"both": "all", "cn": "a", "hk": "hk"}
        config = p.get("config", {})
        fund = p.get("fundamental", {})
        tech = p.get("technical", {})
        vcp_p = p.get("vcp", {})
        converted = {
            "market": market_map.get(p.get("market", "both"), "all"),
            "hk_kline_dir": config.get("hk_kline_dir", DEFAULT_CFG["hk_kline_dir"]),
            "a_kline_dir": config.get("a_kline_dir", DEFAULT_CFG["a_kline_dir"]),
            "hk_fin_dir": config.get("hk_fin_dir", ""),
            "a_fin_dir": config.get("a_fin_dir", ""),
            "ma50_above": tech.get("require_ma50", False),
            "ma150_above": tech.get("require_ma150", False),
            "ma200_above": tech.get("require_ma200", False),
            "min_vol_ratio": tech.get("min_vol_ratio", 1.0),
            "min_vcp_score": vcp_p.get("min_score", 0),
            "min_rev_yoy": fund.get("rev_yoy", 0),
            "min_profit_yoy": fund.get("prof_yoy", 0),
            "min_roe": fund.get("roe", 0),
            "min_cagr": fund.get("cagr_3y", 0),
            "pe_max": fund.get("pe_max", None),
            "pb_max": fund.get("pb_max", None),
            "rsi_min": tech.get("rsi_min", None),
            "rsi_max": tech.get("rsi_max", None),
            "north_dir": p.get("fund_flow", {}).get("north_dir", "all"),
            "south_dir": p.get("fund_flow", {}).get("south_dir", "all"),
            "vix_max": p.get("sentiment", {}).get("vix_max", None),
            "vix_calm": p.get("sentiment", {}).get("vix_calm", False),
        }
        results, funnel = run_screening(converted)
        return JSONResponse({"total": len(results), "results": results, "stage_counts": funnel})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sepa/{code}")
async def api_sepa(code: str):
    try:
        result = sepa_analysis(code)
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/timing/monitor")
async def api_timing_monitor():
    try:
        return JSONResponse(get_timing_monitor())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/report/generate")
async def api_generate_report():
    try:
        path = generate_report_image()
        if path:
            return JSONResponse({"status": "ok", "path": path})
        else:
            raise HTTPException(status_code=500, detail="图片生成失败")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/report/image")
async def api_report_image():
    """返回最新报告图片路径"""
    report_dir = DEFAULT_CFG["report_dir"]
    today_str = date.today().isoformat()
    fname = os.path.join(report_dir, f"daily_report_{today_str}.png")
    if os.path.exists(fname):
        return JSONResponse({"status": "ok", "path": fname, "exists": True})
    return JSONResponse({"status": "ok", "path": fname, "exists": False})


@app.get("/api/path/info")
async def api_path_info(path: str):
    """返回指定目录的最新文件修改日期"""
    try:
        if not os.path.isdir(path):
            return JSONResponse({"status": "ok", "path": path, "latest": None, "count": 0})
        files = [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]
        if not files:
            return JSONResponse({"status": "ok", "path": path, "latest": None, "count": 0})
        latest_mtime = max(os.path.getmtime(os.path.join(path, f)) for f in files)
        latest_date = date.fromtimestamp(latest_mtime).isoformat()
        return JSONResponse({"status": "ok", "path": path, "latest": latest_date, "count": len(files)})
    except Exception as e:
        return JSONResponse({"status": "error", "path": path, "latest": None, "count": 0, "error": str(e)})


# ═══════════════════════════════════════════════════════
# HTML 内容（全内嵌，深色专业交易终端风格）
# ═══════════════════════════════════════════════════════

HTML_CONTENT = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>哮天每日报告</title>
<style>
:root {
  --bg: #0a0e17;
  --card: #131a2a;
  --card-hover: #1a2338;
  --accent: #00d4aa;
  --accent-dim: #00a888;
  --red: #ff4444;
  --green: #00d4aa;
  --yellow: #ffaa00;
  --text: #e0e6f0;
  --text-dim: #8899aa;
  --border: #1e2a3a;
  --font: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--font);
  min-height: 100vh;
}

header {
  background: var(--card);
  border-bottom: 1px solid var(--border);
  padding: 0 24px;
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 100;
}

header .logo {
  font-size: 18px;
  font-weight: 700;
  color: var(--accent);
  letter-spacing: 1px;
}

header .logo span { color: var(--text-dim); font-weight: 400; }

/* ── Tab 导航栏 ───────────────────────────── */
.tabs {
  display: flex;
  gap: 2px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 4px;
}

.tab {
  padding: 8px 20px;
  border-radius: 7px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-dim);
  transition: all 0.2s;
  border: none;
  background: transparent;
  display: flex;
  align-items: center;
  gap: 6px;
}

.tab:hover { color: var(--text); background: var(--bg); }
.tab.active { color: var(--accent); background: rgba(0,212,170,0.15); box-shadow: 0 1px 4px rgba(0,0,0,0.15); }


.main { padding: 20px 24px; max-width: 1400px; margin: 0 auto; }

/* ── Dashboard ─────────────────────────────── */
.market-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 20px;
}

.market-card {
  background: var(--card);
  border-radius: 10px;
  padding: 18px;
  border: 1px solid var(--border);
}

.market-card .label {
  font-size: 12px;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 8px;
}

.market-card .price {
  font-size: 26px;
  font-weight: 700;
  color: var(--text);
  margin-bottom: 4px;
}

.market-card .chg { font-size: 14px; font-weight: 600; }
.market-card .ma-status {
  font-size: 11px;
  color: var(--text-dim);
  margin-top: 6px;
  display: flex;
  align-items: center;
  gap: 4px;
}

.ma-dot { width: 6px; height: 6px; border-radius: 50%; display: inline-block; }
.ma-dot.up { background: var(--green); }
.ma-dot.down { background: var(--red); }

.signals-summary {
  background: var(--card);
  border-radius: 10px;
  padding: 18px;
  border: 1px solid var(--border);
  margin-bottom: 20px;
}

.signals-summary h3 {
  font-size: 14px;
  color: var(--accent);
  margin-bottom: 12px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.signal-list { display: flex; gap: 10px; flex-wrap: wrap; }

.signal-badge {
  padding: 6px 14px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 600;
}

.signal-badge.bull { background: rgba(0,212,170,0.15); color: var(--green); border: 1px solid rgba(0,212,170,0.3); }
.signal-badge.bear { background: rgba(255,68,68,0.15); color: var(--red); border: 1px solid rgba(255,68,68,0.3); }
.signal-badge.neutral { background: rgba(255,170,0,0.15); color: var(--yellow); border: 1px solid rgba(255,170,0,0.3); }

.fund-section {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 20px;
}

.fund-card {
  background: var(--card);
  border-radius: 10px;
  padding: 18px;
  border: 1px solid var(--border);
}

.fund-card h4 { font-size: 13px; color: var(--accent); margin-bottom: 12px; }

.fund-bar-chart {
  display: flex;
  align-items: center;
  gap: 2px;
  height: 80px;
  position: relative;
}

.fund-bar-chart::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 0;
  right: 0;
  height: 1px;
  background: var(--border);
  transform: translateY(-50%);
}

.fund-bar {
  flex: 1;
  border-radius: 2px;
  min-width: 4px;
  transition: opacity 0.2s;
  position: relative;
}

.fund-bar.up {
  background: var(--green);
  transform-origin: bottom center;
}

.fund-bar.down {
  background: var(--red);
  transform-origin: top center;
}

.fund-bar-labels {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
  color: var(--text-dim);
  margin-top: 4px;
}

/* ── 选股扫描 ─────────────────────────────── */
.scan-controls {
  background: var(--card);
  border-radius: 10px;
  padding: 18px;
  border: 1px solid var(--border);
  margin-bottom: 20px;
  display: flex;
  gap: 14px;
  align-items: center;
  flex-wrap: wrap;
}

.scan-controls select, .scan-controls input {
  background: var(--bg);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 14px;
  font-size: 14px;
  outline: none;
}

.scan-controls select:focus, .scan-controls input:focus {
  border-color: var(--accent);
}

.btn {
  padding: 8px 20px;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  border: none;
  transition: all 0.2s;
}

.btn-primary { background: var(--accent); color: #0a0e17; }
.btn-primary:hover { background: var(--accent-dim); }
.btn-secondary { background: var(--card-hover); color: var(--text); border: 1px solid var(--border); }
.btn-secondary:hover { border-color: var(--accent); }

.scan-results {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 14px;
}

.stock-card {
  background: var(--card);
  border-radius: 10px;
  padding: 16px;
  border: 1px solid var(--border);
  cursor: pointer;
  transition: all 0.2s;
}

.stock-card:hover {
  border-color: var(--accent);
  transform: translateY(-2px);
  box-shadow: 0 4px 20px rgba(0,212,170,0.1);
}

.stock-card .code {
  font-size: 12px;
  color: var(--text-dim);
  margin-bottom: 4px;
}

.stock-card .name {
  font-size: 16px;
  font-weight: 700;
  color: var(--text);
  margin-bottom: 8px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.stock-card .signals {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.stock-tag {
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
}

.stock-tag.rsi { background: rgba(255,68,68,0.2); color: #ff8888; }
.stock-tag.macd { background: rgba(0,212,170,0.2); color: var(--accent); }
.stock-tag.cci { background: rgba(255,170,0,0.2); color: var(--yellow); }
.stock-tag.kdj { background: rgba(100,150,255,0.2); color: #88aaff; }
.stock-tag.boll { background: rgba(180,100,255,0.2); color: #cc88ff; }
.stock-tag.stage { background: rgba(0,200,150,0.2); color: #00dd99; }

.stock-card .rsi-val {
  font-size: 13px;
  color: var(--text-dim);
}

.stock-card .rsi-val strong {
  color: var(--text);
  font-size: 15px;
}

.scan-stats {
  font-size: 13px;
  color: var(--text-dim);
  margin-bottom: 14px;
}

/* ── 择时监测 ─────────────────────────────── */
.timing-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 20px;
}

.timing-card {
  background: var(--card);
  border-radius: 10px;
  padding: 20px;
  border: 1px solid var(--border);
}

.timing-card .timing-title {
  font-size: 13px;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 14px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.timing-card .direction {
  font-size: 24px;
  font-weight: 800;
  margin-bottom: 8px;
}

.direction.bull { color: var(--green); }
.direction.bear { color: var(--red); }
.direction.neutral { color: var(--yellow); }

.timing-card .confidence {
  font-size: 12px;
  padding: 3px 10px;
  border-radius: 10px;
  display: inline-block;
  margin-bottom: 10px;
}

.confidence.high { background: rgba(0,212,170,0.2); color: var(--green); }
.confidence.med { background: rgba(255,170,0,0.2); color: var(--yellow); }
.confidence.low { background: rgba(100,100,120,0.2); color: var(--text-dim); }

.timing-card .reason { font-size: 13px; color: var(--text-dim); line-height: 1.5; }
.timing-card .detail { font-size: 12px; color: var(--text-dim); margin-top: 8px; font-family: monospace; }

.timing-refresh { text-align: center; margin-bottom: 20px; }

/* ── 每日报告 ─────────────────────────────── */
.report-panel {
  background: var(--card);
  border-radius: 10px;
  padding: 24px;
  border: 1px solid var(--border);
  text-align: center;
}

.report-panel .generate-btn {
  padding: 14px 40px;
  font-size: 16px;
  margin-bottom: 20px;
}

.report-preview {
  margin-top: 20px;
  display: none;
}

.report-preview.visible { display: block; }

.report-preview img {
  max-width: 100%;
  border-radius: 8px;
  border: 1px solid var(--border);
}

.report-actions { margin-top: 14px; display: flex; gap: 10px; justify-content: center; }

/* ── Loading / Error ──────────────────────── */
.loading { text-align: center; padding: 40px; color: var(--text-dim); }
.loading::after { content: '...'; animation: dots 1.5s infinite; }
@keyframes dots { 0%{content:'.'} 33%{content:'..'} 66%{content:'...'} }

.error-msg { background: rgba(255,68,68,0.1); border: 1px solid rgba(255,68,68,0.3); border-radius: 8px; padding: 14px; color: var(--red); font-size: 13px; }

/* ── 隐藏tab内容 ─────────────────────────── */
.tab-content { display: none; }
.tab-content.active { display: block; }

/* ── 选股系统 ─────────────────────────────── */
.screen-filters {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 16px;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-start;
}

.filter-group {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  background: var(--bg);
  border-radius: 8px;
  border: 1px solid var(--border);
}

.filter-group select {
  background: var(--bg);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 4px 8px;
  font-size: 13px;
  outline: none;
}

.filter-section {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  min-width: 160px;
}

.filter-header {
  padding: 10px 14px;
  font-size: 12px;
  font-weight: 700;
  color: var(--text);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  user-select: none;
  background: linear-gradient(135deg, rgba(0,212,170,0.08), transparent);
}

.filter-header:hover { background: rgba(0,212,170,0.12); }

.section-count { font-size: 10px; color: var(--text-dim); font-weight: 400; }
.toggle-icon { font-size: 10px; color: var(--text-dim); transition: transform 0.2s; }
.filter-section.open .toggle-icon { transform: rotate(180deg); }

.filter-body {
  display: none;
  padding: 10px 14px;
  gap: 8px;
  flex-wrap: wrap;
  border-top: 1px solid var(--border);
}
.filter-section.open .filter-body { display: flex; }

.filter-check {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--text-dim);
  cursor: pointer;
  white-space: nowrap;
}
.filter-check input { accent-color: var(--accent); cursor: pointer; }
.filter-check:hover { color: var(--text); }

.filter-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-dim);
  white-space: nowrap;
}
.filter-row input {
  background: var(--card);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 4px 7px;
  font-size: 12px;
  outline: none;
  width: 58px;
}
.filter-row input:focus { border-color: var(--accent); }

/* ── Funnel Bar ────────────────────────────── */
.funnel-bar {
  display: flex;
  gap: 6px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.funnel-step {
  padding: 4px 14px;
  border-radius: 20px;
  font-size: 11px;
  font-weight: 600;
  background: var(--card);
  border: 1px solid var(--border);
  color: var(--text-dim);
}
.funnel-step.active { background: rgba(0,212,170,0.15); border-color: var(--accent); color: var(--accent); }

/* ── Results Table ────────────────────────── */
.screen-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  margin-top: 8px;
}
.screen-table th {
  background: var(--bg);
  color: var(--text-dim);
  font-weight: 700;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 8px 10px;
  text-align: left;
  border-bottom: 2px solid var(--border);
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
}
.screen-table th:hover { color: var(--accent); }
.screen-table td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--border);
  color: var(--text);
}
.screen-table tr:hover td { background: rgba(0,212,170,0.05); }
.screen-table td.num { text-align: right; font-family: monospace; }

/* ── mini chart (纯CSS) ─────────────────── */
.mini-chart {
.mini-chart {
  display: flex;
  align-items: center;
  gap: 1px;
  height: 30px;
}

.mini-bar {
  width: 3px;
  border-radius: 1px;
  background: var(--accent);
  opacity: 0.6;
}
/* ── SEPA-VCP 样式 ── */
.mkt-btn {
  flex: 1; padding: 9px; border-radius: 8px; border: 1px solid #2a3a50;
  background: #0f1623; color: #8899aa; cursor: pointer; font-size: 0.82em;
  transition: all 0.2s; text-align: center;
}
.mkt-btn.active { background: var(--accent); color: #000; border-color: var(--accent); font-weight: 600; }
.mkt-btn:hover:not(.active) { border-color: var(--accent); color: var(--accent); background: #0f1623; }

.val-badge {
  display: inline-block; background: var(--accent); color: #fff;
  border-radius: 4px; padding: 1px 7px; font-size: 0.72em; min-width: 36px; text-align: center;
}

.stat-chip {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 6px; padding: 6px 12px; font-size: 0.72em; color: var(--text-dim);
}
.stat-chip b { color: var(--text); }
.stat-chip.green { border-color: var(--green); color: var(--green); }
.stat-chip.green b { color: var(--green); }

.market-tag {
  border-radius: 4px; padding: 1px 6px; font-size: 0.7em;
}
.market-A { background: #ff6b6b33; color: #ff6b6b; }
.market-HK { background: #5b8af033; color: #5b8af0; }

</style>
</head>
<body>

<header>
  <div class="logo">🐾 哮天 <span>每日收盘报告</span></div>
  <div class="tabs">
    <button class="tab active" data-tab="dashboard" onclick="switchTab(this)">📊 Dashboard</button>
    <button class="tab" data-tab="screening" onclick="switchTab(this)">🎯 SEPA × VCP</button>
    <button class="tab" data-tab="classic" onclick="switchTab(this)">📊 智能筛选</button>
    <button class="tab" data-tab="analysis" onclick="switchTab(this)">🔍 个股分析</button>
    <button class="tab" data-tab="timing" onclick="switchTab(this)">⏱️ 择时监测</button>
    <button class="tab" data-tab="report" onclick="switchTab(this)">📋 每日报告</button>
  </div>
</header>

<div class="main">

  <!-- Dashboard Tab -->
  <div id="tab-dashboard" class="tab-content active">
    <div class="market-grid" id="market-grid">
      <div class="loading">加载中</div>
    </div>

    <div class="signals-summary">
      <h3>📡 今日择时信号摘要</h3>
      <div class="signal-list" id="signal-list">
        <span class="signal-badge neutral">加载中...</span>
      </div>
    </div>

    <div class="fund-section">
      <div class="fund-card">
        <h4>北向资金（万元）近30日</h4>
        <div class="fund-bar-chart" id="north-chart"></div>
        <div class="fund-bar-labels">
          <span>30日前</span><span>今日</span>
        </div>
      </div>
      <div class="fund-card">
        <h4>南向资金（亿元）近30日</h4>
        <div class="fund-bar-chart" id="south-chart"></div>
        <div class="fund-bar-labels">
          <span>30日前</span><span>今日</span>
        </div>
      </div>
    </div>
  </div>

  <!-- 选股系统 Tab -->
  <div id="tab-screening" class="tab-content">
    <div style="max-width:900px;margin:0 auto">

      <!-- ── 标题 ── -->
      <div style="text-align:center;margin-bottom:16px">
        <h2 style="color:var(--accent);font-size:1.1em;letter-spacing:2px">📈 SEPA × VCP 选股系统</h2>
        <p style="color:var(--text-dim);font-size:0.75em">马克·米勒维尼趋势策略 · A股 + 港股</p>
      </div>

      <!-- ── 路径配置 ── -->
      <div class="card" style="margin-bottom:12px">
        <div class="card-title">⚙️ 数据路径配置</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:6px">
          <div style="display:flex;align-items:center;gap:6px">
            <label style="font-size:0.72em;color:var(--text-dim);min-width:70px">A股K线</label>
            <input id="cfg-a-kline" type="text" value="/Users/tonyleung/Downloads/股票/A股/Kline"
              style="flex:1;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);padding:5px 8px;font-size:0.75em;outline:none">
            <button onclick="updatePathInfo('cfg-a-kline', this)" style="padding:4px 10px;border-radius:6px;border:1px solid var(--accent);background:transparent;color:var(--accent);font-size:0.72em;cursor:pointer;white-space:nowrap">更新</button>
            <span id="cfg-a-kline-info" style="font-size:0.68em;color:var(--text-dim);min-width:70px"></span>
          </div>
          <div style="display:flex;align-items:center;gap:6px">
            <label style="font-size:0.72em;color:var(--text-dim);min-width:70px">港股K线</label>
            <input id="cfg-hk-kline" type="text" value="/Users/tonyleung/Downloads/股票/港股/Kline"
              style="flex:1;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);padding:5px 8px;font-size:0.75em;outline:none">
            <button onclick="updatePathInfo('cfg-hk-kline', this)" style="padding:4px 10px;border-radius:6px;border:1px solid var(--accent);background:transparent;color:var(--accent);font-size:0.72em;cursor:pointer;white-space:nowrap">更新</button>
            <span id="cfg-hk-kline-info" style="font-size:0.68em;color:var(--text-dim);min-width:70px"></span>
          </div>
          <div style="display:flex;align-items:center;gap:6px">
            <label style="font-size:0.72em;color:var(--text-dim);min-width:70px">A股财报</label>
            <input id="cfg-a-fin" type="text" value="/Users/tonyleung/Downloads/股票/A股/财报"
              style="flex:1;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);padding:5px 8px;font-size:0.75em;outline:none">
            <button onclick="updatePathInfo('cfg-a-fin', this)" style="padding:4px 10px;border-radius:6px;border:1px solid var(--accent);background:transparent;color:var(--accent);font-size:0.72em;cursor:pointer;white-space:nowrap">更新</button>
            <span id="cfg-a-fin-info" style="font-size:0.68em;color:var(--text-dim);min-width:70px"></span>
          </div>
          <div style="display:flex;align-items:center;gap:6px">
            <label style="font-size:0.72em;color:var(--text-dim);min-width:70px">港股财报</label>
            <input id="cfg-hk-fin" type="text" value="/Users/tonyleung/Downloads/股票/港股/财报"
              style="flex:1;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);padding:5px 8px;font-size:0.75em;outline:none">
            <button onclick="updatePathInfo('cfg-hk-fin', this)" style="padding:4px 10px;border-radius:6px;border:1px solid var(--accent);background:transparent;color:var(--accent);font-size:0.72em;cursor:pointer;white-space:nowrap">更新</button>
            <span id="cfg-hk-fin-info" style="font-size:0.68em;color:var(--text-dim);min-width:70px"></span>
          </div>
        </div>
      </div>

      <!-- ── 市场选择 ── -->
      <div class="card" style="margin-bottom:12px">
        <div class="card-title">🎯 市场选择</div>
        <select id="market-select" onchange="setMarket(this)" style="
          width: 100%; padding: 11px 14px; border-radius: 8px;
          border: 2px solid #00d4aa; background: #0f1623;
          color: #00d4aa; font-size: 0.9em; font-weight: 600;
          cursor: pointer; outline: none; appearance: none;
          -webkit-appearance: none;
          background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath fill='%2300d4aa' d='M6 8L0 0h12z'/%3E%3C/svg%3E");
          background-repeat: no-repeat;
          background-position: right 14px center;
        ">
          <option value="both">📈 A股 + 港股</option>
          <option value="cn">🟢 仅 A股</option>
          <option value="hk">🔵 仅 港股</option>
        </select>
      </div>

      <!-- ── 筛选条件 ── -->
      <div class="card" style="margin-bottom:12px">
        <div class="card-title">🔍 筛选条件</div>

        <div style="font-size:0.65em;color:var(--text-dim);letter-spacing:1px;margin:8px 0 6px;text-transform:uppercase">基本面</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px">
          <div>
            <label style="font-size:0.72em;color:var(--muted)">营收 YoY &gt; <span class="val-badge" id="lbl-rev">25</span>%</label>
            <input type="range" id="sl-rev" min="0" max="80" value="25" step="5"
              style="width:100%;accent-color:var(--accent)" oninput="syncSlider(this,'lbl-rev')">
          </div>
          <div>
            <label style="font-size:0.72em;color:var(--muted)">净利 YoY &gt; <span class="val-badge" id="lbl-prof">30</span>%</label>
            <input type="range" id="sl-prof" min="0" max="100" value="30" step="5"
              style="width:100%;accent-color:var(--accent)" oninput="syncSlider(this,'lbl-prof')">
          </div>
          <div>
            <label style="font-size:0.72em;color:var(--muted)">ROE &gt; <span class="val-badge" id="lbl-roe">15</span>%</label>
            <input type="range" id="sl-roe" min="0" max="40" value="15" step="1"
              style="width:100%;accent-color:var(--accent)" oninput="syncSlider(this,'lbl-roe')">
          </div>
          <div>
            <label style="font-size:0.72em;color:var(--muted)">3年CAGR &gt; <span class="val-badge" id="lbl-cagr">20</span>%</label>
            <input type="range" id="sl-cagr" min="0" max="60" value="20" step="5"
              style="width:100%;accent-color:var(--accent)" oninput="syncSlider(this,'lbl-cagr')">
          </div>
          <div>
            <label style="font-size:0.72em;color:var(--muted)">PE &lt; <span class="val-badge" id="lbl-pe">100</span></label>
            <input type="range" id="sl-pe" min="0" max="100" value="100" step="5"
              style="width:100%;accent-color:var(--accent)" oninput="syncSlider(this,'lbl-pe')">
          </div>
          <div>
            <label style="font-size:0.72em;color:var(--muted)">PB &lt; <span class="val-badge" id="lbl-pb">20</span></label>
            <input type="range" id="sl-pb" min="0" max="20" value="20" step="1"
              style="width:100%;accent-color:var(--accent)" oninput="syncSlider(this,'lbl-pb')">
          </div>
        </div>

        <div style="font-size:0.65em;color:var(--text-dim);letter-spacing:1px;margin:8px 0 6px;text-transform:uppercase">技术面</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px">
          <div>
            <label style="font-size:0.72em;color:var(--muted)">量比(10日/120日) &gt; <span class="val-badge" id="lbl-vol">1.0</span>x</label>
            <input type="range" id="sl-vol" min="0.5" max="5" value="1.0" step="0.1"
              style="width:100%;accent-color:var(--accent)" oninput="syncSliderFloat(this,'lbl-vol')">
          </div>
          <div>
            <label style="font-size:0.72em;color:var(--muted)">VCP评分 &ge; <span class="val-badge" id="lbl-vcp">60</span>分</label>
            <input type="range" id="sl-vcp" min="20" max="100" value="60" step="5"
              style="width:100%;accent-color:var(--accent)" oninput="syncSlider(this,'lbl-vcp')">
          </div>
          <div>
            <label style="font-size:0.72em;color:var(--muted)">RSI(14) &gt; <span class="val-badge" id="lbl-rsi-min">0</span></label>
            <input type="range" id="sl-rsi-min" min="0" max="70" value="0" step="5"
              style="width:100%;accent-color:var(--accent)" oninput="syncSlider(this,'lbl-rsi-min')">
          </div>
          <div>
            <label style="font-size:0.72em;color:var(--muted)">RSI(14) &lt; <span class="val-badge" id="lbl-rsi-max">100</span></label>
            <input type="range" id="sl-rsi-max" min="30" max="100" value="100" step="5"
              style="width:100%;accent-color:var(--accent)" oninput="syncSlider(this,'lbl-rsi-max')">
          </div>
        </div>
        <div style="margin-top:6px">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:0.8em;color:var(--text)">
            <input type="checkbox" id="sl-ma50" checked style="accent-color:var(--accent)">
            要求股价 &gt; 50日均线(MA50)
          </label>
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:0.8em;color:var(--text);margin-top:4px">
            <input type="checkbox" id="sl-ma150" checked style="accent-color:var(--accent)">
            要求 MA50 &gt; 150日均线(MA150)，即均线多头排列
          </label>
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:0.8em;color:var(--text);margin-top:4px">
            <input type="checkbox" id="sl-ma200" style="accent-color:var(--accent)">
            要求股价 &gt; 200日均线(MA200)，即长期趋势向上
          </label>
        </div>

        <div style="font-size:0.65em;color:var(--text-dim);letter-spacing:1px;margin:8px 0 6px;text-transform:uppercase">资金面</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px">
          <div>
            <label style="font-size:0.72em;color:var(--muted)">北向资金方向</label>
            <select id="sl-north-dir" style="width:100%;padding:6px;border:1px solid var(--border);border-radius:4px;background:var(--bg-card);color:var(--text);font-size:0.75em;margin-top:4px">
              <option value="all">全部</option>
              <option value="buy">净买入</option>
              <option value="sell">净卖出</option>
            </select>
          </div>
          <div>
            <label style="font-size:0.72em;color:var(--muted)">南向资金方向</label>
            <select id="sl-south-dir" style="width:100%;padding:6px;border:1px solid var(--border);border-radius:4px;background:var(--bg-card);color:var(--text);font-size:0.75em;margin-top:4px">
              <option value="all">全部</option>
              <option value="buy">净买入</option>
              <option value="sell">净卖出</option>
            </select>
          </div>
        </div>

        <div style="font-size:0.65em;color:var(--text-dim);letter-spacing:1px;margin:8px 0 6px;text-transform:uppercase">情绪面</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px">
          <div>
            <label style="font-size:0.72em;color:var(--muted)">VIX &lt; <span class="val-badge" id="lbl-vix">50</span></label>
            <input type="range" id="sl-vix" min="10" max="50" value="50" step="2"
              style="width:100%;accent-color:var(--accent)" oninput="syncSlider(this,'lbl-vix')">
          </div>
          <div style="display:flex;align-items:flex-end">
            <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:0.8em;color:var(--text)">
              <input type="checkbox" id="sl-vix-calm" style="accent-color:var(--accent)">
              要求VIX &lt; 25（市场平静）
            </label>
          </div>
        </div>
      </div>

      <!-- ── 按钮 ── -->
      <div style="display:flex;gap:10px;margin-bottom:14px">
        <button id="btn-run" onclick="runSepaVcp()" style="flex:1;padding:12px;border-radius:8px;border:none;background:var(--accent);color:#000;font-size:0.9em;font-weight:600;cursor:pointer">▶ 运行筛选</button>
        <button onclick="resetSepaVcp()" style="padding:12px 16px;border-radius:8px;border:1px solid var(--border);background:var(--bg-card);color:var(--text);font-size:0.85em;cursor:pointer">↺ 重置</button>
        <button onclick="exportSepaCsv()" style="padding:12px 16px;border-radius:8px;border:1px solid var(--border);background:var(--bg-card);color:var(--accent);font-size:0.85em;cursor:pointer">💾 导出</button>
      </div>
      <div id="sepa-status" style="font-size:0.78em;min-height:18px;margin-bottom:10px"></div>

      <!-- ── 筛选漏斗 ── -->
      <div class="card" id="sepa-funnel-card" style="display:none;margin-bottom:12px">
        <div class="card-title">🔬 筛选漏斗 · 各条件命中数量</div>
        <div id="sepa-funnel-body" style="display:flex;gap:6px;flex-wrap:wrap;align-items:center"></div>
      </div>

      <!-- ── 结果表格 ── -->
      <div class="card" id="sepa-result-card" style="display:none">
        <div class="card-title">📊 筛选结果</div>
        <div style="overflow-x:auto">
          <table style="width:100%;border-collapse:collapse;font-size:0.75em">
            <thead>
              <tr style="background:#12141e;color:var(--accent)">
                <th style="padding:8px 10px;text-align:left;white-space:nowrap">#</th>
                <th style="padding:8px 10px;text-align:left;white-space:nowrap;cursor:pointer" onclick="sortSepa('code')">代码 ↕</th>
                <th style="padding:8px 10px;text-align:left;white-space:nowrap">名称</th>
                <th style="padding:8px 10px;text-align:left;white-space:nowrap;cursor:pointer" onclick="sortSepa('market')">市场 ↕</th>
                <th style="padding:8px 10px;text-align:left;white-space:nowrap;cursor:pointer" onclick="sortSepa('sector')">板块 ↕</th>
                <th style="padding:8px 10px;text-align:right;white-space:nowrap;cursor:pointer" onclick="sortSepa('close')">最新价 ↕</th>
                <th style="padding:8px 10px;text-align:right;white-space:nowrap">MA50 ↕</th>
                <th style="padding:8px 10px;text-align:right;white-space:nowrap">MA150 ↕</th>
                <th style="padding:8px 10px;text-align:right;white-space:nowrap;cursor:pointer" onclick="sortSepa('vol_ratio')">量比 ↕</th>
                <th style="padding:8px 10px;text-align:right;white-space:nowrap;cursor:pointer" onclick="sortSepa('vcp_score')">VCP评分 ↕</th>
                <th style="padding:8px 10px;text-align:center">收缩</th>
                <th style="padding:8px 10px;text-align:center">突破</th>
                <th style="padding:8px 10px;text-align:right">营收YoY</th>
                <th style="padding:8px 10px;text-align:right">净利YoY</th>
                <th style="padding:8px 10px;text-align:right">ROE</th>
                <th style="padding:8px 10px;text-align:right">3年CAGR</th>
              </tr>
            </thead>
            <tbody id="sepa-tbody"></tbody>
          </table>
        </div>
      </div>

    </div>
  </div>

  <!-- 智能筛选 Tab（经典多维度选股） -->
  <div id="tab-classic" class="tab-content">
    <div class="screen-filters">
      <div class="filter-group">
        <label style="font-size:12px;color:var(--text-dim)">市场：</label>
        <select id="s-market">
          <option value="all">全部</option><option value="hk">港股</option><option value="a">A股</option>
        </select>
      </div>

      <div class="filter-section open">
        <div class="filter-header" onclick="toggleSection(this)">
          <span>📈 技术面</span><span class="section-count">已选 0 项</span><span class="toggle-icon">▼</span>
        </div>
        <div class="filter-body">
          <label class="filter-check"><input type="checkbox" id="s-ma50"> MA50在价格上方</label>
          <label class="filter-check"><input type="checkbox" id="s-ma150"> MA150在价格上方</label>
          <label class="filter-check"><input type="checkbox" id="s-ma200"> MA200在价格上方</label>
          <div class="filter-row"><span>量比 ≥</span><input type="number" id="s-vol-ratio" value="0.5" min="0" step="0.1" style="width:60px"></div>
          <div class="filter-row"><span>VCP评分 ≥</span><input type="number" id="s-vcp" value="30" min="0" max="100" style="width:60px"></div>
          <div class="filter-row"><span>RSI ≤</span><input type="number" id="s-rsi-max" value="70" min="30" max="100" style="width:60px"></div>
          <div class="filter-row"><span>RSI ≥</span><input type="number" id="s-rsi-min" placeholder="不限" min="0" max="100" style="width:60px"></div>
        </div>
      </div>

      <div class="filter-section open">
        <div class="filter-header" onclick="toggleSection(this)">
          <span>📊 基本面</span><span class="section-count">已选 0 项</span><span class="toggle-icon">▼</span>
        </div>
        <div class="filter-body">
          <div class="filter-row"><span>营收增速 YoY ≥</span><input type="number" id="s-rev-yoy" value="25" min="0" max="100" style="width:60px">%</div>
          <div class="filter-row"><span>净利润增速 YoY ≥</span><input type="number" id="s-profit-yoy" value="30" min="0" max="100" style="width:60px">%</div>
          <div class="filter-row"><span>ROE ≥</span><input type="number" id="s-roe" value="10" min="0" max="50" style="width:60px">%</div>
          <div class="filter-row"><span>3年CAGR ≥</span><input type="number" id="s-cagr" value="20" min="0" max="100" style="width:60px">%</div>
        </div>
      </div>

      <div class="filter-section open">
        <div class="filter-header" onclick="toggleSection(this)">
          <span>💰 资金面</span><span class="section-count">已选 0 项</span><span class="toggle-icon">▼</span>
        </div>
        <div class="filter-body">
          <div class="filter-row"><span>北向资金：</span><select id="s-north-dir" style="width:90px"><option value="all">全部</option><option value="buy">净买入</option><option value="sell">净卖出</option></select></div>
          <div class="filter-row"><span>南向资金：</span><select id="s-south-dir" style="width:90px"><option value="all">全部</option><option value="buy">净买入</option><option value="sell">净卖出</option></select></div>
        </div>
      </div>

      <div class="filter-section open">
        <div class="filter-header" onclick="toggleSection(this)">
          <span>🌊 情绪面</span><span class="section-count">已选 0 项</span><span class="toggle-icon">▼</span>
        </div>
        <div class="filter-body">
          <div class="filter-row"><span>VIX ≤</span><input type="number" id="s-vix-max" placeholder="不限" min="0" max="100" style="width:60px"></div>
          <label class="filter-check"><input type="checkbox" id="s-vix-calm"> 仅VIX平静（≤25）</label>
        </div>
      </div>

      <div style="display:flex;gap:10px;padding:10px 0">
        <button onclick="runScreening()" style="background:var(--accent);color:#000;font-weight:700;border:none;border-radius:6px;padding:9px 24px;cursor:pointer;font-size:14px">🔍 开始选股</button>
        <button onclick="resetScreening()" style="background:var(--bg-card);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:9px 16px;cursor:pointer;font-size:13px">重置</button>
        <button id="export-csv-btn" onclick="exportScreeningCSV()" style="display:none;background:var(--bg-card);color:var(--accent);border:1px solid var(--accent);border-radius:6px;padding:9px 16px;cursor:pointer;font-size:13px">📥 导出CSV</button>
      </div>
    </div>

    <div id="s-funnel" class="funnel-bar" style="display:none"></div>

    <div id="s-results" style="display:none">
      <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0">
        <span id="s-result-count" style="font-size:13px;color:var(--text-dim)"></span>
      </div>
      <div style="overflow-x:auto">
        <table class="screen-table" id="s-table">
          <thead id="s-table-head"></thead>
          <tbody id="s-table-body"></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- 个股分析 Tab -->
  <div id="tab-analysis" class="tab-content">
    <div style="padding:10px 0;display:flex;gap:10px;align-items:center">
      <input id="ana-stock" type="text" placeholder="输入股票代码，如 000001.SZ" value=""
        style="flex:1;background:var(--bg-card);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:8px 12px;font-size:14px">
      <button onclick="loadAnalysis()" style="background:var(--accent);color:#000;font-weight:700;border:none;border-radius:6px;padding:8px 20px;cursor:pointer;font-size:14px">分析</button>
    </div>
    <div id="ana-result" style="padding:10px 0"></div>
  </div><!-- 择时监测 Tab -->
  <div id="tab-timing" class="tab-content">
    <div class="timing-refresh">
      <button class="btn btn-primary" onclick="refreshTiming()">刷新数据</button>
    </div>
    <div class="timing-grid" id="timing-grid">
      <div class="loading">加载中</div>
    </div>
  </div>

  <!-- 每日报告 Tab -->
  <div id="tab-report" class="tab-content">
    <div class="report-panel">
      <button class="btn btn-primary generate-btn" onclick="generateReport()">一键生成今日报告</button>
      <div class="report-preview" id="report-preview">
        <img id="report-img" src="" alt="每日报告">
        <div class="report-actions">
          <button class="btn btn-secondary" onclick="downloadReport()">下载图片</button>
        </div>
      </div>
    </div>
  </div>

</div>

<script>
// ─────────────────────────────────────
// 工具函数
// ─────────────────────────────────────
function api(url) {
  return fetch(url).then(r => r.json());
}

function dirClass(d) {
  if (d === '看涨') return 'bull';
  if (d === '看跌') return 'bear';
  return 'neutral';
}

function confClass(c) {
  if (c === '高置信') return 'high';
  if (c === '中置信') return 'med';
  return 'low';
}

function signalBadgeClass(s) {
  if (s === '看涨') return 'bull';
  if (s === '看跌') return 'bear';
  return 'neutral';
}

// ─────────────────────────────────────
// Tab 切换
// ─────────────────────────────────────
function switchTab(domEl) {
  document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  domEl.classList.add('active');
  document.getElementById('tab-' + domEl.dataset.tab).classList.add('active');
}

// ─────────────────────────────────────
// Dashboard
// ─────────────────────────────────────
async function loadDashboard() {
  const data = await api('/api/dashboard');
  const rt = data.realtime || {};
  const hs300 = data.hs300_ma200 || {};
  const hk = data.hk_ma200 || {};
  const bt = data.black_thursday || {};
  const nm = data.north_money || {};
  const sm = data.south_money || {};
  const vix = data.vix || {};

  // 大盘卡片
  const cards = [
    { label: '沪深300', code: 'SH000300', ma_data: hs300 },
    { label: '恒生指数', code: 'HKHSI', ma_data: hk },
    { label: '纳斯达克', code: 'NDX', ma_data: {} },
    { label: '恐慌指数', code: 'VIX', extra: vix },
  ];

  let html = '';
  for (const c of cards) {
    if (c.code === 'VIX') {
      // VIX 恐慌指数特殊处理
      const vixVal = c.extra.vix;
      const vixSource = c.extra.source || '';
      const vixCls = vixVal !== null && vixVal !== undefined ? (vixVal < 20 ? 'up' : vixVal >= 25 ? 'down' : '') : '';
      html += `<div class="market-card">
        <div class="label">${c.label}</div>
        <div class="price">${vixVal !== null && vixVal !== undefined ? vixVal.toFixed(2) : '—'}</div>
        <div class="chg ${vixCls}" style="font-size:11px">${vixSource}</div>
        <div class="ma-status">
          <span class="ma-dot ${vixCls}"></span>
          ${vixVal !== null && vixVal !== undefined ? (vixVal < 20 ? '市场平静' : vixVal >= 25 ? '市场恐慌' : '正常波动') : '暂无数据'}
        </div>
      </div>`;
      continue;
    }
    const info = rt[c.code] || {};
    const price = info.price || '—';
    const chg = info.chg_pct;
    const ma_sig = c.ma_data.signal || '';
    const ma_above = c.ma_data.above;
    const ma_dot_cls = ma_above === true ? 'up' : ma_above === false ? 'down' : '';
    const ma_txt = ma_sig || '无 MA 数据';

    html += `<div class="market-card">
      <div class="label">${c.label}</div>
      <div class="price">${typeof price === 'number' ? price.toFixed(2) : price}</div>
      <div class="chg" style="color:${chg >= 0 ? 'var(--green)' : 'var(--red)'}">${typeof chg === 'number' ? (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%' : '—'}</div>
      <div class="ma-status">
        ${ma_dot_cls ? `<span class="ma-dot ${ma_dot_cls}"></span>` : ''}
        ${ma_txt}
      </div>
    </div>`;
  }
  document.getElementById('market-grid').innerHTML = html;

  // 信号摘要
  const signals = [];
  if (bt.signal === 1) signals.push({ label: '黑色星期四', d: '看涨' });
  else if (bt.signal === 0) signals.push({ label: '黑色星期四', d: '看跌' });
  if (nm.cum5 > 20) signals.push({ label: '北向资金', d: '看涨' });
  else if (nm.cum5 < -20) signals.push({ label: '北向资金', d: '看跌' });
  if (sm.cum5 > 20) signals.push({ label: '南向资金', d: '看涨' });
  else if (sm.cum5 < -20) signals.push({ label: '南向资金', d: '看跌' });
  if (vix.vix && vix.vix < 20) signals.push({ label: 'VIX(' + vix.vix + ')', d: '看涨' });
  else if (vix.vix && vix.vix >= 25) signals.push({ label: 'VIX(' + vix.vix + ')', d: '看跌' });
  if (hs300.above === true) signals.push({ label: '沪深300 MA200', d: '看涨' });
  else if (hs300.above === false) signals.push({ label: '沪深300 MA200', d: '看跌' });

  if (signals.length === 0) {
    document.getElementById('signal-list').innerHTML = '<span class="signal-badge neutral">暂无明确信号</span>';
  } else {
    document.getElementById('signal-list').innerHTML = signals.map(s =>
      `<span class="signal-badge ${signalBadgeClass(s.d)}">${s.label}：${s.d}</span>`
    ).join('');
  }

  // 北向资金柱图
  const northHist = data.north_history || [];
  renderFundChart('north-chart', northHist, 'net_buy');

  // 南向资金柱图
  const southHist = data.south_history || [];
  renderFundChart('south-chart', southHist, 'net_buy');
}

function renderFundChart(elId, rows, key) {
  const el = document.getElementById(elId);
  if (!rows || rows.length === 0) {
    el.innerHTML = '<div style="color:var(--text-dim);font-size:12px;text-align:center;padding:20px">暂无数据</div>';
    return;
  }
  const maxAbs = Math.max(...rows.map(r => Math.abs(r[key]))) || 1;
  el.innerHTML = rows.map(r => {
    const val = r[key];
    const h = Math.max(2, Math.abs(val) / maxAbs * 40); // 40px 是半高度
    const cls = val >= 0 ? 'up' : 'down';
    const style = val >= 0 
      ? `height:${h}px; transform: translateY(-${h}px);`
      : `height:${h}px;`;
    return `<div class="fund-bar ${cls}" style="${style}" title="${r.date}: ${val.toFixed(2)}"></div>`;
  }).join('');
}

// ─────────────────────────────────────
// 选股扫描
// ─────────────────────────────────────
let lastScanResults = [];

async function runScan() {
  const market = document.getElementById('scan-market').value;
  const signal = document.getElementById('scan-signal').value;
  document.getElementById('scan-results').innerHTML = '<div class="loading">扫描中，请稍候...</div>';
  document.getElementById('scan-stats').textContent = '';

  try {
    const data = await api(`/api/scan?market=${market}&signal=${encodeURIComponent(signal)}`);
    lastScanResults = data.results || [];
    document.getElementById('scan-stats').textContent = `共找到 ${lastScanResults.length} 只符合条件的股票`;

    if (lastScanResults.length === 0) {
      document.getElementById('scan-results').innerHTML = '<div class="error-msg">未找到符合条件的股票</div>';
      return;
    }

    document.getElementById('scan-results').innerHTML = lastScanResults.map(r => {
      const code = r.code || '';
      const name = r.name || code;
      const signals = r.signals || [];
      const rsi = r.rsi14;
      const snap = r.snap || {};

      const tagCls = { 'RSI超卖': 'rsi', 'MACD金叉': 'macd', 'CCI超卖': 'cci', 'KDJ金叉': 'kdj', '布林下轨': 'boll', '趋势第二阶段': 'stage' };
      const tagsHtml = signals.map(s => `<span class="stock-tag ${tagCls[s] || ''}">${s}</span>`).join('');

      return `<div class="stock-card" onclick="goSepa('${code}')">
        <div class="code">${code}</div>
        <div class="name" title="${name}">${name}</div>
        <div class="signals">${tagsHtml}</div>
        <div class="rsi-val">RSI(14): <strong>${rsi !== null ? rsi : '—'}</strong></div>
      </div>`;
    }).join('');
  } catch (e) {
    document.getElementById('scan-results').innerHTML = `<div class="error-msg">扫描失败: ${e.message}</div>`;
  }
}

function goSepa(code) {
  document.querySelector('[data-tab="sepa"]').click();
  document.getElementById('sepa-code').value = code;
  runSepa();
}

function exportScan() {
  if (!lastScanResults.length) return;
  const headers = ['code', 'name', 'market', 'close', 'rsi14', 'signals'];
  const rows = lastScanResults.map(r => [
    r.code || '', r.name || '', r.market || '', r.close || '', r.rsi14 || '', (r.signals || []).join(';')
  ]);
  const csv = [headers.join(','), ...rows.map(r => r.map(v => '"' + String(v).replace(/"/g, '""') + '"').join(','))].join('\\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = `scan_results_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click(); URL.revokeObjectURL(url);
}

// ─────────────────────────────────────
// SEPA分析
// ─────────────────────────────────────
async function runSepa() {
  const code = document.getElementById('sepa-code').value.trim();
  if (!code) return;
  const el = document.getElementById('sepa-result');
  const errEl = document.getElementById('sepa-error');
  el.innerHTML = '<div class="loading">分析中...</div>';
  if (errEl) errEl.style.display = 'none';

  try {
    const data = await api(`/api/sepa/${encodeURIComponent(code)}`);
    if (data.error) {
      el.innerHTML = `<div class="error-msg">${data.error}</div>`;
      return;
    }

    const ind = data.indicators || {};
    const signals = data.signals || [];
    const stage = ind.stage || '?';
    let stageCls = 'unknown';
    if (stage.includes('第二阶段')) stageCls = 'stage2';
    else if (stage.includes('第四阶段')) stageCls = 'stage4';
    else if (stage.includes('第一阶段')) stageCls = 'stage1';
    else if (stage.includes('第三阶段')) stageCls = 'stage3';

    const tagCls = { 'RSI超卖': 'rsi', 'MACD金叉': 'macd', 'CCI超卖': 'cci', 'KDJ金叉': 'kdj', '布林下轨': 'boll', '趋势第二阶段': 'stage' };
    const tagsHtml = signals.map(s => `<span class="stock-tag ${tagCls[s] || ''}">${s}</span>`).join('') || '<span style="color:var(--text-dim);font-size:13px">无触发信号</span>';

    el.innerHTML = `<div class="sepa-result">
      <div class="sepa-header">
        <div>
          <div class="stock-name">${data.name || code}</div>
          <div class="stock-meta">${data.code} | ${data.market} | 更新: ${data.last_date || '—'}</div>
        </div>
        <div class="sepa-stage ${stageCls}">${stage}</div>
      </div>

      <div class="signals-summary" style="margin-bottom:16px">
        <h3>触发信号</h3>
        <div class="signal-list">${tagsHtml}</div>
      </div>

      <div class="sepa-grid">
        <div class="sepa-item"><div class="label">最新价格</div><div class="value">${ind.close || '—'}</div></div>
        <div class="sepa-item"><div class="label">MA20</div><div class="value">${ind.ma20 || '—'}</div></div>
        <div class="sepa-item"><div class="label">MA50</div><div class="value">${ind.ma50 || '—'}</div></div>
        <div class="sepa-item"><div class="label">MA200</div><div class="value">${ind.ma200 || '—'}</div></div>
        <div class="sepa-item"><div class="label">RSI(14)</div><div class="value" style="color:${ind.rsi14 < 30 ? 'var(--green)' : ind.rsi14 > 70 ? 'var(--red)' : 'var(--text)'}">${ind.rsi14 || '—'}</div></div>
        <div class="sepa-item"><div class="label">CCI(20)</div><div class="value" style="color:${ind.cci20 < -100 ? 'var(--green)' : 'var(--text)'}">${ind.cci20 || '—'}</div></div>
        <div class="sepa-item"><div class="label">MACD</div><div class="value">${ind.macd || '—'}</div><div class="sub">Signal: ${ind.macd_signal || '—'}</div></div>
        <div class="sepa-item"><div class="label">KDJ K</div><div class="value">${ind.k || '—'}</div><div class="sub">D: ${ind.d || '—'} J: ${ind.j || '—'}</div></div>
        <div class="sepa-item"><div class="label">布林上轨</div><div class="value">${ind.bb_upper || '—'}</div></div>
        <div class="sepa-item"><div class="label">布林下轨</div><div class="value">${ind.bb_lower || '—'}</div></div>
        <div class="sepa-item"><div class="label">量比</div><div class="value" style="color:${ind.vol_ratio > 2 ? 'var(--green)' : 'var(--text)'}">${ind.vol_ratio || '—'}</div></div>
      </div>

      <div class="sepa-indicators">
        <div class="sepa-ind-row"><span class="ind-name">价格 &gt; MA20</span><span class="ind-val">${ind.price_above_ma20 ? '✅ 是' : '❌ 否'}</span></div>
        <div class="sepa-ind-row"><span class="ind-name">价格 &gt; MA50</span><span class="ind-val">${ind.price_above_ma50 ? '✅ 是' : '❌ 否'}</span></div>
        <div class="sepa-ind-row"><span class="ind-name">价格 &gt; MA200</span><span class="ind-val">${ind.price_above_ma200 ? '✅ 是' : '❌ 否'}</span></div>
        <div class="sepa-ind-row"><span class="ind-name">均线多头排列</span><span class="ind-val">${ind.ma_bullish ? '✅ 是' : '❌ 否'}</span></div>
        <div class="sepa-ind-row"><span class="ind-name">MACD金叉</span><span class="ind-val">${ind.macd_cross_up ? '✅ 是' : '❌ 否'}</span></div>
        <div class="sepa-ind-row"><span class="ind-name">KDJ超卖金叉</span><span class="ind-val">${ind.kdj_cross_up ? '✅ 是' : '❌ 否'}</span></div>
        <div class="sepa-ind-row"><span class="ind-name">布林下轨支撑</span><span class="ind-val">${ind.near_bb_lower ? '✅ 是' : '❌ 否'}</span></div>
      </div>
    </div>`;
  } catch (e) {
    el.innerHTML = `<div class="error-msg">分析失败: ${e.message}</div>`;
  }
}

// ─────────────────────────────────────
// 择时监测
// ─────────────────────────────────────
async function refreshTiming() {
  document.getElementById('timing-grid').innerHTML = '<div class="loading">加载中...</div>';
  try {
    const data = await api('/api/timing/monitor');
    const items = [
      { key: 'hs300_ma200', label: '沪深300 MA200' },
      { key: 'north_money', label: '北向资金' },
      { key: 'south_money', label: '南向资金' },
      { key: 'vix', label: 'VIX恐慌指数' },
      { key: 'black_thursday', label: '黑色星期四' },
      { key: 'composite', label: '综合信号' },
    ];

    document.getElementById('timing-grid').innerHTML = items.map(it => {
      const d = data[it.key] || {};
      const dir = d.direction || '—';
      const conf = d.confidence || '低置信';
      const reason = d.reason || '';
      const detail = d.detail || '';
      return `<div class="timing-card">
        <div class="timing-title">${it.label}</div>
        <div class="direction ${dirClass(dir)}">${dir}</div>
        <span class="confidence ${confClass(conf)}">${conf}</span>
        <div class="reason">${reason}</div>
        <div class="detail">${detail}</div>
      </div>`;
    }).join('');
  } catch (e) {
    document.getElementById('timing-grid').innerHTML = `<div class="error-msg">加载失败: ${e.message}</div>`;
  }
}

// ─────────────────────────────────────
// 每日报告
// ─────────────────────────────────────
async function generateReport() {
  const preview = document.getElementById('report-preview');
  const img = document.getElementById('report-img');
  preview.classList.remove('visible');
  img.src = '';
  const btn = document.querySelector('.generate-btn');
  btn.textContent = '生成中...';
  btn.disabled = true;

  try {
    const res = await fetch('/api/report/generate', { method: 'POST' });
    const json = await res.json();
    if (json.path) {
      // 检查图片是否存在
      const checkRes = await api('/api/report/image');
      if (checkRes.exists) {
        img.src = 'file://' + checkRes.path + '?t=' + Date.now();
        preview.classList.add('visible');
      } else {
        alert('报告图片已生成但无法预览，请到 ' + json.path + ' 查看');
      }
    }
  } catch (e) {
    alert('生成失败: ' + e.message);
  } finally {
    btn.textContent = '一键生成今日报告';
    btn.disabled = false;
  }
}

function downloadReport() {
  const img = document.getElementById('report-img');
  if (!img.src) return;
  const a = document.createElement('a');
  a.href = img.src;
  a.download = 'daily_report_' + new Date().toISOString().slice(0, 10) + '.png';
  a.click();
}

// ─────────────────────────────────────
// 初始化
// ─────────────────────────────────────

// ── 选股系统 JS ──────────────────────────────────────────────────────
// ── SEPA-VCP 选股 JS ─────────────────────────────────────────────────────
let sepaResults = [];
let sepaSort = { col: 'vcp_score', asc: false };

function syncSlider(el, lblId) {
  document.getElementById(lblId).textContent = el.value;
}
function syncSliderFloat(el, lblId) {
  document.getElementById(lblId).textContent = parseFloat(el.value).toFixed(1);
}

async function updatePathInfo(inputId, btnEl) {
  const path = document.getElementById(inputId).value.trim();
  const infoEl = document.getElementById(inputId + '-info');
  btnEl.textContent = '...';
  btnEl.disabled = true;
  try {
    const r = await fetch('/api/path/info?path=' + encodeURIComponent(path));
    const d = await r.json();
    if (d.latest) {
      infoEl.textContent = d.latest + ' (' + d.count + '文件)';
      infoEl.style.color = 'var(--accent)';
    } else {
      infoEl.textContent = '无数据';
      infoEl.style.color = 'var(--text-dim)';
    }
  } catch(e) {
    infoEl.textContent = '查询失败';
    infoEl.style.color = 'var(--red)';
  }
  btnEl.textContent = '更新';
  btnEl.disabled = false;
}

function setMarket(el) {
  // el is the <select> element — value is already updated on change
}

function getSepaParams() {
  return {
    config: {
      a_kline_dir:  document.getElementById('cfg-a-kline').value,
      hk_kline_dir: document.getElementById('cfg-hk-kline').value,
      a_fin_dir:    document.getElementById('cfg-a-fin').value,
      hk_fin_dir:   document.getElementById('cfg-hk-fin').value,
    },
    market: document.getElementById('market-select').value,
    fundamental: {
      rev_yoy:  parseFloat(document.getElementById('sl-rev').value),
      prof_yoy: parseFloat(document.getElementById('sl-prof').value),
      roe:      parseFloat(document.getElementById('sl-roe').value),
      cagr_3y:  parseFloat(document.getElementById('sl-cagr').value),
      pe_max:   parseFloat(document.getElementById('sl-pe').value),
      pb_max:   parseFloat(document.getElementById('sl-pb').value),
    },
    technical: {
      min_vol_ratio: parseFloat(document.getElementById('sl-vol').value),
      require_ma50:  document.getElementById('sl-ma50').checked,
      require_ma150: document.getElementById('sl-ma150').checked,
      require_ma200: document.getElementById('sl-ma200').checked,
      rsi_min:       parseFloat(document.getElementById('sl-rsi-min').value),
      rsi_max:       parseFloat(document.getElementById('sl-rsi-max').value),
    },
    fund_flow: {
      north_dir: document.getElementById('sl-north-dir').value,
      south_dir: document.getElementById('sl-south-dir').value,
    },
    sentiment: {
      vix_max:    parseFloat(document.getElementById('sl-vix').value),
      vix_calm:   document.getElementById('sl-vix-calm').checked,
    },
    vcp: { min_score: parseInt(document.getElementById('sl-vcp').value) },
  };
}

function vcpCls(score) {
  if (score >= 80) return 'var(--green)';
  if (score >= 60) return 'var(--gold)';
  return 'var(--muted)';
}

function fmt(v, suffix='', prefix='') {
  if (v === null || v === undefined) return '<span style="color:var(--muted)">N/A</span>';
  return prefix + v + suffix;
}

async function runSepaVcp() {
  const btn = document.getElementById('btn-run');
  const status = document.getElementById('sepa-status');
  btn.disabled = true;
  status.textContent = '⏳ 筛选中，请稍候...';
  status.style.color = '';
  try {
    const params = getSepaParams();
    const t0 = Date.now();
    const resp = await fetch('/api/screen', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(params),
    });
    const data = await resp.json();
    const ms = Date.now() - t0;
    if (data.error) {
      status.textContent = '❌ 错误: ' + data.error;
      status.style.color = 'var(--red)';
      return;
    }
    sepaResults = data.results || [];
    const sc = data.stage_counts || {};
    renderSepaFunnel(sc);
    renderSepaTable(sepaResults);
    status.textContent = '✅ 筛选完成，耗时 ' + (ms/1000).toFixed(1) + 's，' + sepaResults.length + ' 只符合条件';
    status.style.color = 'var(--green)';
  } catch(e) {
    status.textContent = '❌ 请求失败: ' + e.message;
    status.style.color = 'var(--red)';
  } finally {
    btn.disabled = false;
  }
}

function renderSepaFunnel(sc) {
  const card = document.getElementById('sepa-funnel-card');
  const body = document.getElementById('sepa-funnel-body');
  if (!sc || sc.total === undefined) { card.style.display = 'none'; return; }
  card.style.display = 'block';
  // 每个条件通过的数量
  const total = sc.total;
  const passed_ma50 = sc.ma50 || 0;
  const passed_ma150 = sc.ma150 || 0;
  const passed_vol = sc.vol_ratio || 0;
  const passed_vcp = sc.vcp || 0;
  const passed_fundamental = sc.fundamental || 0;
  const final = sc.final || 0;
  
  const chips = [
    { label:'总计', val: total },
    { label:'通过MA50', val: passed_ma50, green: true },
    { label:'通过MA150', val: passed_ma150, green: true },
    { label:'通过量比', val: passed_vol, green: true },
    { label:'通过VCP', val: passed_vcp, green: true },
    { label:'通过基本面', val: passed_fundamental, green: true },
    { label:'最终通过', val: final, green: true },
  ];
  body.innerHTML = chips.map(c => {
    const cls = c.green && c.val > 0 ? 'green' : '';
    return '<div class="stat-chip' + (cls ? ' '+cls : '') + '">' + c.label + ' <b>' + (c.val || 0) + '</b></div>';
  }).join('');
}

function sortSepa(col) {
  if (sepaSort.col === col) {
    sepaSort.asc = !sepaSort.asc;
  } else {
    sepaSort.col = col;
    sepaSort.asc = false;
  }
  renderSepaTable(sepaResults);
}

function renderSepaTable(rows) {
  const tbody = document.getElementById('sepa-tbody');
  const card = document.getElementById('sepa-result-card');
  if (!rows || rows.length === 0) {
    card.style.display = 'none';
    return;
  }
  card.style.display = 'block';
  const col = sepaSort.col;
  const asc = sepaSort.asc;
  const sorted = [...rows].sort(function(a, b) {
    let va = a[col], vb = b[col];
    if (va == null) return 1;
    if (vb == null) return -1;
    if (typeof va === 'string') return asc ? va.localeCompare(vb) : vb.localeCompare(va);
    return asc ? va - vb : vb - va;
  });
  let h = '';
  for (let i = 0; i < sorted.length; i++) {
    const r = sorted[i];
    const score = r.vcp_score || 0;
    h += `<tr style="cursor:pointer" data-code="${r.code}" onclick="showAnalysis('${r.code}')">`;
    h += '<td style="padding:7px 10px;color:var(--muted)">' + (i+1) + '</td>';
    h += '<td style="padding:7px 10px;font-weight:600">' + (r.code||'') + '</td>';
    h += '<td style="padding:7px 10px">' + (r.name||'') + '</td>';
    h += '<td style="padding:7px 10px"><span class="market-tag ' + (r.market==='A股'?'market-A':'market-HK') + '">' + (r.market||'') + '</span></td>';
    h += '<td style="padding:7px 10px;color:var(--muted)">' + (r.sector||'—') + '</td>';
    h += '<td style="padding:7px 10px;text-align:right;font-weight:600">' + fmt(r.close) + '</td>';
    h += '<td style="padding:7px 10px;text-align:right">' + fmt(r.ma50, '', 'MA50 ') + '</td>';
    h += '<td style="padding:7px 10px;text-align:right">' + fmt(r.ma150, '', 'MA150 ') + '</td>';
    h += '<td style="padding:7px 10px;text-align:right;' + (r.vol_ratio >= 2 ? 'color:var(--green);font-weight:600' : '') + '">' + fmt(r.vol_ratio, 'x') + '</td>';
    h += '<td style="padding:7px 10px;text-align:right;font-weight:600;color:' + vcpCls(score) + '">' + fmt(score, '分') + '</td>';
    h += '<td style="padding:7px 10px;text-align:center;color:' + (r.is_contracting ? 'var(--green)' : 'var(--muted)') + '">' + (r.is_contracting ? '✅' : '❌') + '</td>';
    h += '<td style="padding:7px 10px;text-align:center;color:var(--accent)">' + (r.breakouts || 0) + '</td>';
    h += '<td style="padding:7px 10px;text-align:right">' + fmt(r.rev_yoy, '%') + '</td>';
    h += '<td style="padding:7px 10px;text-align:right">' + fmt(r.prof_yoy, '%') + '</td>';
    h += '<td style="padding:7px 10px;text-align:right">' + fmt(r.roe, '%') + '</td>';
    h += '<td style="padding:7px 10px;text-align:right">' + fmt(r.cagr_3y, '%') + '</td>';
    h += '</tr>';
  }
  tbody.innerHTML = h;
}

function resetSepaVcp() {
  const defs = [
    // 基本面
    ['sl-rev','25','lbl-rev'], ['sl-prof','30','lbl-prof'],
    ['sl-roe','15','lbl-roe'], ['sl-cagr','20','lbl-cagr'],
    ['sl-pe','100','lbl-pe'], ['sl-pb','20','lbl-pb'],
    // 技术面
    ['sl-vol','1.0','lbl-vol',true], ['sl-vcp','60','lbl-vcp'],
    ['sl-rsi-min','0','lbl-rsi-min'], ['sl-rsi-max','100','lbl-rsi-max'],
    ['sl-vix','50','lbl-vix'],
  ];
  defs.forEach(([id, def, lbl, isFloat]) => {
    const el = document.getElementById(id);
    const lblEl = document.getElementById(lbl);
    if (el) el.value = isFloat ? parseFloat(def).toFixed(1) : def;
    if (lblEl) lblEl.textContent = isFloat ? def : def;
  });
  // 重置复选框
  document.getElementById('sl-ma50').checked = true;
  document.getElementById('sl-ma150').checked = true;
  document.getElementById('sl-ma200').checked = false;
  document.getElementById('sl-vix-calm').checked = false;
  // 重置下拉框
  document.getElementById('sl-north-dir').value = 'all';
  document.getElementById('sl-south-dir').value = 'all';
  // 重置市场选择
  document.getElementById('market-select').value = 'both';
  // 重置显示
  document.getElementById('sepa-funnel-card').style.display = 'none';
  document.getElementById('sepa-result-card').style.display = 'none';
  document.getElementById('sepa-status').textContent = '';
  sepaResults = [];
}

function exportSepaCsv() {
  if (!sepaResults.length) return;
  const headers = ['代码','名称','市场','板块','最新价','MA50','MA150','量比','VCP评分','波动收缩','基部突破','营收YoY','净利YoY','ROE','3年CAGR'];
  const rows = sepaResults.map(r => [
    r.code||'', r.name||'', r.market||'', r.sector||'—',
    r.close||'', r.ma50||'', r.ma150||'',
    r.vol_ratio||'', r.vcp_score||'',
    r.is_contracting?'是':'否', r.breakouts||'',
    r.rev_yoy||'', r.prof_yoy||'', r.roe||'', r.cagr_3y||'',
  ]);
  const csv = [headers, ...rows].map(r => r.join(',')).join('\\n');
  const blob = new Blob([csv], {type:'text/csv'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'sepa_vcp_' + new Date().toISOString().slice(0,10) + '.csv';
  a.click();
  URL.revokeObjectURL(url);
}

function showAnalysis(code) {
  document.querySelector('[data-tab="analysis"]').click();
  document.getElementById('ana-stock').value = code;
  loadAnalysis();
}

// ── 智能筛选 JS ─────────────────────────────────────────────────────
let lastScreeningResults = [];

async function runScreening() {
  const params = {
    market: document.getElementById('s-market').value,
    ma50_above: document.getElementById('s-ma50').checked,
    ma150_above: document.getElementById('s-ma150').checked,
    ma200_above: document.getElementById('s-ma200').checked,
    min_vol_ratio: parseFloat(document.getElementById('s-vol-ratio').value) || 1.0,
    min_vcp_score: parseFloat(document.getElementById('s-vcp').value) || 0,
    rsi_max: parseFloat(document.getElementById('s-rsi-max').value) || null,
    rsi_min: parseFloat(document.getElementById('s-rsi-min').value) || null,
    min_rev_yoy: parseFloat(document.getElementById('s-rev-yoy').value) || 0,
    min_profit_yoy: parseFloat(document.getElementById('s-profit-yoy').value) || 0,
    min_roe: parseFloat(document.getElementById('s-roe').value) || 0,
    min_cagr: parseFloat(document.getElementById('s-cagr').value) || 0,
    north_dir: document.getElementById('s-north-dir').value,
    south_dir: document.getElementById('s-south-dir').value,
    vix_max: parseFloat(document.getElementById('s-vix-max').value) || null,
    vix_calm: document.getElementById('s-vix-calm').checked,
  };
  const r = await fetch('/api/screening', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(params)
  });
  const d = await r.json();
  lastScreeningResults = d.results || [];
  const f = d.funnel || {};
  const funnelEl = document.getElementById('s-funnel');
  funnelEl.style.display = 'flex';
  funnelEl.innerHTML = '<span class="funnel-step' + (f.total ? ' active' : '') + '">总计 ' + (f.total||0) + ' 只</span>' +
    '<span class="funnel-step">MA50通过 ' + (f.ma50||0) + ' 只</span>' +
    '<span class="funnel-step">MA150通过 ' + (f.ma150||0) + ' 只</span>' +
    '<span class="funnel-step">VCP通过 ' + (f.vcp||0) + ' 只</span>' +
    '<span class="funnel-step active">最终 ' + (f.final||0) + ' 只</span>';
  const cols = [
    {key:'code', label:'代码', w:'90px'},
    {key:'name', label:'名称', w:'100px'},
    {key:'market', label:'市场', w:'60px'},
    {key:'vcp_score', label:'VCP评分', w:'70px', num:true},
    {key:'volume_ratio', label:'量比', w:'60px', num:true},
    {key:'rsi14', label:'RSI', w:'60px', num:true},
    {key:'ma50_ok', label:'MA50', w:'60px', bool:true},
    {key:'ma150_ok', label:'MA150', w:'60px', bool:true},
    {key:'rev_yoy', label:'营收YoY', w:'75px', num:true, pct:true},
    {key:'profit_yoy', label:'净利YoY', w:'75px', num:true, pct:true},
    {key:'roe', label:'ROE', w:'60px', num:true, pct:true},
    {key:'north_dir', label:'北向', w:'70px'},
    {key:'south_dir', label:'南向', w:'70px'},
    {key:'vix_level', label:'VIX', w:'60px'},
  ];
  let th = '<tr>' + cols.map(c => '<th style="width:80px" data-key="'+c.key+'" onclick="sortTable(this)">'+c.label+'</th>').join('') + '</tr>';
  document.getElementById('s-table-head').innerHTML = th;
  document.getElementById('s-result-count').textContent = '共 ' + lastScreeningResults.length + ' 只符合条件';
  renderTableBody(lastScreeningResults, cols);
  document.getElementById('s-results').style.display = 'block';
  document.getElementById('export-csv-btn').style.display = lastScreeningResults.length > 0 ? 'inline-block' : 'none';
}

let _sortKey = 'vcp_score', _sortAsc = false;
function sortTable(th) {
  var key = th ? th.getAttribute('data-key') : _sortKey;
  if (key === _sortKey) { _sortAsc = !_sortAsc; } else { _sortKey = key; _sortAsc = false; }
  var cols = getCols();
  lastScreeningResults.sort(function(a, b) {
    var va = a[_sortKey], vb = b[_sortKey];
    if (va == null) return 1;
    if (vb == null) return -1;
    if (typeof va === 'string') return _sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
    return _sortAsc ? va - vb : vb - va;
  });
  renderTableBody(lastScreeningResults, cols);
}

function getCols() {
  return [
    {key:'code', label:'代码', w:'90px'},
    {key:'name', label:'名称', w:'100px'},
    {key:'market', label:'市场', w:'60px'},
    {key:'vcp_score', label:'VCP评分', w:'70px', num:true},
    {key:'volume_ratio', label:'量比', w:'60px', num:true},
    {key:'rsi14', label:'RSI', w:'60px', num:true},
    {key:'ma50_ok', label:'MA50', w:'60px', bool:true},
    {key:'ma150_ok', label:'MA150', w:'60px', bool:true},
    {key:'rev_yoy', label:'营收YoY', w:'75px', num:true, pct:true},
    {key:'profit_yoy', label:'净利YoY', w:'75px', num:true, pct:true},
    {key:'roe', label:'ROE', w:'60px', num:true, pct:true},
    {key:'north_dir', label:'北向', w:'70px'},
    {key:'south_dir', label:'南向', w:'70px'},
    {key:'vix_level', label:'VIX', w:'60px'},
  ];
}

function renderTableBody(rows, cols) {
  let h = '';
  for (const r of rows) {
    h += '<tr data-code="'+r.code+'" onclick="showAnalysis(this.dataset.code)">';
    for (const c of cols) {
      let v = r[c.key];
      if (c.bool) v = v ? '<span style="color:var(--accent)">✅</span>' : '<span style="color:var(--red)">❌</span>';
      else if (c.pct) v = v != null ? v.toFixed(1)+'%' : '—';
      else if (c.num) v = v != null ? (typeof v === 'number' ? v.toFixed(2) : v) : '—';
      else if (v == null) v = '—';
      h += '<td class="'+(c.num?'num':'')+'">'+v+'</td>';
    }
    h += '</tr>';
  }
  document.getElementById('s-table-body').innerHTML = h || '<tr><td colspan="'+cols.length+'" style="text-align:center;color:var(--text-dim);padding:20px">无符合条件股票</td></tr>';
}

function resetScreening() {
  document.getElementById('s-market').value='all';
  document.getElementById('s-ma50').checked=false;
  document.getElementById('s-ma150').checked=false;
  document.getElementById('s-ma200').checked=false;
  document.getElementById('s-vol-ratio').value='0.5';
  document.getElementById('s-vcp').value='30';
  document.getElementById('s-rsi-max').value='70';
  document.getElementById('s-rsi-min').value='';
  document.getElementById('s-rev-yoy').value='25';
  document.getElementById('s-profit-yoy').value='30';
  document.getElementById('s-roe').value='10';
  document.getElementById('s-cagr').value='20';
  document.getElementById('s-north-dir').value='all';
  document.getElementById('s-south-dir').value='all';
  document.getElementById('s-vix-max').value='';
  document.getElementById('s-vix-calm').checked=false;
  document.getElementById('s-funnel').style.display='none';
  document.getElementById('s-results').style.display='none';
  lastScreeningResults = [];
}

function exportScreeningCSV() {
  if (!lastScreeningResults.length) return;
  const cols = getCols();
  const headers = cols.map(c => c.label);
  const rows = lastScreeningResults.map(r => cols.map(c => {
    let v = r[c.key];
    if (v == null) return '';
    if (c.bool) v = v ? '是' : '否';
    return String(v);
  }));
  const csv = [headers, ...rows].map(r => r.join(',')).join('\\n');
  const blob = new Blob([csv], {type:'text/csv'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href=url; a.download='screening_'+new Date().toISOString().slice(0,10)+'.csv'; a.click();
  URL.revokeObjectURL(url);
}

// ── 个股分析 JS ─────────────────────────────────────────────────────
async function loadAnalysis() {
  const code = document.getElementById('ana-stock').value.trim();
  if (!code) return;
  const r = await fetch('/api/sepa/' + encodeURIComponent(code));
  if (!r.ok) { document.getElementById('ana-result').innerHTML = '<div style="padding:20px;color:var(--red)">股票不存在或数据加载失败</div>'; return; }
  const d = await r.json();
  const s = d.stage || '—';
  const stageColor = s.includes('第二阶段')||s.includes('上升') ? 'var(--accent)' : s.includes('第四阶段')||s.includes('下降') ? 'var(--red)' : '#ffbd2e';
  document.getElementById('ana-result').innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
      <div class="card"><div class="card-title">基本信息</div>
        <div class="card-row"><span>代码</span><span>${d.code||'—'}</span></div>
        <div class="card-row"><span>名称</span><span>${d.name||'—'}</span></div>
        <div class="card-row"><span>趋势阶段</span><span style="color:${stageColor};font-weight:700">${s}</span></div>
        <div class="card-row"><span>收盘价</span><span>${d.close||'—'}</span></div>
        <div class="card-row"><span>MA20</span><span>${d.ma20||'—'}</span></div>
        <div class="card-row"><span>MA50</span><span>${d.ma50||'—'}</span></div>
        <div class="card-row"><span>MA150</span><span>${d.ma150||'—'}</span></div>
        <div class="card-row"><span>MA200</span><span>${d.ma200||'—'}</span></div>
      </div>
      <div class="card"><div class="card-title">技术指标</div>
        <div class="card-row"><span>RSI(14)</span><span>${d.rsi||'—'}</span></div>
        <div class="card-row"><span>MACD</span><span>${d.macd||'—'}</span></div>
        <div class="card-row"><span>KDJ</span><span>${d.kdj||'—'}</span></div>
        <div class="card-row"><span>CCI</span><span>${d.cci||'—'}</span></div>
        <div class="card-row"><span>布林上轨</span><span>${d.bb_upper||'—'}</span></div>
        <div class="card-row"><span>布林下轨</span><span>${d.bb_lower||'—'}</span></div>
        <div class="card-row"><span>ATR</span><span>${d.atr||'—'}</span></div>
        <div class="card-row"><span>VCP评分</span><span>${d.vcp_score||'—'}</span></div>
      </div>
    </div>`;
}

loadDashboard();
window.addEventListener('load', () => {
  setTimeout(() => { refreshTiming(); }, 2000);
});
</script>

</body>
</html>"""


# ═══════════════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    print("=" * 60)
    print("🐾 哮天每日收盘报告 v4.0")
    print("=" * 60)
    print("启动服务: http://localhost:7878")
    print("按 Ctrl+C 停止服务")
    print("=" * 60)


uvicorn.run(app, host="0.0.0.0", port=7878, log_level="warning")
