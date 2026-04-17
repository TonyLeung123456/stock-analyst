#!/usr/bin/env python3
"""
哮天每日收盘报告工具 (FastAPI Web UI)
=====================================
马克·米勒维尼趋势策略 · 选股扫描 + SEPA分析 + 择时监测 + 每日报告
所有数据基于本地 K 线 CSV 文件

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
    "hk_kline_dir":  "/Users/tonyleung/Downloads/股票/港股/Kline",
    "north_dir":     "/Users/tonyleung/.openclaw/Downloads/股票/北向资金",
    "south_dir":     "/Users/tonyleung/.openclaw/Downloads/股票/南向资金",
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
    except Exception:
        return None


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
    except Exception:
        return None


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
        rt = fetch_tencent_realtime(["sh000300", "hkHSI", "usNDX", "sh000001"])
        result["realtime"] = rt
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
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

app = FastAPI(title="哮天每日收盘报告", version="4.0")


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_CONTENT


@app.get("/api/dashboard")
async def get_dashboard():
    try:
        return JSONResponse(get_dashboard_data())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/scan")
async def api_scan(market: str = "all", signal: str = "全部"):
    try:
        results = scan_market(market, signal)
        return JSONResponse({"total": len(results), "results": results})
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

.tabs {
  display: flex;
  gap: 4px;
}

.tab {
  padding: 8px 18px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
  color: var(--text-dim);
  transition: all 0.2s;
  border: none;
  background: transparent;
}

.tab:hover { color: var(--text); background: var(--card-hover); }
.tab.active { color: var(--accent); background: rgba(0,212,170,0.12); }

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
  align-items: flex-end;
  gap: 2px;
  height: 80px;
}

.fund-bar {
  flex: 1;
  border-radius: 2px 2px 0 0;
  min-width: 4px;
  transition: opacity 0.2s;
}

.fund-bar:hover { opacity: 0.8; }

.fund-bar.up { background: var(--green); }
.fund-bar.down { background: var(--red); }

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

/* ── SEPA分析 ─────────────────────────────── */
.sepa-input {
  background: var(--card);
  border-radius: 10px;
  padding: 18px;
  border: 1px solid var(--border);
  margin-bottom: 20px;
  display: flex;
  gap: 14px;
  align-items: center;
}

.sepa-input input {
  background: var(--bg);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px 16px;
  font-size: 15px;
  width: 240px;
  outline: none;
}

.sepa-input input:focus { border-color: var(--accent); }

.sepa-result {
  background: var(--card);
  border-radius: 10px;
  padding: 24px;
  border: 1px solid var(--border);
}

.sepa-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}

.sepa-header .stock-name {
  font-size: 28px;
  font-weight: 700;
  color: var(--text);
}

.sepa-header .stock-meta {
  font-size: 13px;
  color: var(--text-dim);
  margin-top: 4px;
}

.sepa-stage {
  padding: 8px 20px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 700;
}

.sepa-stage.stage2 { background: rgba(0,212,170,0.2); color: var(--green); }
.sepa-stage.stage4 { background: rgba(255,68,68,0.2); color: var(--red); }
.sepa-stage.stage1 { background: rgba(255,170,0,0.2); color: var(--yellow); }
.sepa-stage.stage3 { background: rgba(255,170,0,0.2); color: var(--yellow); }
.sepa-stage.unknown { background: var(--card-hover); color: var(--text-dim); }

.sepa-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 14px;
}

.sepa-item {
  background: var(--bg);
  border-radius: 8px;
  padding: 14px;
  border: 1px solid var(--border);
}

.sepa-item .label { font-size: 11px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
.sepa-item .value { font-size: 20px; font-weight: 700; color: var(--text); }
.sepa-item .sub { font-size: 11px; color: var(--text-dim); margin-top: 2px; }

.sepa-indicators { margin-top: 20px; }

.sepa-ind-row {
  display: flex;
  justify-content: space-between;
  padding: 10px 0;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}

.sepa-ind-row:last-child { border-bottom: none; }
.sepa-ind-row .ind-name { color: var(--text-dim); }
.sepa-ind-row .ind-val { color: var(--text); font-weight: 600; font-family: monospace; }

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

/* ── mini chart (纯CSS) ─────────────────── */
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
</style>
</head>
<body>

<header>
  <div class="logo">🐾 哮天 <span>每日收盘报告</span></div>
  <div class="tabs">
    <button class="tab active" data-tab="dashboard">Dashboard</button>
    <button class="tab" data-tab="scan">选股扫描</button>
    <button class="tab" data-tab="sepa">SEPA分析</button>
    <button class="tab" data-tab="timing">择时监测</button>
    <button class="tab" data-tab="report">每日报告</button>
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

  <!-- 选股扫描 Tab -->
  <div id="tab-scan" class="tab-content">
    <div class="scan-controls">
      <label style="font-size:13px;color:var(--text-dim)">市场：</label>
      <select id="scan-market">
        <option value="all">全部</option>
        <option value="hk">港股</option>
        <option value="a">A股</option>
      </select>
      <label style="font-size:13px;color:var(--text-dim)">信号：</label>
      <select id="scan-signal">
        <option value="全部">全部</option>
        <option value="RSI超卖">RSI超卖</option>
        <option value="MACD金叉">MACD金叉</option>
        <option value="CCI超卖">CCI超卖</option>
        <option value="KDJ金叉">KDJ金叉</option>
        <option value="布林下轨">布林下轨</option>
        <option value="趋势第二阶段">趋势第二阶段</option>
      </select>
      <button class="btn btn-primary" onclick="runScan()">执行扫描</button>
      <button class="btn btn-secondary" onclick="exportScan()">导出CSV</button>
    </div>
    <div class="scan-stats" id="scan-stats"></div>
    <div class="scan-results" id="scan-results">
      <div class="loading">点击"执行扫描"开始选股</div>
    </div>
  </div>

  <!-- SEPA分析 Tab -->
  <div id="tab-sepa" class="tab-content">
    <div class="sepa-input">
      <label style="font-size:13px;color:var(--text-dim)">股票代码：</label>
      <input type="text" id="sepa-code" placeholder="如：0700.HK / 600519.SS" onkeydown="if(event.key==='Enter')runSepa()">
      <button class="btn btn-primary" onclick="runSepa()">分析</button>
    </div>
    <div id="sepa-result">
      <div class="error-msg" style="display:none" id="sepa-error"></div>
    </div>
  </div>

  <!-- 择时监测 Tab -->
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
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
  });
});

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
    { label: 'A50期货', code: 'SHOO', ma_data: {} },
  ];

  let html = '';
  for (const c of cards) {
    const info = rt[c.code] || {};
    const price = info.price || '—';
    const chg = info.chg_pct;
    const ma_sig = c.ma_data.signal || '';
    const ma_above = c.ma_data.above;
    const ma_dot_cls = ma_above === true ? 'up' : ma_above === false ? 'down' : '';
    const ma_txt = ma_sig || '暂无数据';

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
    const h = Math.max(2, Math.abs(val) / maxAbs * 80);
    const cls = val >= 0 ? 'up' : 'down';
    return `<div class="fund-bar ${cls}" style="height:${h}px" title="${r.date}: ${val.toFixed(2)}"></div>`;
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
loadDashboard();

window.addEventListener('load', () => {
  // 启动后自动刷新一次择时
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
