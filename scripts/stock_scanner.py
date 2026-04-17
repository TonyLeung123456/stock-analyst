#!/usr/bin/env python3
"""
港股/A股 每日选股扫描
=========================
功能：扫描 K 线数据，找出符合技术指标买入条件的股票
数据：kline/*.csv（从 Yahoo Finance 增量更新）
输出：CSV + JSON 结果文件

用法：
    python3 scripts/stock_scanner.py                          # 扫港股（只看今天）
    python3 scripts/stock_scanner.py --lookback 5             # 扫近5个交易日
    python3 scripts/stock_scanner.py --market hk             # 港股
    python3 scripts/stock_scanner.py --market a              # A股
    python3 scripts/stock_scanner.py --market all           # 港股 + A股
    python3 scripts/stock_scanner.py --run-all              # 运行全部11个指标
    python3 scripts/stock_scanner.py --lookback 3 --run-all # 近3天全部指标
    python3 scripts/stock_scanner.py --combo 'cci<-220,kdj<-10' --profit 10  # 组合AND信号
    python3 scripts/stock_scanner.py --list                  # 列出所有指标
"""

import os
import sys
import json
import csv
import glob
import argparse
import ssl
import urllib.request
from datetime import datetime, date
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Tuple

# ============================================================
# 指标计算模块（内嵌，不依赖 backtester）
# ============================================================

def _read_klines(csv_path: str) -> List[Dict]:
    """读取单只股票 K 线数据，返回 [{date, open, high, low, close, volume}]"""
    rows = []
    for enc in ('utf-8', 'gbk', 'gb2312'):
        try:
            with open(csv_path, encoding=enc, errors='ignore') as f:
                for row in csv.DictReader(f):
                    try:
                        rows.append({
                            'date':   row['date'],
                            'open':   float(row['open']),
                            'high':   float(row['high']),
                            'low':    float(row['low']),
                            'close':  float(row['close']),
                            'volume': int(row['volume']) if row.get('volume') else 0,
                        })
                    except (KeyError, ValueError):
                        continue
            break
        except UnicodeDecodeError:
            continue
    rows.sort(key=lambda x: x['date'])
    return rows


def _sma(values: list, n: int) -> Optional[float]:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def _std(values: list, n: int) -> Optional[float]:
    if len(values) < n:
        return None
    avg = sum(values[-n:]) / n
    variance = sum((v - avg) ** 2 for v in values[-n:]) / n
    return variance ** 0.5


# -------- 移动平均线 --------
def calc_sma(prices: List[float], period: int) -> List[Optional[float]]:
    result = []
    for i in range(len(prices)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(sum(prices[i - period + 1:i + 1]) / period)
    return result


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


# -------- RSI --------
def calc_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# -------- MACD --------
def calc_macd(prices: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
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


# -------- CCI --------
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


# -------- 布林带 --------
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


# -------- KDJ --------
def calc_kdj(highs: List[float], lows: List[float], closes: List[float], n: int = 9, m1: int = 3, m2: int = 3) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
    k_values, d_values, j_values = [], [], []
    for i in range(len(closes)):
        if i < n - 1:
            k_values.append(50.0)
            d_values.append(50.0)
            j_values.append(50.0)
            continue
        low_n = min(lows[i - n + 1:i + 1])
        high_n = max(highs[i - n + 1:i + 1])
        rsv = (closes[i] - low_n) / (high_n - low_n) * 100 if high_n != low_n else 50
        k = 2 / 3 * k_values[-1] + 1 / 3 * rsv
        d = 2 / 3 * d_values[-1] + 1 / 3 * k
        j = 3 * k - 2 * d
        k_values.append(k)
        d_values.append(d)
        j_values.append(j)
    return k_values, d_values, j_values


# -------- ATR --------
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


# -------- VIX（^VIX 日线，最后一个收盘价）--------
VIX_CACHE = {}


def fetch_vix(days: int = 30) -> Optional[float]:
    """从 Yahoo Finance 获取 US VIX 收盘价（使用缓存）"""
    global VIX_CACHE
    today_str = str(date.today())
    if today_str in VIX_CACHE:
        return VIX_CACHE[today_str]
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX"
               f"?interval=1d&range={days}d")
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            data = json.loads(resp.read())
        quotes = data['chart']['result'][0]['indicators']['quote'][0]
        closes = [c for c in quotes.get('close', []) if c is not None]
        if closes:
            VIX_CACHE[today_str] = closes[-1]
            return closes[-1]
    except Exception:
        pass
    return None


# ============================================================
# 股票列表读取
# ============================================================

DEFAULT_HK_LIST = '/Users/tonyleung/.openclaw/Downloads/股票/港股/list.txt'
DEFAULT_A_LIST  = '/Users/tonyleung/.openclaw/Downloads/股票/A股/list.txt'


def read_stock_list(path: str) -> set:
    """
    从文件读取股票代码集合（跳过空行和 # 注释）

    支持格式：
      00001,长和
      00700，腾讯控股
      0700.HK
      0700

    返回标准化的代码集合（无后缀，如 {'0001', '0700', ...}）
    """
    if not os.path.exists(path):
        return set()
    codes = set()
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            # 取第一列（代码部分）
            code = line.split('，')[0].split(',')[0].strip()
            # 统一转数字，忽略全角
            code = code.translate(str.maketrans('０１２３４５６７８９', '0123456789'))
            code = code.replace('.HK', '').replace('.hk', '').replace('.Hk', '')
            code = code.replace('.SS', '').replace('.SZ', '').replace('.ss', '').replace('.sz', '')
            if code.isdigit():
                # 港股5位码去前导零
                if len(code) == 5:
                    code = code[1:]
                codes.add(code)
    return codes

@dataclass
class ScanSignal:
    symbol:        str
    strategy:      str        # 策略名（英文简称）
    strategy_zh:   str        # 策略名（中文）
    signal:        str         # BUY / SELL
    value:         float       # 当前指标值
    threshold:     str         # 触发条件描述
    price:         float       # 信号触发日价格（历史信号日收盘价）
    latest_price:  float       # 最新收盘价（K线最后一天）
    price_chg_pct: float       # 信号日到最新价的涨跌幅（%）
    market:        str         # hk / a
    scan_date:     str         # 信号触发日（K线日期）
    value2:        Optional[float] = None  # 辅助值（如J值、止盈价等）
    note:          str = ""
    name:          str = ""    # 股票名称（港股/A股）


# ============================================================
# 指标时序扫描（核心：支持回溯 N 个交易日）
# ============================================================

def _values_at(klines: List[Dict], idx: int) -> Dict:
    """
    返回截至 klines[idx] 当日为止的各指标数值（供各策略检测函数使用）。
    返回 dict，key 为指标名，value 可能为 float 或 None。
    """
    closes = [k['close'] for k in klines[:idx+1]]
    highs  = [k['high']  for k in klines[:idx+1]]
    lows   = [k['low']   for k in klines[:idx+1]]
    vols   = [k['volume'] for k in klines[:idx+1]]

    rsi_val   = calc_rsi(closes, 14) if len(closes) >= 15 else None
    cci_val   = calc_cci(highs, lows, closes, 20) if len(closes) >= 20 else None

    macd_l, sig_l, hist = calc_macd(closes)
    macd_v  = macd_l[-1] if macd_l else None
    sig_v   = sig_l[-1]  if sig_l  else None
    hist_v  = hist[-1]   if hist   else None

    ma5_v   = calc_sma(closes, 5)[-1]  if len(closes) >= 5  else None
    ma20_v  = calc_sma(closes, 20)[-1] if len(closes) >= 20 else None
    ema12_v = calc_ema(closes, 12)[-1] if len(closes) >= 12 else None
    ema26_v = calc_ema(closes, 26)[-1] if len(closes) >= 26 else None

    upper_v, mid_v, lower_v = calc_bollinger(closes, 20, 2.0)
    lower_v = lower_v if lower_v else None

    if len(highs) >= 9:
        k_v, d_v, j_v = calc_kdj(highs, lows, closes, 9, 3, 3)
        k_v, d_v, j_v = k_v[-1], d_v[-1], j_v[-1]
    else:
        k_v = d_v = j_v = None

    ma_vol_v  = _sma(vols, 20) if len(vols) >= 20 else None
    ma_close_v = calc_sma(closes, 5)[-1] if len(closes) >= 5 else None

    atr_v    = calc_atr(highs, lows, closes, 14) if len(closes) >= 15 else None

    # 上一个 bar 的值（用于金叉/反转检测）
    prev_macd_above = (macd_l[-2] > sig_l[-2]) if (len(macd_l) >= 2 and macd_l[-2] is not None and sig_l[-2] is not None) else None
    prev_ema_above  = (calc_ema(closes, 12)[-2] > calc_ema(closes, 26)[-2]) if (len(closes) >= 27) else None
    prev_hist_neg   = (hist[-2] < 0) if (len(hist) >= 2 and hist[-2] is not None) else None

    return {
        'rsi':        rsi_val,
        'cci':        cci_val,
        'macd':       macd_v,
        'signal':     sig_v,
        'histogram':  hist_v,
        'ma5':        ma5_v,
        'ma20':       ma20_v,
        'ema12':      ema12_v,
        'ema26':      ema26_v,
        'bollinger_lower': lower_v,
        'k':          k_v,
        'd':          d_v,
        'j':          j_v,
        'ma_vol':     ma_vol_v,
        'ma_close':   ma_close_v,
        'atr':        atr_v,
        'prev_macd_above': prev_macd_above,
        'prev_ema_above':  prev_ema_above,
        'prev_hist_neg':   prev_hist_neg,
        'price':      closes[-1],
        'high':       highs[-1],
        'volume':     vols[-1],
    }


# 单个指标在指定 idx 是否触发 BUY 信号，返回 (bool, ScanSignal or None)
def _signal_at(strategy: str, idx: int, klines: List[Dict], vix_val: Optional[float],
               scan_date: str, lookback: int) -> Optional[ScanSignal]:
    """检测指定策略在 klines[idx] 是否触发 BUY 信号，返回 ScanSignal 或 None"""
    vals = _values_at(klines, idx)
    p = klines[idx]['close']
    m = klines[idx].get('market', 'hk')
    sym = klines[idx].get('symbol', '')
    recent_highs = max(k['high'] for k in klines[max(0, idx-19):idx+1]) if idx >= 19 else max(k['high'] for k in klines[:idx+1])

    latest = klines[-1]['close']
    chg_pct = ((latest - p) / p * 100) if p != 0 else 0.0
    if strategy == 'rsi':
        v = vals['rsi']
        if v is not None and v < 20:
            return ScanSignal(sym, 'rsi', 'RSI超买超卖', 'BUY', round(v, 2),
                              f'RSI={v:.1f}（<20超卖）', p, latest, round(chg_pct, 2), m, scan_date,
                              note=f'RSI={v:.1f}，严重超卖' if v < 20 else f'RSI={v:.1f}，超卖')
        return None

    if strategy == 'cci':
        v = vals['cci']
        if v is not None and v < -200:
            return ScanSignal(sym, 'cci', 'CCI顺势指标', 'BUY', round(v, 2),
                              f'CCI={v:.1f}（<-200超卖）', p, latest, round(chg_pct, 2), m, scan_date,
                              note=f'CCI={v:.1f}，强势超卖')
        return None

    if strategy == 'bollinger':
        lower = vals['bollinger_lower']
        if lower is not None and p <= lower:
            pct = ((lower - p) / lower) * 100 if lower > 0 else 0
            if pct >= 2.0:  # 偏离下轨必须超过2%才算有效信号
                return ScanSignal(sym, 'bollinger', '布林带支撑压力', 'BUY', round(lower, 2),
                                  f'下轨={lower:.2f}', p, latest, round(chg_pct, 2), m, scan_date,
                                  note=f'价格{p:.2f}≤下轨{lower:.2f}，偏离{pct:.1f}%')
        return None

    if strategy == 'ma_cross':
        ma5, ma20 = vals['ma5'], vals['ma20']
        if ma5 is not None and ma20 is not None:
            # 需要前一天的 ma5/ma20
            if idx < 1:
                return None
            prev_vals = _values_at(klines, idx - 1)
            ma5_prev, ma20_prev = prev_vals['ma5'], prev_vals['ma20']
            if ma5_prev is not None and ma20_prev is not None:
                if ma5_prev <= ma20_prev and ma5 > ma20:
                    diff = ma5 - ma20
                    pct = diff / ma20 * 100 if ma20 != 0 else 0
                    return ScanSignal(sym, 'ma_cross', 'MA金叉死叉', 'BUY', round(diff, 3),
                                      f'MA5({ma5:.2f})上穿MA20({ma20:.2f})', p, latest, round(chg_pct, 2), m, scan_date,
                                      note=f'金叉开口{pct:.2f}%')
        return None

    if strategy == 'macd_cross':
        macd, sig = vals['macd'], vals['signal']
        if macd is not None and sig is not None and vals['prev_macd_above'] is not None:
            curr_above = macd > sig
            if not vals['prev_macd_above'] and curr_above:
                return ScanSignal(sym, 'macd_cross', 'MACD金叉死叉', 'BUY', round(macd - sig, 3),
                                  f'MACD线由负转正', p, latest, round(chg_pct, 2), m, scan_date,
                                  note=f'MACD={macd:.3f}>Signal={sig:.3f}')
        return None

    if strategy == 'kdj':
        k, j = vals['k'], vals['j']
        if k is not None and j is not None and idx >= 1:
            prev_vals = _values_at(klines, idx - 1)
            k_prev, j_prev = prev_vals['k'], prev_vals['j']
            if k_prev is not None and j_prev is not None and k < 20 and j < 0:
                if j_prev <= k_prev and j > k:
                    return ScanSignal(sym, 'kdj', 'KDJ金叉死叉', 'BUY', round(k, 2),
                                      f'K<20金叉，K={k:.1f} J={j:.1f}', p, latest, round(chg_pct, 2), m, scan_date,
                                      note=f'KDJ超卖金叉，K={k:.1f} D={vals["d"]:.1f} J={j:.1f}')
        return None

    if strategy == 'macd_hist':
        hist = vals['histogram']
        if hist is not None and vals['prev_hist_neg'] is not None:
            if vals['prev_hist_neg'] and hist >= 0:
                return ScanSignal(sym, 'macd_hist', 'MACD柱状图反转', 'BUY', round(hist, 4),
                                  '柱状图由负转正', p, latest, round(chg_pct, 2), m, scan_date,
                                  note=f'MACD柱={hist:.4f}')
        return None

    if strategy == 'ema_cross':
        e12, e26 = vals['ema12'], vals['ema26']
        if e12 is not None and e26 is not None and idx >= 1:
            prev_vals = _values_at(klines, idx - 1)
            e12_prev, e26_prev = prev_vals['ema12'], prev_vals['ema26']
            if e12_prev is not None and e26_prev is not None:
                if e12_prev <= e26_prev and e12 > e26:
                    diff = e12 - e26
                    return ScanSignal(sym, 'ema_cross', '双EMA交叉', 'BUY', round(diff, 3),
                                      f'EMA12({e12:.2f})上穿EMA26({e26:.2f})', p, latest, round(chg_pct, 2), m, scan_date,
                                      note=f'DIFF={diff:.3f}')
        return None

    if strategy == 'vol_breakout':
        ma_vol = vals['ma_vol']
        ma_close = vals['ma_close']
        if ma_vol is not None and ma_close is not None:
            vol_ratio = vals['volume'] / ma_vol if ma_vol > 0 else 0
            if vol_ratio > 2.0 and vals['price'] > ma_close:
                return ScanSignal(sym, 'vol_breakout', '量价突破', 'BUY', round(vol_ratio, 2),
                                  f'量>{vol_ratio:.1f}x均量且价格>MA5', p, latest, round(chg_pct, 2), m, scan_date,
                                  note=f'放量{vol_ratio:.1f}倍，突破MA5({ma_close:.2f})')
        return None

    if strategy == 'atr_stop':
        atr = vals['atr']
        if atr is not None and vals['price'] >= recent_highs and idx >= 1:
            if vals['price'] > klines[idx-1]['close']:
                return ScanSignal(sym, 'atr_stop', 'ATR跟踪止损', 'BUY', round(vals['price'], 2),
                                  f'创{min(20, idx+1)}日新高{vals["price"]:.2f}', p, latest, round(chg_pct, 2), m, scan_date,
                                  note=f'突破ATR={atr:.3f}，近期高点={recent_highs:.2f}')
        return None

    if strategy == 'vix':
        if vix_val is not None and vix_val > 30:
            return ScanSignal('^VIX', 'vix', 'VIX情绪指数', 'BUY', round(vix_val, 2),
                              f'VIX>30（市场恐慌）', vix_val, vix_val, 0.0, m, scan_date,
                              note=f'VIX={vix_val:.1f}，恐慌情绪，可逆向买入')
        return None

    return None


def _scan_stock(klines: List[Dict], strategies: List[str], lookback: int,
                vix_val: Optional[float]) -> List[ScanSignal]:
    """
    扫描单只股票，过去 lookback 个交易日内任一天出现信号即记录。
    信号日期为实际 K 线日期。
    """
    if len(klines) < 2:
        return []
    n = len(klines)
    signals = []
    # 从倒数第 lookback 天开始扫，到最后一天为止
    start = max(0, n - lookback)
    for idx in range(start, n):
        scan_date = klines[idx]['date']
        for strat in strategies:
            if strat == 'vix':
                continue  # VIX 由 scan_market 统一处理，不再逐股生成
            s = _signal_at(strat, idx, klines, vix_val, scan_date, lookback)
            if s is not None:
                signals.append(s)
    return signals


# ============================================================
# 策略注册表
# ============================================================

STRATEGY_ZH_NAMES = {
    'ma_cross':    'MA金叉死叉',
    'rsi':         'RSI超买超卖',
    'macd_cross':  'MACD金叉死叉',
    'bollinger':   '布林带支撑压力',
    'kdj':         'KDJ金叉死叉',
    'macd_hist':   'MACD柱状图反转',
    'ema_cross':   '双EMA交叉',
    'vol_breakout': '量价突破',
    'atr_stop':    'ATR跟踪止损',
    'cci':         'CCI顺势指标',
    'vix':         'VIX情绪指数',
}

ALL_STRATEGIES = [
    'ma_cross', 'rsi', 'macd_cross', 'bollinger', 'kdj',
    'macd_hist', 'ema_cross', 'vol_breakout', 'atr_stop', 'cci', 'vix',
]


# ============================================================
# 组合指标检测
# ============================================================

def _check_combo(klines: List[Dict], ind1: str, th1: float, ind2: str, th2: float, profit_pct: float, vix: Optional[float] = None) -> Optional[ScanSignal]:
    """
    组合AND信号：两个指标同时满足条件买入，任一指标反向或达到止盈卖出
    返回 BUY 信号（仅在买入触发时返回）
    """
    price = klines[-1]['close']
    trigger_price = price  # combo 永远在最后一天触发
    latest = trigger_price
    chg_pct = 0.0
    val1 = _get_indicator_value(klines, ind1, vix)
    val2 = _get_indicator_value(klines, ind2, vix)
    if val1 is None or val2 is None:
        return None
    if val1 < th1 and val2 < th2:
        return ScanSignal(
            symbol=klines[0].get('symbol', ''),
            strategy=f'combo_{ind1}<{th1}&{ind2}<{th2}',
            strategy_zh=f'组合[{STRATEGY_ZH_NAMES.get(ind1,ind1)}<{th1}&{STRATEGY_ZH_NAMES.get(ind2,ind2)}<{th2}]',
            signal='BUY',
            value=round(val1, 2),
            value2=round(val2, 2),
            threshold=f'{ind1}={val1:.1f}<{th1} AND {ind2}={val2:.1f}<{th2}',
            price=trigger_price,
            latest_price=latest,
            price_chg_pct=round(chg_pct, 2),
            market=klines[0].get('market', 'hk'),
            scan_date=str(date.today()),
            note=f'组合买入，止盈{profit_pct}%'
        )
    return None


def _get_indicator_value(klines: List[Dict], ind: str, vix: Optional[float]) -> Optional[float]:
    """获取指标当前值（用于组合指标比较）"""
    closes = [k['close'] for k in klines]
    highs = [k['high'] for k in klines]
    lows = [k['low'] for k in klines]
    if ind == 'rsi':
        return calc_rsi(closes)
    elif ind == 'cci':
        return calc_cci(highs, lows, closes)
    elif ind == 'kdj':
        _, _, j_vals = calc_kdj(highs, lows, closes)
        return j_vals[-1] if j_vals else None
    elif ind == 'vix':
        return vix
    elif ind == 'macd_hist':
        _, _, hist = calc_macd(closes)
        return hist[-1] if hist else None
    elif ind == 'bollinger':
        _, _, lower = calc_bollinger(closes)
        return lower  # 价格偏离下轨程度
    return None


# ============================================================
# 扫描器
# ============================================================

def scan_market(kline_dir: str, strategies: list, market: str = 'hk',
                combo_expr: str = None, profit_pct: float = 10.0,
                stock_list: set = None, lookback: int = 1) -> list:
    """扫描指定市场的所有股票

    Args:
        kline_dir: K 线数据目录
        strategies: 策略列表
        market: 市场标识
        combo_expr: 组合指标表达式
        profit_pct: 组合止盈百分比
        stock_list: 如果非空，只扫描该集合中的股票代码
        lookback: 回溯多少个交易日（默认1，只看最后一天）
    """
    csv_files = sorted(glob.glob(os.path.join(kline_dir, '*.csv')))
    total = len(csv_files)

    # 按股票代码过滤（文件名格式：0700.HK / 600519.SS 等）
    if stock_list:
        filtered = []
        for f in csv_files:
            basename = os.path.splitext(os.path.basename(f))[0]  # e.g. "0001.HK"
            # 去掉市场后缀，只比较纯代码部分
            code = basename.replace('.HK', '').replace('.SS', '').replace('.SZ', '').replace('.hk', '')
            if code in stock_list:
                filtered.append(f)
        print(f"  📋 列表过滤：{len(filtered)}/{total} 只股票")
        csv_files = filtered
        total = len(csv_files)

    signals = []

    # ── 加载股票名称映射 ─────────────────────────────────────────────
    stock_names: dict = {}
    if market == 'hk':
        list_path = Path(kline_dir).parent / 'list.txt'
        if list_path.exists():
            with open(list_path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if ',' in line:
                        code, name = line.split(',', 1)
                        # 5位码 00001 → 4位 0001 → 0001.HK
                        code_hk = code.lstrip('0').zfill(4) + '.HK'
                        stock_names[code_hk] = name
            print(f"  📖 已加载 {len(stock_names)} 只港股名称")
    elif market == 'a':
        list_path = Path(kline_dir).parent / 'list.txt'
        if list_path.exists():
            with open(list_path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if ',' in line:
                        code, name = line.split(',', 1)
                        # A股: 6位码 600519 → 600519.SS / 000001 → 000001.SZ
                        if code.startswith(('6', '688')):
                            code_a = code + '.SS'
                        else:
                            code_a = code + '.SZ'
                        stock_names[code_a] = name
            print(f"  📖 已加载 {len(stock_names)} 只A股名称")

    # VIX 只查一次
    vix_value = None
    if 'vix' in strategies or (combo_expr and 'vix' in combo_expr.lower()):
        vix_value = fetch_vix()
        print(f"  📊 当前 VIX = {vix_value:.2f}" if vix_value else "  ⚠️ 无法获取 VIX 数据")

    # 解析组合指标
    combo_ind1 = combo_ind2 = combo_th1 = combo_th2 = None
    if combo_expr:
        parts = combo_expr.split(',')
        if len(parts) == 2:
            try:
                ind1_expr = parts[0]
                ind2_expr = parts[1]
                combo_ind1, combo_th1_str = ind1_expr.split('<')
                combo_ind2, combo_th2_str = ind2_expr.split('<')
                combo_th1 = float(combo_th1_str)
                combo_th2 = float(combo_th2_str)
                print(f"  🔧 组合信号：{combo_ind1}<{combo_th1} AND {combo_ind2}<{combo_th2}，止盈{profit_pct}%")
            except ValueError:
                print(f"  ❌ 组合表达式解析失败：{combo_expr}")
                combo_expr = None

    for i, csv_path in enumerate(csv_files):
        symbol = os.path.splitext(os.path.basename(csv_path))[0]
        klines = _read_klines(csv_path)
        if not klines:
            continue

        for k in klines:
            k['symbol'] = symbol
            k['market'] = market

        # 使用新的时序扫描，支持回溯 lookback 天
        stock_signals = _scan_stock(klines, strategies, lookback, vix_value)
        signals.extend(stock_signals)

        # 进度
        if (i + 1) % 200 == 0:
            print(f"  进度: {i+1}/{total}...", end='\r', flush=True)

    # ── VIX 大盘信号：全市场只记一条 ──────────────────────────────────
    if ('vix' in strategies or (combo_expr and 'vix' in combo_expr.lower())) and vix_value is not None:
        if vix_value > 30:
            # 找最近一个 K 线日期作为信号日期
            last_date = signals[0].scan_date if signals else date.today().isoformat()
            vix_signal = ScanSignal(
                symbol='^VIX',
                strategy='vix',
                strategy_zh='VIX情绪指数',
                signal='BUY',
                value=round(vix_value, 2),
                threshold='VIX>30（市场恐慌）',
                price=round(vix_value, 2),
                market=market,
                scan_date=last_date,
                note=f'VIX={vix_value:.1f}，恐慌情绪，可逆向买入'
            )
            signals.append(vix_signal)

    return signals


def save_results(signals: list, market: str, strategies: list, combo_expr: str = None):
    """保存结果到 CSV 和 JSON"""
    today = date.today().isoformat()
    # 保存到 Downloads/股票/scanner/
    scanner_dir = Path("/Users/tonyleung/Downloads/股票/scanner")
    scanner_dir.mkdir(parents=True, exist_ok=True)
    tag = combo_expr.replace('<', '_lt_').replace(',', '_AND_') if combo_expr else '_'.join(strategies)
    tag = tag[:80]

    # JSON
    json_path = scanner_dir / f"scan_results_{market}_{today}.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        data = [asdict(s) for s in signals]
        json.dump({
            'scan_date': today,
            'market': market,
            'strategies': strategies,
            'combo': combo_expr,
            'total_signals': len(signals),
            'signals': data
        }, f, ensure_ascii=False, indent=2)

    # 加载股票名称映射（与 scan_market 保持一致）
    name_map = {}
    if market == 'hk':
        list_path = DEFAULT_HK_LIST
    elif market == 'a':
        list_path = DEFAULT_A_LIST
    if list_path and os.path.exists(list_path):
        with open(list_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or ',' not in line:
                    continue
                code, name = line.split(',', 1)
                code = code.strip()
                name = name.strip()
                if market == 'hk':
                    # 5位码 00001 → 4位 0001 → 0001.HK
                    code_hk = code.lstrip('0').zfill(4) + '.HK'
                    name_map[code_hk] = name
                elif market == 'a':
                    # A股: 6位码，600/688开头 → .SS，其他 → .SZ
                    if code.startswith(('6', '688')):
                        code_a = code + '.SS'
                    else:
                        code_a = code + '.SZ'
                    name_map[code_a] = name

    # CSV
    csv_path = scanner_dir / f"scan_results_{market}_{today}.csv"
    if signals:
        with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            cols = ['symbol', 'stock_name', 'strategy', 'strategy_zh', 'signal',
                    'value', 'value2', 'threshold',
                    'price',           # 信号触发日价格
                    'latest_price',    # 最新收盘价
                    'price_chg_pct',   # 信号日至今涨跌幅（%）
                    'market', 'scan_date', 'note']
            writer = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
            writer.writeheader()
            for s in signals:
                row = asdict(s)
                sym_code = row['symbol']
                row['stock_name'] = name_map.get(sym_code, '')
                writer.writerow(row)

    return str(json_path), str(csv_path)


def summarize(signals: list, market: str):
    """打印汇总"""
    by_strategy = defaultdict(list)
    for s in signals:
        by_strategy[s.strategy_zh].append(s)

    print(f"\n{'='*60}")
    print(f"📋 {market.upper()} 选股扫描结果汇总 — {date.today()}")
    print(f"{'='*60}")

    if not signals:
        print("  今日无信号")
        return

    print(f"  共 {len(signals)} 个信号：\n")
    # 加载股票名称（用于显示）
    name_map = {}
    list_path = DEFAULT_HK_LIST if market == 'hk' else DEFAULT_A_LIST if market == 'a' else None
    if list_path and os.path.exists(list_path):
        with open(list_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or ',' not in line:
                    continue
                code, name = line.split(',', 1)
                code, name = code.strip(), name.strip()
                if market == 'hk':
                    code_hk = code.lstrip('0').zfill(4) + '.HK'
                    name_map[code_hk] = name
                elif market == 'a':
                    if code.startswith(('6', '688')):
                        code_a = code + '.SS'
                    else:
                        code_a = code + '.SZ'
                    name_map[code_a] = name
    for strat_zh, s_list in sorted(by_strategy.items(), key=lambda x: -len(x[1])):
        print(f"  📌 {strat_zh}：{len(s_list)} 个信号")
        for s in s_list[:5]:
            val_str = f"{s.value}"
            if s.value2 is not None:
                val_str += f" / {s.value2}"
            stock_name = name_map.get(s.symbol, '')
            name_suffix = f' {stock_name}' if stock_name else ''
            print(f"     {s.symbol}{name_suffix}  {val_str} | 价={s.price} | {s.note}")
        if len(s_list) > 5:
            print(f"     ... 还有 {len(s_list)-5} 只")
        print()


def print_strategies():
    """列出所有可用指标"""
    print("\n📊 可用选股指标（11个）：")
    print("-" * 60)
    print(f"  {'简称':<14} {'名称':<20} {'买入条件'}")
    print("-" * 60)
    details = [
        ('ma_cross',     'MA金叉死叉',       'MA5上穿MA20'),
        ('rsi',          'RSI超买超卖',       'RSI < 20（严重超卖）'),
        ('macd_cross',   'MACD金叉死叉',     'MACD线由负转正'),
        ('bollinger',    '布林带支撑',        '价格触及布林下轨'),
        ('kdj',          'KDJ金叉死叉',      'K<20超卖区金叉'),
        ('macd_hist',    'MACD柱状图反转',   '柱状图由负转正'),
        ('ema_cross',    '双EMA交叉',        'EMA12上穿EMA26'),
        ('vol_breakout', '量价突破',         '量>2x均量且价格>MA5'),
        ('atr_stop',     'ATR跟踪止损',      '收盘价创N日新高'),
        ('cci',          'CCI顺势指标',      'CCI < -200（强势超卖）'),
        ('vix',          'VIX情绪指数',      'VIX > 30（市场恐慌）'),
    ]
    for key, name, cond in details:
        print(f"  {key:<14} {name:<20} {cond}")
    print()
    print("  组合示例：--combo 'cci<-220,kdj<-10' --profit 10")
    print()


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="每日选股扫描（支持全部11个技术指标）")
    parser.add_argument('--market', choices=['hk', 'a', 'all'], default='hk',
                        help='市场: hk(港股默认) / a(A股) / all(全部)')
    parser.add_argument('--strategies', default='rsi,vix,cci,bollinger',
                        help='策略列表(逗号分隔)，可用值：ma_cross,rsi,macd_cross,bollinger,kdj,macd_hist,ema_cross,vol_breakout,atr_stop,cci,vix')
    parser.add_argument('--run-all', action='store_true',
                        help='运行全部11个指标')
    parser.add_argument('--hk-dir', default='/Users/tonyleung/.openclaw/Downloads/股票/港股/kline',
                        help='港股K线目录')
    parser.add_argument('--a-dir',  default='/Users/tonyleung/.openclaw/Downloads/股票/A股/kline',
                        help='A股K线目录')
    parser.add_argument('--list', action='store_true',
                        help='列出所有可用指标')
    parser.add_argument('--list-file',
                        help='扫描指定股票列表文件（默认：港股用 /Users/tonyleung/.openclaw/Downloads/股票/港股/list.txt，A股用对应的 list.txt）')
    parser.add_argument('--combo', metavar='EXPR',
                        help='组合AND信号，格式: 指标名<阈值,指标名<阈值  例：cci<-220,kdj<-10')
    parser.add_argument('--profit', metavar='PCT', type=float, default=10.0,
                        help='组合止盈百分比（默认10%%）')
    parser.add_argument('--output-dir',
                        default='/Users/tonyleung/.openclaw/Downloads/股票/scanner',
                        help='结果输出目录')
    parser.add_argument('--lookback', metavar='N', type=int, default=1,
                        help='回溯多少个交易日（默认1，只看最后一天；设为3则扫描近3天出现信号的股票）')
    args = parser.parse_args()

    if args.list:
        print_strategies()
        return

    if args.run_all:
        strategies = ALL_STRATEGIES
        print(f"🗡️ 选股扫描启动 | 全部11个指标 | 市场: {args.market}")
    else:
        strategies = [s.strip() for s in args.strategies.split(',')]
        print(f"🗡️ 选股扫描启动 | 市场: {args.market} | 策略: {strategies}")

    print(f"   日期: {date.today()}")
    print(f"   回溯: 近 {args.lookback} 个交易日")

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    # 股票列表：默认用 list.txt，--list-file 覆盖
    hk_list_path = args.list_file if args.list_file else DEFAULT_HK_LIST
    a_list_path  = args.list_file if args.list_file else DEFAULT_A_LIST
    hk_list = read_stock_list(hk_list_path) if args.market in ('hk', 'all') else set()
    a_list  = read_stock_list(a_list_path)  if args.market in ('a',  'all') else set()
    if args.list_file:
        print(f"  📄 股票列表：{args.list_file}（{len(hk_list) + len(a_list)} 只）")

    all_signals = []

    # 港股
    if args.market in ('hk', 'all'):
        print(f"\n📦 扫描港股: {args.hk_dir}")
        sigs = scan_market(args.hk_dir, strategies, 'hk', args.combo, args.profit, hk_list, args.lookback)
        print(f"\n  ✅ 港股扫描完成，得到 {len(sigs)} 个信号")
        all_signals.extend(sigs)

    # A股
    if args.market in ('a', 'all'):
        print(f"\n📦 扫描A股: {args.a_dir}")
        sigs = scan_market(args.a_dir, strategies, 'a', args.combo, args.profit, a_list, args.lookback)
        print(f"\n  ✅ A股扫描完成，得到 {len(sigs)} 个信号")
        all_signals.extend(sigs)

    # 保存
    if all_signals:
        json_path, csv_path = save_results(all_signals, args.market, strategies, args.combo)
        print(f"\n  💾 JSON: {json_path}")
        print(f"  💾 CSV:  {csv_path}")
    else:
        print("\n  今日无信号，未生成文件")

    summarize(all_signals, args.market)


if __name__ == '__main__':
    main()
