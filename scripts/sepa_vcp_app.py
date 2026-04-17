#!/usr/bin/env python3
"""
SEPA × VCP 选股工具  (FastAPI Web UI)
用法: python3 sepa_vcp_app.py
访问: http://localhost:7860
"""

import os, json, warnings, time
from pathlib import Path
from datetime import datetime

warnings.filterwarnings("ignore")
# 清除所有代理变量（确保国内API直连）
for _k in list(os.environ.keys()):
    if _k.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        del os.environ[_k]
os.environ["NO_PROXY"] = "*"

# ──────────────────────────────────────────────
# 股票名称 + 板块获取（腾讯财经 API）
# ──────────────────────────────────────────────
_NAME_CACHE = {}   # code → 中文名
_SECTOR_CACHE = {}  # code → 行业板块
_NAME_MAP_LOADED = False

def _load_sector_map():
    """从本地缓存加载股票→行业映射（东方财富API在批量请求时会被截断，暂用缓存文件）"""
    global _SECTOR_MAP_LOADED
    if _SECTOR_MAP_LOADED:
        return
    _SECTOR_MAP_LOADED = True
    import os
    cache_path = "/tmp/stock_sector_map.json"
    if os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                _SECTOR_CACHE.update(json.load(f))
        except: pass


def _qq_fetch(codes):
    """从腾讯财经批量获取股票信息，返回 {code: (name, sector)}"""
    if not codes:
        return {}
    batch = ','.join(codes)
    url = f'https://qt.gtimg.cn/q={batch}'
    try:
        import requests as _requests
        r = _requests.get(url, timeout=4, headers={
            'Referer': 'https://finance.qq.com/',
            'User-Agent': 'Mozilla/5.0'
        })
        if r.status_code != 200:
            return {}
        text = r.text.strip()
        lines = text.split('\n')
        result = {}
        for line in lines:
            if '=' not in line:
                continue
            key_part = line.split('=')[0].strip().replace('v_', '')
            rest = line.split('=', 1)[1].strip('"')
            fields = rest.split('~')
            if len(fields) < 15:
                continue
            name = fields[1] if fields[1] else key_part
            # 板块：尝试从字段获取（腾讯数据不一定有，尝试几个已知位置）
            sector = fields[47] if len(fields) > 47 and fields[47] else (
                fields[46] if len(fields) > 46 and fields[46] else ''
            )
            result[key_part.upper()] = (name, sector)
        return result
    except Exception:
        return {}

def _load_name_map():
    """从本地 list.txt 加载股票名称和行业（list.txt: 代码,名称[,行业]）"""
    global _NAME_MAP_LOADED
    if _NAME_MAP_LOADED:
        return
    _NAME_MAP_LOADED = True
    try:
        # A股 list.txt: 代码,名称,行业
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
                        _SECTOR_CACHE[c + '.SZ'] = sector
                        _SECTOR_CACHE[c + '.SS'] = sector
        # 港股 list.txt: 代码,名称,行业
        hk_path = '/Users/tonyleung/Downloads/股票/港股/list.txt'
        if os.path.exists(hk_path):
            with open(hk_path, encoding='utf-8') as f:
                for line in f:
                    parts = line.strip().split(',')
                    if len(parts) >= 2:
                        c = parts[0].strip().lstrip('0').zfill(5)  # "00001" → "00001"
                        name = parts[1].strip()
                        sector = parts[2].strip() if len(parts) >= 3 else '—'
                        _NAME_CACHE['HK' + c] = name   # HK00001
                        _NAME_CACHE[c + '.HK'] = name  # 00001.HK
                        _SECTOR_CACHE['HK' + c] = sector
                        _SECTOR_CACHE[c + '.HK'] = sector
    except Exception as e:
        import sys; sys.stderr.write(f'name_map load error: {e}\n')


def enrich_names_sectors(results, cfg=None):
    """为结果列表补充中文名称和板块（从本地 list.txt 加载）"""
    if not results:
        return results
    _load_name_map()
    for r in results:
        code = r['code']
        # A股: list.txt 存为 "数字.SZ" / "数字.SS"（6位数字 + 后缀）
        if code.endswith('.SS') or code.endswith('.SZ'):
            name = _NAME_CACHE.get(code, '')
            r['name'] = name if name else code
            r['sector'] = _SECTOR_CACHE.get(code, '—')
        # 港股: list.txt 里是 5 位数字，cache key 存为 "HK"+5位
        elif code.endswith('.HK'):
            raw = code.replace('.HK', '').lstrip('0').zfill(5)  # "6651" → "06651"
            name = _NAME_CACHE.get('HK' + raw, '')
            r['name'] = name if name else code
            r['sector'] = _SECTOR_CACHE.get('HK' + raw, '—')
        else:
            r['name'] = code
            r['sector'] = '—'
    return results


DEFAULT_CFG = {
    "a_kline_dir":  "/Users/tonyleung/Downloads/股票/A股/Kline",
    "a_fin_dir":    "/Users/tonyleung/Downloads/股票/A股/财报",
    "hk_kline_dir": "/Users/tonyleung/Downloads/股票/港股/Kline",
    "hk_fin_dir":   "/Users/tonyleung/Downloads/股票/港股/财报",
    "out_dir":      "/Users/tonyleung/Downloads/股票/SEPA-VCP选股",
}

# ──────────────────────────────────────────────
# 依赖库检查
# ──────────────────────────────────────────────
try:
    import pandas as pd
except Exception:
    print("需要 pandas: pip3 install pandas")
    raise

# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────
def _calc_cagr(vals):
    """计算3年CAGR（从大到小排序）"""
    try:
        vals = sorted([v for v in vals if v and v > 0], reverse=True)
        if len(vals) < 2:
            return None
        oldest, newest = vals[-1], vals[0]
        if oldest <= 0 or newest <= oldest:
            return None
        return ((newest / oldest) ** (1 / 3) - 1) * 100
    except Exception:
        return None

def _safe_float(v, default=None):
    try:
        f = float(v)
        return f if f != 0 else default
    except Exception:
        return default

def list_files(d):
    try:
        return sorted([f for f in os.listdir(d) if f.endswith(".csv")])
    except Exception:
        return []

def load_kline(path, market="CN"):
    """返回 [{date, open, high, low, close, volume}]，最近日期在前"""
    try:
        df = pd.read_csv(path)
        df.columns = [c.strip() for c in df.columns.str.lower()]
        if "date" not in df.columns:
            return []
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"]).sort_values("date")
        # 列名标准化
        col_map = {}
        for c in df.columns:
            if c in ("open", "开盘"): col_map[c] = "open"
            elif c in ("high", "最高"): col_map[c] = "high"
            elif c in ("low", "最低"): col_map[c] = "low"
            elif c in ("close", "收盘"): col_map[c] = "close"
            elif c in ("volume", "vol", "成交"): col_map[c] = "volume"
        df = df.rename(columns=col_map)
        need = {"open", "high", "low", "close", "volume"}
        if not need.issubset(set(df.columns)):
            # HK格式: open, close, high, low, volume
            if {"open", "close", "high", "low", "volume"}.issubset(set(df.columns)):
                col_map2 = {}
                for c in df.columns:
                    if c == "open": col_map2[c] = "open"
                    elif c == "close": col_map2[c] = "close"
                    elif c == "high": col_map2[c] = "high"
                    elif c == "low": col_map2[c] = "low"
                    elif c == "volume": col_map2[c] = "volume"
                df = df.rename(columns=col_map2)
                need = {"open", "high", "low", "close", "volume"}
                if not need.issubset(set(df.columns)):
                    return []
            else:
                return []
        df = df.sort_values("date")
        return df[["date","open","high","low","close","volume"]].to_dict("records")
    except Exception:
        return []

def load_hk_fin(path):
    """
    读取港股财报 CSV（多年数据），计算财务指标。
    返回 dict，包含:
      rev_yoy, prof_yoy, prof_qoq, roe, cagr_3y
    """
    try:
        import math as _math
        df = pd.read_csv(path, dtype=str)
        df.columns = [c.strip() for c in df.columns]
        if df.shape[0] < 2:
            return {}

        date_col = df.columns[0]  # REPORT_DATE
        df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
        df = df.dropna(subset=[date_col]).sort_values(date_col, ascending=False)
        if df.shape[0] < 2:
            return {}

        # 严格匹配列名，避免匹配到错误字段
        def get_col(name):
            for c in df.columns:
                if name in c:
                    return c
            return None

        # 营收列：优先 IS_营业额（一般企业），其次 IS_经营收入总额（保险等）
        rev_col = get_col('IS_营业额') or get_col('经营收入总额') or get_col('营业额')
        # 净利润列
        prof_col = get_col('IS_股东应占溢利') or get_col('股东应占溢利')
        # 股东权益列
        equity_col = get_col('BS_股东权益') or get_col('股东权益')

        if not rev_col or not prof_col:
            return {}

        # 读取最近4年的数据
        years = {}   # {year_str: (rev, prof)}
        for _, row in df.iterrows():
            dt = row[date_col]
            yr = str(dt.year) if hasattr(dt, 'year') else str(dt)[:4]
            if yr not in years and len(years) < 4:
                try:
                    rv = _safe_float(str(row[rev_col]).strip())
                    pf = _safe_float(str(row[prof_col]).strip())
                    years[yr] = (rv, pf)
                except Exception:
                    pass

        sorted_yrs = sorted(years.keys(), reverse=True)  # newest first
        if len(sorted_yrs) < 2:
            return {}

        rev0, prof0 = years[sorted_yrs[0]][0], years[sorted_yrs[0]][1]
        rev1, prof1 = years[sorted_yrs[1]][0], years[sorted_yrs[1]][1]

        # YoY（保护：分母不能为0或负）
        def safe_yoy(new, old):
            if new is None or old is None or old == 0 or not _math.isfinite(old):
                return None
            v = (new - old) / abs(old) * 100
            return round(v, 2) if _math.isfinite(v) else None

        rev_yoy  = safe_yoy(rev0, rev1)
        prof_yoy = safe_yoy(prof0, prof1)

        # 3年CAGR
        cagr = None
        if len(sorted_yrs) >= 3:
            yr_oldest = sorted_yrs[-1]
            r_oldest = years[yr_oldest][0]
            cagr_vals = [rv for rv in [rev0, years[sorted_yrs[1]][0], r_oldest] if rv and rv > 0]
            if len(cagr_vals) >= 2:
                cagr = _calc_cagr(cagr_vals)

        # ROE = 股东应占溢利 / 股东权益
        roe = None
        if equity_col:
            try:
                eq0 = _safe_float(str(df.iloc[0][equity_col]).strip())
                if eq0 and eq0 != 0 and prof0 is not None:
                    raw = prof0 / eq0 * 100
                    if _math.isfinite(raw):
                        roe = round(raw, 2)
            except Exception:
                pass

        return {
            "rev_yoy":  rev_yoy,
            "prof_yoy": prof_yoy,
            "prof_qoq": None,
            "roe":      roe,
            "cagr_3y":  round(cagr, 2) if cagr is not None and _math.isfinite(cagr) else None,
        }
    except Exception:
        return {}

def calc_tech(kl, market="CN"):
    """计算技术指标，返回 dict"""
    n = len(kl)
    if n < 20:
        return None
    closes = [float(k["close"]) for k in kl]
    volumes = [float(k["volume"]) for k in kl]
    price = closes[-1]

    ma20 = sum(closes[-20:]) / 20
    ma50 = sum(closes[-50:]) / 50 if n >= 50 else None
    ma150 = sum(closes[-150:]) / 150 if n >= 150 else None
    ma5  = sum(closes[-5:]) / 5

    vol10 = sum(volumes[-10:]) / 10
    vol120 = sum(volumes[-120:]) / 120 if n >= 120 else sum(volumes) / n

    above_ma50   = (price > ma50) if ma50 else False
    above_ma150  = (price > ma150) if ma150 else (price > ma20)
    ma50_above_150 = (ma50 > ma150) if ma150 else (ma5 > ma20)
    vol_surge     = vol10 > vol120

    # VCP波动收缩
    recent30 = kl[-30:] if len(kl) >= 30 else kl
    cur_range  = (max(k["high"] for k in recent30) - min(k["low"] for k in recent30)) / price
    full_range = (max(closes) - min(closes)) / price
    is_contracting = cur_range < full_range * 0.6

    # 基部突破（近似：短期高点）
    breakouts = 0
    if n >= 30:
        for i in range(-30, -5):
            if i+1 >= -len(kl):
                window = closes[i:i+5]
                if len(window) == 5 and window[-1] == max(window):
                    breakouts += 1

    return {
        "close": price,
        "ma20": ma20, "ma50": ma50, "ma150": ma150,
        "above_ma50": above_ma50, "above_ma150": above_ma150,
        "ma50_above_150": ma50_above_150,
        "vol_ratio": vol10 / max(vol120, 1),
        "vol10": vol10, "vol120": vol120,
        "is_contracting": is_contracting,
        "breakouts": breakouts,
        "n": n,
        "last_date": kl[-1]["date"] if kl else None,
    }

def calc_fin_hk(fd):
    """从港股财报dict计算财务指标"""
    if not fd:
        return None
    rev   = fd.get("IS_营业额")       or fd.get("IS_营业收入")    or fd.get("IS_收入")
    profit= fd.get("IS_股东应占溢利")  or fd.get("IS_本公司拥有人应占溢利") or fd.get("IS_溢利")
    roe_v = None

    try:
        if rev and profit:
            rev = float(rev); profit = float(profit)
            if rev > 0 and profit > 0:
                roe_v = profit / rev * 100
    except Exception:
        pass

    # 读取多年数据（同一个fd只包含一年，多次调用取最新）
    return {
        "rev": rev, "profit": profit, "roe": roe_v,
        "rev_yoy": None, "prof_yoy": None, "prof_qoq": None, "cagr_3y": None,
    }

def run_screening(params):
    """执行SEPA-VCP筛选，返回 (结果列表, stage_counts dict)"""
    cfg = params["config"]
    mkt = params.get("market", "both")
    fund = params.get("fundamental", {})
    tech = params.get("technical", {})
    vcp  = params.get("vcp", {})

    f_rev_yoy  = fund.get("rev_yoy", 25)
    f_prof_yoy = fund.get("prof_yoy", 30)
    f_roe      = fund.get("roe", 10)
    f_cagr     = fund.get("cagr_3y", 20)
    t_vol_ratio = tech.get("min_vol_ratio", 1.0)
    t_ma50_req  = tech.get("require_ma50", True)
    t_ma150_req = tech.get("require_ma150", True)
    v_min_score = vcp.get("min_score", 60)

    results = []

    # stage_counts: 每个条件独立计数（passed counts after each filter）
    sc = dict(total=0, ma50=0, ma150=0, vol=0, vcp=0,
              rev=0, prof=0, roe=0, cagr=0, final=0)

    # ─── A股 ───
    if mkt in ("cn", "both"):
        a_kline_dir = cfg["a_kline_dir"]
        a_fin_dir   = cfg.get("a_fin_dir", "")
        a_files = list_files(a_kline_dir)
        for fname in a_files:
            code = fname.replace(".csv", "").replace(".SZ", "").replace(".SS", "")
            kpath = os.path.join(a_kline_dir, fname)
            kl = load_kline(kpath, "CN")
            if len(kl) < 60:
                continue
            tk = calc_tech(kl, "CN")
            if tk is None:
                continue
            sc["total"] += 1
            # MA50
            if t_ma50_req and not tk["above_ma50"]:
                continue
            sc["ma50"] += 1
            # MA150
            if t_ma150_req and not tk["above_ma150"]:
                continue
            sc["ma150"] += 1
            # 量比
            if tk["vol_ratio"] < t_vol_ratio:
                continue
            sc["vol"] += 1
            # VCP
            vcp_score = 0
            if tk["is_contracting"]: vcp_score += 30
            vcp_score += min(30, tk["vol_ratio"] / 2 * 30)
            vcp_score += min(40, tk["breakouts"] * 12)
            rs_proxy = (tk["close"] / tk["ma50"] - 1) if tk["ma50"] else 0
            vcp_score += min(100, max(0, rs_proxy * 5 + 50))
            if vcp_score < v_min_score:
                continue
            sc["vcp"] += 1
            # A股财务数据暂无（_rev等保持0）
            sc["rev"] += 1; sc["prof"] += 1; sc["roe"] += 1; sc["cagr"] += 1
            # suffix
            suffix = ".SZ" if not code.startswith(("6", "8")) else ".SS"
            results.append({
                "code": code + suffix, "name": code, "market": "A股",
                "close": round(tk["close"], 2),
                "ma50": round(tk["ma50"], 2) if tk["ma50"] else None,
                "ma150": round(tk["ma150"], 2) if tk["ma150"] else None,
                "vol_ratio": round(tk["vol_ratio"], 2),
                "vcp_score": round(vcp_score, 1),
                "is_contracting": tk["is_contracting"],
                "breakouts": tk["breakouts"],
                "last_date": str(tk["last_date"])[:10] if tk["last_date"] else None,
                "rev_yoy": None, "prof_yoy": None, "roe": None, "cagr_3y": None,
                "fin_note": "财务数据待补充",
            })
            sc["final"] += 1

    # ─── 港股 ───
    if mkt in ("hk", "both"):
        hk_kline_dir = cfg["hk_kline_dir"]
        hk_fin_dir   = cfg.get("hk_fin_dir", "")
        hk_files = list_files(hk_kline_dir)
        for fname in hk_files:
            code = fname.replace(".HK.csv", "").replace(".csv", "")
            kpath = os.path.join(hk_kline_dir, fname)
            kl = load_kline(kpath, "HK")
            if len(kl) < 60:
                continue
            tk = calc_tech(kl, "HK")
            if tk is None:
                continue
            sc["total"] += 1
            # MA50
            if t_ma50_req and not tk["above_ma50"]:
                continue
            sc["ma50"] += 1
            # MA150
            if t_ma150_req and not tk["above_ma150"]:
                continue
            sc["ma150"] += 1
            # 量比
            if tk["vol_ratio"] < t_vol_ratio:
                continue
            sc["vol"] += 1
            # VCP
            vcp_score = 0
            if tk["is_contracting"]: vcp_score += 30
            vcp_score += min(30, tk["vol_ratio"] / 2 * 30)
            vcp_score += min(40, tk["breakouts"] * 12)
            rs_proxy = (tk["close"] / tk["ma50"] - 1) if tk["ma50"] else 0
            vcp_score += min(100, max(0, rs_proxy * 5 + 50))
            if vcp_score < v_min_score:
                continue
            sc["vcp"] += 1
            # 财务
            fin = load_hk_fin(os.path.join(hk_fin_dir, code + ".csv")) \
                if os.path.exists(os.path.join(hk_fin_dir, code + ".csv")) else {}
            rev_yoy  = fin.get("rev_yoy")
            prof_yoy = fin.get("prof_yoy")
            roe_v    = fin.get("roe")
            cagr     = fin.get("cagr_3y")
            # 营收YoY
            if rev_yoy is not None and 0 < f_rev_yoy and rev_yoy < f_rev_yoy:
                pass  # filtered out
            else:
                sc["rev"] += 1
            # 净利YoY
            if prof_yoy is not None and 0 < f_prof_yoy and prof_yoy < f_prof_yoy:
                pass
            else:
                sc["prof"] += 1
            # ROE
            if roe_v is not None and 0 < f_roe and roe_v < f_roe:
                pass
            else:
                sc["roe"] += 1
            # CAGR
            if cagr is not None and 0 < f_cagr and cagr < f_cagr:
                pass
            else:
                sc["cagr"] += 1
            # 若任何财务条件被过滤，continue
            if (rev_yoy  is not None and 0 < f_rev_yoy and rev_yoy  < f_rev_yoy) or \
               (prof_yoy is not None and 0 < f_prof_yoy and prof_yoy < f_prof_yoy) or \
               (roe_v    is not None and 0 < f_roe     and roe_v    < f_roe)     or \
               (cagr     is not None and 0 < f_cagr    and cagr      < f_cagr):
                continue
            sc["final"] += 1
            results.append({
                "code": code + ".HK", "name": code, "market": "港股",
                "close": round(tk["close"], 2),
                "ma50": round(tk["ma50"], 2) if tk["ma50"] else None,
                "ma150": round(tk["ma150"], 2) if tk["ma150"] else None,
                "vol_ratio": round(tk["vol_ratio"], 2),
                "vcp_score": round(vcp_score, 1),
                "is_contracting": tk["is_contracting"],
                "breakouts": tk["breakouts"],
                "last_date": str(tk["last_date"])[:10] if tk["last_date"] else None,
                "rev_yoy": round(rev_yoy, 1) if rev_yoy else None,
                "prof_yoy": round(prof_yoy, 1) if prof_yoy else None,
                "roe": round(roe_v, 1) if roe_v else None,
                "cagr_3y": round(cagr, 1) if cagr else None,
                "fin_note": "",
            })

    # stage_counts dict（含 thresholds 供前端显示）
    stage_counts = {
        **sc,
        "th": {
            "rev_yoy":   f_rev_yoy,
            "prof_yoy":  f_prof_yoy,
            "roe":       f_roe,
            "cagr":      f_cagr,
            "vol_ratio": t_vol_ratio,
            "vcp_min":   v_min_score,
            "ma50_req":  t_ma50_req,
            "ma150_req": t_ma150_req,
        },
    }

    results.sort(key=lambda x: x["vcp_score"], reverse=True)
    return enrich_names_sectors(results, cfg), stage_counts



# ──────────────────────────────────────────────
# FastAPI 应用
# ──────────────────────────────────────────────
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

app = FastAPI(title="SEPA × VCP 选股工具")

# 内存缓存配置
_cfg = dict(DEFAULT_CFG)

HTML = r"""
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SEPA × VCP 选股工具</title>
<style>
  :root {
    --bg: #0f1117;
    --card: #1a1d27;
    --border: #2a2d3a;
    --accent: #5b8af0;
    --accent2: #4ecdc4;
    --green: #3ddc84;
    --red: #ff6b6b;
    --gold: #ffd93d;
    --text: #e8e8f0;
    --muted: #8888aa;
    --radius: 10px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
         background: var(--bg); color: var(--text);
         min-height: 100vh; padding: 20px; }

  h1 { text-align: center; color: var(--accent);
        font-size: 1.6em; margin-bottom: 6px; letter-spacing: 2px; }
  .subtitle { text-align: center; color: var(--muted); font-size: 0.8em; margin-bottom: 24px; }

  /* ── 通用卡片 ── */
  .card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 18px; margin-bottom: 14px;
  }
  .card-title {
    font-size: 0.85em; font-weight: 600; color: var(--accent);
    margin-bottom: 12px; letter-spacing: 1px;
    border-bottom: 1px solid var(--border); padding-bottom: 8px;
  }

  /* ── 路径配置 ── */
  .path-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .path-row { display: flex; align-items: center; gap: 8px; }
  .path-row label { color: var(--muted); font-size: 0.75em; min-width: 80px; }
  .path-row input {
    flex: 1; background: var(--bg); border: 1px solid var(--border);
    border-radius: 6px; color: var(--text); padding: 6px 10px;
    font-size: 0.78em; outline: none;
  }
  .path-row input:focus { border-color: var(--accent); }

  /* ── 市场选择 ── */
  .market-btns { display: flex; gap: 8px; }
  .mkt-btn {
    flex: 1; padding: 9px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--bg); color: var(--muted); cursor: pointer; font-size: 0.82em;
    transition: all 0.2s; text-align: center;
  }
  .mkt-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  .mkt-btn:hover:not(.active) { border-color: var(--accent); color: var(--text); }

  /* ── 过滤器网格 ── */
  .filter-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .filter-item { display: flex; flex-direction: column; gap: 4px; }
  .filter-item label { font-size: 0.72em; color: var(--muted); }
  .filter-item input[type=range] {
    width: 100%; accent-color: var(--accent); cursor: pointer;
  }
  .val-badge {
    display: inline-block; background: var(--accent); color: #fff;
    border-radius: 4px; padding: 1px 7px; font-size: 0.72em; min-width: 36px; text-align: center;
  }

  /* ── 勾选框 ── */
  .toggle-row { display: flex; align-items: center; gap: 8px; cursor: pointer; }
  .toggle-row input[type=checkbox] { accent-color: var(--accent); width: 15px; height: 15px; }
  .toggle-row span { font-size: 0.8em; color: var(--text); }

  /* ── 按钮 ── */
  .btn-row { display: flex; gap: 10px; margin-top: 6px; }
  .btn {
    flex: 1; padding: 12px; border-radius: 8px; border: none;
    font-size: 0.88em; font-weight: 600; cursor: pointer; transition: all 0.2s;
  }
  .btn-primary { background: var(--accent); color: #fff; }
  .btn-primary:hover { background: #4a7ae0; }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-secondary { background: var(--border); color: var(--text); }
  .btn-secondary:hover { background: #3a3d4a; }

  /* ── 统计条 ── */
  .stats-bar {
    display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 14px;
  }
  .stat-chip {
    background: var(--card); border: 1px solid var(--border);
    border-radius: 6px; padding: 6px 12px; font-size: 0.72em; color: var(--muted);
  }
  .stat-chip b { color: var(--text); }
  .stat-chip.green { border-color: var(--green); color: var(--green); }
  .stat-chip.green b { color: var(--green); }

  /* ── 表格 ── */
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 0.75em; }
  th {
    background: #12141e; color: var(--accent); padding: 8px 10px;
    text-align: left; border-bottom: 2px solid var(--border);
    white-space: nowrap; position: sticky; top: 0;
  }
  td {
    padding: 7px 10px; border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }
  tr:hover { background: #1e2130; }
  .market-tag {
    border-radius: 4px; padding: 1px 6px; font-size: 0.7em;
  }
  .market-A { background: #ff6b6b33; color: #ff6b6b; }
  .market-HK { background: #5b8af033; color: #5b8af0; }
  .vcp-high { color: var(--green); font-weight: 600; }
  .vcp-mid  { color: var(--gold); }
  .vcp-low  { color: var(--muted); }
  .yes { color: var(--green); }
  .no  { color: var(--red); }
  .null { color: var(--muted); }

  /* ── 状态行 ── */
  #status { font-size: 0.78em; color: var(--muted); margin-top: 8px; min-height: 18px; }
  #status.ok { color: var(--green); }
  #status.err { color: var(--red); }

  /* ── 截面标题 ── */
  .section-label {
    font-size: 0.65em; color: var(--muted); letter-spacing: 1px;
    margin: 12px 0 8px; text-transform: uppercase;
  }
</style>
</head>
<body>

<h1>📈 SEPA × VCP 选股工具</h1>
<p class="subtitle">马克·米勒维尼趋势策略 · A股 + 港股</p>

<!-- ══ 路径配置 ══ -->
<div class="card">
  <div class="card-title">⚙️ 数据路径配置</div>
  <div class="path-grid">
    <div class="path-row">
      <label>A股 K线</label>
      <input id="a_kline_dir" value="/Users/tonyleung/Downloads/股票/A股/Kline">
    </div>
    <div class="path-row">
      <label>A股 财报</label>
      <input id="a_fin_dir" value="/Users/tonyleung/Downloads/股票/A股/财报">
    </div>
    <div class="path-row">
      <label>港股 K线</label>
      <input id="hk_kline_dir" value="/Users/tonyleung/Downloads/股票/港股/Kline">
    </div>
    <div class="path-row">
      <label>港股 财报</label>
      <input id="hk_fin_dir" value="/Users/tonyleung/Downloads/股票/港股/财报">
    </div>
  </div>
</div>

<!-- ══ 市场选择 ══ -->
<div class="card">
  <div class="card-title">🎯 市场选择</div>
  <div class="market-btns">
    <div class="mkt-btn active" data-val="both">A股 + 港股</div>
    <div class="mkt-btn" data-val="cn">仅 A股</div>
    <div class="mkt-btn" data-val="hk">仅 港股</div>
  </div>
</div>

<!-- ══ 过滤条件 ══ -->
<div class="card">
  <div class="card-title">🔍 筛选条件</div>

  <div class="section-label">基本面</div>
  <div class="filter-grid">
    <div class="filter-item">
      <label>营收 YoY &gt; <span class="val-badge" id="rev_yoy_val">25</span>%</label>
      <input type="range" id="rev_yoy" min="0" max="80" value="25" step="5">
    </div>
    <div class="filter-item">
      <label>净利 YoY &gt; <span class="val-badge" id="prof_yoy_val">30</span>%</label>
      <input type="range" id="prof_yoy" min="0" max="100" value="30" step="5">
    </div>
    <div class="filter-item">
      <label>净资产收益率(ROE) &gt; <span class="val-badge" id="roe_val">10</span>%</label>
      <input type="range" id="roe" min="0" max="40" value="10" step="1">
    </div>
    <div class="filter-item">
      <label>3年净利润复合增长率(CAGR) &gt; <span class="val-badge" id="cagr_3y_val">20</span>%</label>
      <input type="range" id="cagr_3y" min="0" max="60" value="20" step="5">
    </div>
  </div>

  <div class="section-label">技术面</div>
  <div class="filter-grid">
    <div class="filter-item">
      <label>量比(10日/120日均量) &gt; <span class="val-badge" id="vol_ratio_val">1.0</span>x</label>
      <input type="range" id="vol_ratio" min="0.5" max="5" value="1.0" step="0.1">
    </div>
    <div class="filter-item">
      <label>VCP形态评分 &ge; <span class="val-badge" id="vcp_min_val">60</span>分</label>
      <input type="range" id="vcp_min" min="20" max="100" value="60" step="5">
    </div>
  </div>
  <div style="margin-top:8px">
    <div class="toggle-row">
      <input type="checkbox" id="req_ma50" checked>
      <span>要求股价 &gt; 50日均线(MA50)</span>
    </div>
    <div class="toggle-row">
      <input type="checkbox" id="req_ma150" checked>
      <span>要求 MA50 &gt; 150日均线(MA150)，即均线多头排列</span>
    </div>
  </div>
</div>

<!-- ══ 按钮 ══ -->
<div class="btn-row">
  <button class="btn btn-primary" id="btn_run" onclick="runScreening()">▶ 运行筛选</button>
  <button class="btn btn-secondary" onclick="resetDefaults()">↺ 重置默认</button>
  <button class="btn btn-secondary" onclick="exportCSV()">💾 导出CSV</button>
</div>
<div id="status"></div>

<!-- ══ 筛选漏斗 ══ -->
<div class="card" id="filter_counts_card" style="display:none">
  <div class="card-title">🔬 筛选漏斗 · 各条件命中数量</div>
  <div id="filter_counts_body" style="display:flex;gap:6px;flex-wrap:wrap;align-items:center"></div>
</div>

<!-- ══ 结果表格 ══ -->
<div class="card" id="result_card" style="display:none">
  <div class="card-title">📊 筛选结果</div>
  <div class="table-wrap">
    <table id="result_tbl">
      <thead>
        <tr>
          <th>#</th>
          <th onclick="sortColumn('code')" style="cursor:pointer">股票代码 ↕</th>
          <th onclick="sortColumn('market')" style="cursor:pointer">市场 ↕</th>
          <th onclick="sortColumn('sector')" style="cursor:pointer">板块 ↕</th>
          <th onclick="sortColumn('close')" style="cursor:pointer">最新价 ↕</th>
          <th onclick="sortColumn('ma50')" style="cursor:pointer">MA50 ↕</th>
          <th onclick="sortColumn('ma150')" style="cursor:pointer">MA150 ↕</th>
          <th onclick="sortColumn('vol_ratio')" style="cursor:pointer">量比 ↕</th>
          <th onclick="sortColumn('vcp_score')" style="cursor:pointer">VCP评分 ↕</th>
          <th onclick="sortColumn('is_contracting')" style="cursor:pointer">波动收缩 ↕</th>
          <th onclick="sortColumn('breakouts')" style="cursor:pointer">基部突破 ↕</th>
          <th>最后日期</th>
          <th onclick="sortColumn('rev_yoy')" style="cursor:pointer">营收YoY ↕</th>
          <th onclick="sortColumn('prof_yoy')" style="cursor:pointer">净利YoY ↕</th>
          <th onclick="sortColumn('roe')" style="cursor:pointer">ROE ↕</th>
          <th onclick="sortColumn('cagr_3y')" style="cursor:pointer">3年CAGR ↕</th>
        </tr>
      </thead>
      <tbody id="result_tbody"></tbody>
    </table>
  </div>
</div>

<script>
let lastResults = [];
let lastStageCounts = {};
let lastSort = { col: 'vcp_score', asc: false };  // 默认按VCP评分降序

// ── 市场按钮 ──
document.querySelectorAll('.mkt-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.mkt-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  });
});

// ── 滑块联动数值标签 + 同步 value 属性 ──
const sliders = [
  ['rev_yoy','rev_yoy_val'],
  ['prof_yoy','prof_yoy_val'],
  ['roe','roe_val'],
  ['cagr_3y','cagr_3y_val'],
  ['vol_ratio','vol_ratio_val'],
  ['vcp_min','vcp_min_val'],
];
sliders.forEach(([id, vid]) => {
  const el = document.getElementById(id);
  const valEl = document.getElementById(vid);
  el.addEventListener('input', () => {
    let v = el.value;
    if (id === 'vol_ratio') v = parseFloat(v).toFixed(1);
    valEl.textContent = v;
    el.setAttribute('data-val', v);   // 同步到属性，供 getParams 读取
  });
});

function getParams() {
  return {
    config: {
      a_kline_dir:  document.getElementById('a_kline_dir').value,
      a_fin_dir:    document.getElementById('a_fin_dir').value,
      hk_kline_dir: document.getElementById('hk_kline_dir').value,
      hk_fin_dir:   document.getElementById('hk_fin_dir').value,
    },
    market: document.querySelector('.mkt-btn.active').dataset.val,
    fundamental: {
      rev_yoy:  parseFloat(document.getElementById('rev_yoy').value),
      prof_yoy: parseFloat(document.getElementById('prof_yoy').value),
      roe:      parseFloat(document.getElementById('roe').value),
      cagr_3y:  parseFloat(document.getElementById('cagr_3y').value),
    },
    technical: {
      min_vol_ratio: parseFloat(document.getElementById('vol_ratio').value),
      require_ma50:  document.getElementById('req_ma50').checked,
      require_ma150: document.getElementById('req_ma150').checked,
    },
    vcp: {
      min_score: parseInt(document.getElementById('vcp_min').value),
    },
  };
}

function sortColumn(col) {
  if (lastSort.col === col) {
    lastSort.asc = !lastSort.asc;
  } else {
    lastSort.col = col;
    lastSort.asc = false;
  }
  renderResults(lastResults, lastStageCounts, 0);
}

function resetDefaults() {
  const sliderDefs = [
    ['rev_yoy','25','rev_yoy_val'],
    ['prof_yoy','30','prof_yoy_val'],
    ['roe','10','roe_val'],
    ['cagr_3y','20','cagr_3y_val'],
    ['vol_ratio','1.0','vol_ratio_val'],
    ['vcp_min','60','vcp_min_val'],
  ];
  sliderDefs.forEach(([id, defVal, vid]) => {
    const el = document.getElementById(id);
    const valEl = document.getElementById(vid);
    if (el) { el.value = defVal; el.setAttribute('data-val', defVal); }
    if (valEl) valEl.textContent = defVal;
  });
  document.querySelectorAll('.mkt-btn').forEach(b => b.classList.remove('active'));
  const bothBtn = document.querySelector('.mkt-btn[data-val="both"]');
  if (bothBtn) bothBtn.classList.add('active');
}

function vcpClass(score) {
  if (score >= 80) return 'vcp-high';
  if (score >= 60) return 'vcp-mid';
  return 'vcp-low';
}

function fmt(v, suffix='', prefix='') {
  if (v === null || v === undefined) return `<span class="null">N/A</span>`;
  return `${prefix}${v}${suffix}`;
}

async function runScreening() {
  const btn = document.getElementById('btn_run');
  const status = document.getElementById('status');
  btn.disabled = true;
  status.textContent = '⏳ 筛选中，请稍候...';
  status.className = '';
  try {
    const params = getParams();
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
      status.className = 'err';
      return;
    }
    lastResults = data.results || [];
    lastStageCounts = data.stage_counts || {};
    renderResults(lastResults, lastStageCounts, ms);
    status.textContent = `✅ 筛选完成，耗时 ${(ms/1000).toFixed(1)}s`;
    status.className = 'ok';
  } catch(e) {
    status.textContent = '❌ 请求失败: ' + e.message;
    status.className = 'err';
  } finally {
    btn.disabled = false;
  }
}

function renderResults(results, sc, ms) {
  // sc = stage_counts from backend: { total, ma50, ma150, vol, vcp, rev, prof, roe, cagr, final, th }
  const total = sc && sc.total !== undefined ? sc.total : 0;
  const th = (sc && sc.th) || {};

  // ── 筛选漏斗 ──
  const filters = [
    { key: 'ma50',  label: 'MA50过滤',   cond: '股价 &gt; 50日均线' },
    { key: 'ma150', label: 'MA150过滤',  cond: 'MA50 &gt; MA150 多头排列' },
    { key: 'vol',   label: '量比过滤',    cond: '量比 &gt; ' + th.vol_ratio },
    { key: 'vcp',   label: 'VCP评分',     cond: 'VCP评分 ≥ ' + th.vcp_min },
    { key: 'rev',   label: '营收YoY',     cond: '营收YoY &gt; ' + th.rev_yoy + '%' },
    { key: 'prof',  label: '净利YoY',    cond: '净利YoY &gt; ' + th.prof_yoy + '%' },
    { key: 'roe',   label: 'ROE',          cond: 'ROE &gt; ' + th.roe + '%' },
    { key: 'cagr',  label: '3年CAGR',   cond: 'CAGR &gt; ' + th.cagr + '%' },
  ];

  let funnelHtml = `<div class="stat-chip" style="border-color:var(--accent)">
    总扫描 <b style="color:var(--accent)">${total}</b> 只
  </div>`;

  filters.forEach(f => {
    const passed = Math.max(0, (sc && sc[f.key]) || 0);
    const pct = total > 0 ? ((passed / total) * 100).toFixed(1) : '0.0';
    const isBtl = (passed < (sc && sc.final !== undefined ? sc.final : 0)) && passed < total;
    const style = isBtl ? 'border-color:var(--red);color:var(--red)' : '';
    funnelHtml += `<div class="stat-chip"${style ? ' style="' + style + '"' : ''} title="${f.cond}">
      ${f.label} <b>${passed}</b> 只
      <span style="font-size:0.9em;color:var(--muted)">(${pct}%)</span>
    </div>`;
  });

  funnelHtml += `<div class="stat-chip green">
    最终候选 <b>${(sc && sc.final) || 0}</b> 只
  </div>`;

  document.getElementById('filter_counts_body').innerHTML = funnelHtml;
  document.getElementById('filter_counts_card').style.display = 'block';

  // ── 表格 ──
  const tbody = document.getElementById('result_tbody');
  if (!results || results.length === 0) {
    document.getElementById('result_card').style.display = 'block';
    tbody.innerHTML = '<tr><td colspan="16" style="text-align:center;color:var(--muted);padding:30px">未找到符合当前条件的股票</td></tr>';
    return;
  }

  const { col, asc } = lastSort;
  const sorted = [...results].sort((a, b) => {
    const va = a[col], vb = b[col];
    if (va === null || va === undefined) return 1;
    if (vb === null || vb === undefined) return -1;
    if (typeof va === 'boolean') return asc ? (va - vb) : (vb - va);
    if (typeof va === 'number')  return asc ? (va - vb) : (vb - va);
    return asc ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va));
  });

  let html = '';
  sorted.forEach((r, i) => {
    const mkt_cls = r.market === 'A股' ? 'market-A' : 'market-HK';
    const vcp_cls = vcpClass(r.vcp_score || 0);
    const contract = r.is_contracting ? '<span class="yes">是</span>' : '<span class="no">否</span>';
    const name4 = (r.name || r.code || '').slice(0, 4);
    html += `<tr>
      <td>${i+1}</td>
      <td><b>${r.code}</b><br><span style="font-size:0.7em;color:var(--muted)">${name4}</span></td>
      <td><span class="market-tag ${mkt_cls}">${r.market}</span></td>
      <td>${r.sector || '—'}</td>
      <td>${fmt(r.close)}</td>
      <td>${fmt(r.ma50)}</td>
      <td>${fmt(r.ma150)}</td>
      <td>${fmt(r.vol_ratio, 'x')}</td>
      <td class="${vcp_cls}"><b>${fmt(r.vcp_score)}</b></td>
      <td>${contract}</td>
      <td>${fmt(r.breakouts)}</td>
      <td>${r.last_date || ''}</td>
      <td>${fmt(r.rev_yoy, '%')}</td>
      <td>${fmt(r.prof_yoy, '%')}</td>
      <td>${fmt(r.roe, '%')}</td>
      <td>${fmt(r.cagr_3y, '%')}</td>
    </tr>`;
  });

  tbody.innerHTML = html;
  document.getElementById('result_card').style.display = 'block';
}



function exportCSV() {
  if (!lastResults.length) { alert('请先运行筛选'); return; }
  const hdr = ['代码','市场','最新价','MA50','MA150','量比','VCP评分','收缩','突破次数','最后日期',
                '营收YoY%','净利YoY%','ROE%','3年CAGR%','备注'];
  const rows = lastResults.map(r => [
    r.code, r.market, r.close, r.ma50, r.ma150, r.vol_ratio, r.vcp_score,
    r.is_contracting ? '是' : '否', r.breakouts, r.last_date || '',
    r.rev_yoy, r.prof_yoy, r.roe, r.cagr_3y, r.fin_note || ''
  ]);
  const csv = [hdr, ...rows].map(r => r.join(',')).join('\n');
  const blob = new Blob(['\uFEFF' + csv], {type: 'text/csv;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `SEPA_VCP_${new Date().toISOString().slice(0,10)}.csv`;
  a.click();
}
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML


@app.post("/api/screen")
async def api_screen(payload: dict):
    try:
        results, stage_counts = run_screening(payload)
        return JSONResponse({"results": results, "stage_counts": stage_counts})
    except Exception as e:
        import traceback
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, status_code=500)


@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now().isoformat()}


if __name__ == "__main__":
    print("=" * 50)
    print("SEPA × VCP 选股工具")
    print("访问: http://localhost:7860")
    print("按 Ctrl+C 停止")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=7860, log_level="warning")
