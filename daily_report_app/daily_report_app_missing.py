def calc_south_money_local() -> Dict[str, Any]:
    """
    南向资金（港股通）：使用 akshare stock_hsgt_fund_flow_summary_em
    精确过滤：板块 in ["港股通(沪)","港股通(深)"] 且 资金方向=="南向"
    """
    try:
        import akshare as ak
        import pandas as pd

        summary = ak.stock_hsgt_fund_flow_summary_em()
        today_yi = 0.0
        today_date = str(pd.Timestamp.today().date())

        if summary is not None and not summary.empty:
            south_rows = summary[
                (summary["板块"].astype(str).isin(["港股通(沪)", "港股通(深)"])) &
                (summary["资金方向"] == "南向")
            ]
            for _, row in south_rows.iterrows():
                val = row.get("成交净买额", 0)
                if isinstance(val, (int, float)):
                    today_yi += float(val)

        # 读取历史数据（如果存在）
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
            "today_net": today_net,
            "cum_net": cum_net,
            "ma_net": ma_net,
            "signal": signal,
            "last_date": last_date,
            "recent_signals": recent_signals[:5],
        }
    except Exception:
        return {}


def fetch_vix() -> Dict[str, Any]:
    """获取VIX恐惧指数（Yahoo Finance），备选：用A股ATR估算"""
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
                return {
                    "vix": round(float(current_price), 2) if current_price else None,
                    "prev_close": round(float(prev_close), 2) if prev_close else None,
                    "source": "Yahoo Finance",
                }
    except Exception:
        pass

    # 备选：用沪深300 ATR 估算（粗略）
    try:
        df = fetch_eastmoney_index_kline("sh000300", days=20)
        if df is not None and len(df) >= 15:
            closes = df["close"].tolist()
            highs = df["high"].tolist()
            lows = df["low"].tolist()
            atr = calc_atr(highs, lows, closes, 14)
            if atr and closes[-1]:
                # ATR占比作为VIX替代（放大倍数：经验值×4）
                vix_est = round((atr / closes[-1]) * 100 * 4, 1)
                return {"vix": vix_est, "source": "沪深300ATR估算(备选)", "prev_close": None}
    except Exception:
        pass
    return {}


def run_timing(force: bool = False) -> Dict[str, Any]:
    """完整择时分析（30分钟缓存）"""
    cache_key = "/tmp/timing_cache_v3.json"
    cache_ttl = 30 * 60  # 30分钟

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

    # 1. 沪深300 MA200 择时
    try:
        df = fetch_eastmoney_index_kline("sh000300", days=300)
        if df is not None and not df.empty:
            closes = df["close"].tolist()
            dates = df["date"].tolist()
            hs300_ma = calc_ma_timing(closes, dates, period=200)
            result["hs300_ma200"] = hs300_ma
        else:
            result["hs300_ma200"] = {"signal": "数据获取失败"}
    except Exception as e:
        result["hs300_ma200"] = {"signal": f"错误: {e}"}

    # 2. 恒生指数 MA200 择时
    try:
        df_hk = fetch_hk_index_kline("hkHSI", days=500)
        if df_hk is not None and not df_hk.empty:
            closes_hk = df_hk["close"].tolist()
            dates_hk = df_hk["date"].dt.strftime("%Y-%m-%d").tolist()
            hk_ma = calc_ma_timing(closes_hk, dates_hk, period=200)
            result["hk_hs300"] = hk_ma
        else:
            result["hk_hs300"] = {"signal": "数据获取失败"}
    except Exception as e:
        result["hk_hs300"] = {"signal": f"错误: {e}"}

    # 3. 恒生科技指数 MA200 择时
    try:
        df_hktech = fetch_hk_index_kline("hkHSTECH", days=500)
        if df_hktech is not None and not df_hktech.empty:
            closes_ht = df_hktech["close"].tolist()
            dates_ht = df_hktech["date"].dt.strftime("%Y-%m-%d").tolist()
            ht_ma = calc_ma_timing(closes_ht, dates_ht, period=200)
            result["hk_hstech"] = ht_ma
        else:
            result["hk_hstech"] = {"signal": "数据获取失败"}
    except Exception as e:
        result["hk_hstech"] = {"signal": f"错误: {e}"}

    # 4. 黑色星期四
    try:
        result["black_thursday"] = fetch_quantclass_signal()
    except Exception:
        result["black_thursday"] = {}

    # 5. VIX
    try:
        result["vix"] = fetch_vix()
    except Exception:
        result["vix"] = {}

    # 6. 北向资金
    try:
        result["north_money"] = calc_north_money_local()
    except Exception:
        result["north_money"] = {}

    # 7. 南向资金
    try:
        result["south_money"] = calc_south_money_local()
    except Exception:
        result["south_money"] = {}

    # 8. 实时行情（腾讯财经）
    try:
        realtime_data = fetch_tencent_realtime([
            "sh000300", "sh000001", "sz399001",
            "hkHSI", "hkHSTECH",
        ])
        result["realtime"] = realtime_data
    except Exception:
        result["realtime"] = {}

    try:
        with open(cache_key, "w") as f:
            json.dump(result, f, ensure_ascii=False, default=str)
    except Exception:
        pass

    return result


# ═══════════════════════════════════════════════════════
# 预测引擎
# ═══════════════════════════════════════════════════════

def _signal_to_方向(sig: Optional[str]) -> Optional[str]:
    """将信号描述转换为方向"""
    if not sig:
        return None
    sig_str = str(sig)
    if any(x in sig_str for x in ["看涨", "持有", "看多", "买入"]):
        return "看涨"
    if any(x in sig_str for x in ["看跌", "空仓", "看空", "卖出"]):
        return "看跌"
    return "震荡"


def _votes(signals: List[Optional[str]]) -> str:
    """少数服从多数投票"""
    counts: Dict[str, int] = {"看涨": 0, "看跌": 0, "震荡": 0}
    for s in signals:
        d = _signal_to_方向(s)
        if d:
            counts[d] += 1
    if not any(counts.values()):
        return "震荡"
    return max(counts, key=counts.get)


def build_timing_predictions(raw: Dict[str, Any]) -> Dict[str, Any]:
    """基于择时原始数据构建预测信号"""
    predictions: Dict[str, Any] = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    # ── 分项预测 ──
    sub_predictions: Dict[str, Any] = {}

    # 1. 沪深300 MA200 → A股
    hs300 = raw.get("hs300_ma200", {})
    hs300_signal = hs300.get("signal", "")
    hs300_pct = hs300.get("pct_above")
    if hs300_pct is not None and abs(hs300_pct) > 5:
        hs300_conf = "高置信"
        hs300_reason = f"价格偏离MA200 {hs300_pct:+.2f}%，趋势明确"
    elif hs300_signal:
        hs300_conf = "中置信"
        hs300_reason = f"MA200择时信号：{hs300_signal}"
    else:
        hs300_conf = "低置信"
        hs300_reason = "数据不足"
    sub_predictions["hs300_ma200"] = {
        "prediction": _signal_to_方向(hs300_signal) or "震荡",
        "confidence": hs300_conf,
        "reasoning": hs300_reason,
        "detail": hs300_signal,
        "pct_above": hs300_pct,
    }

    # 2. 北向资金 → A股
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
    sub_predictions["north_money"] = {
        "prediction": north_pred,
        "confidence": north_conf,
        "reasoning": north_reason,
        "detail": f"今日净买入 {north.get('today_net', 0):.2f}亿 | 5日均线 {north.get('ma_net', 0):.2f}亿",
        "cum_net": cum_net,
        "today_net": north.get("today_net", 0),
    }

    # 3. 黑色星期四 → A股
    bt = raw.get("black_thursday", {})
    bt_sig = bt.get("signal")
    if bt_sig == 1:
        bt_pred, bt_conf, bt_reason = "看涨", "中置信", f"黑色星期四策略：{bt.get('market_type', '牛市')}信号，年化收益 {bt.get('annual', 'N/A')}%"
    elif bt_sig == 0:
        bt_pred, bt_conf, bt_reason = "看跌", "中置信", f"黑色星期四策略：{bt.get('market_type', '熊市')}信号"
    else:
        bt_pred, bt_conf, bt_reason = "震荡", "低置信", "黑色星期四信号获取失败"
    sub_predictions["black_thursday"] = {
        "prediction": _signal_to_方向(bt_pred) if bt_pred != "震荡" else "震荡",
        "confidence": bt_conf,
        "reasoning": bt_reason,
        "detail": bt.get("signal_desc", ""),
        "annual": bt.get("annual", ""),
        "recent_signals": bt.get("recent_signals", []),
    }

    # 4. VIX → A股
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
    sub_predictions["vix"] = {
        "prediction": vix_pred,
        "confidence": vix_conf,
        "reasoning": vix_reason,
        "detail": f"VIX={vix_val}" if vix_val is not None else "无数据",
        "source": vix_data.get("source", ""),
    }

    # 5. 南向资金 → 恒生
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
    sub_predictions["south_money"] = {
        "prediction": south_pred,
        "confidence": south_conf,
        "reasoning": south_reason,
        "detail": f"今日净买入 {south.get('today_net', 0):.2f}亿 | 5日均线 {south.get('ma_net', 0):.2f}亿",
        "cum_net": south_cum,
    }

    # 6. 恒生MA200
    hk_ma = raw.get("hk_hs300", {})
    hk_signal = hk_ma.get("signal", "")
    hk_pct = hk_ma.get("pct_above")
    if hk_pct is not None and abs(hk_pct) > 5:
        hk_conf = "高置信"
        hk_reason = f"恒生指数价格偏离MA200 {hk_pct:+.2f}%，趋势明确"
    elif hk_signal:
        hk_conf = "中置信"
        hk_reason = f"恒生指数MA200择时：{hk_signal}"
    else:
        hk_conf = "低置信"
        hk_reason = "数据不足"
    sub_predictions["hk_ma200"] = {
        "prediction": _signal_to_方向(hk_signal) or "震荡",
        "confidence": hk_conf,
        "reasoning": hk_reason,
        "detail": hk_signal,
        "pct_above": hk_pct,
    }

    predictions["sub_predictions"] = sub_predictions

    # ── 综合预测 ──
    # A股综合：北向+黑色星期四+VIX+MA200投票
    a股_signals = [
        sub_predictions["north_money"]["prediction"],
        sub_predictions["black_thursday"]["prediction"],
        sub_predictions["vix"]["prediction"],
        sub_predictions["hs300_ma200"]["prediction"],
    ]
    a股_final = _votes(a股_signals)
    a股_conf_values = [
        sub_predictions["north_money"]["confidence"],
        sub_predictions["black_thursday"]["confidence"],
        sub_predictions["vix"]["confidence"],
        sub_predictions["hs300_ma200"]["confidence"],
    ]
    high_count = a股_conf_values.count("高置信")
    if high_count >= 2:
        a股_final_conf = "高置信"
    elif high_count == 1 or a股_conf_values.count("中置信") >= 2:
        a股_final_conf = "中置信"
    else:
        a股_final_conf = "低置信"
    predictions["a_share"] = {
        "prediction": a股_final,
        "confidence": a股_final_conf,
        "reasoning": f"综合 {len(a股_signals)} 项指标投票：{a股_final}（{' '.join(a股_signals)}）",
        "votes": dict(zip(
            ["北向资金", "黑色星期四", "VIX", "沪深300MA200"],
            a股_signals
        )),
    }

    # 恒生综合：南向+恒生MA200投票
    hk_signals = [
        sub_predictions["south_money"]["prediction"],
        sub_predictions["hk_ma200"]["prediction"],
    ]
    hk_final = _votes(hk_signals)
    hk_conf_values = [
        sub_predictions["south_money"]["confidence"],
        sub_predictions["hk_ma200"]["confidence"],
    ]
    if "高置信" in hk_conf_values:
        hk_final_conf = "高置信"
    elif "中置信" in hk_conf_values:
        hk_final_conf = "中置信"
    else:
        hk_final_conf = "低置信"
    predictions["hk_share"] = {
        "prediction": hk_final,
        "confidence": hk_final_conf,
        "reasoning": f"综合南向资金和恒生MA200投票：{hk_final}",
        "votes": dict(zip(["南向资金", "恒生MA200"], hk_signals)),
    }

    return predictions


# ═══════════════════════════════════════════════════════
# FastAPI 应用
# ═══════════════════════════════════════════════════════

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

app = FastAPI(title="哮天每日收盘报告", version="3.0")

# 内存缓存
_timing_cache: Dict[str, Any] = {}
_predictions_cache: Dict[str, Any] = {}


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_CONTENT


@app.get("/api/timing")
async def get_timing():
    """完整择时数据（raw）"""
    try:
        data = run_timing()
        return JSONResponse(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/timing/refresh")
async def refresh_timing():
    """强制刷新择时数据"""
    try:
        data = run_timing(force=True)
        return JSONResponse({"status": "ok", "data": data})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/timing/predictions")
async def get_predictions():
    """综合预测信号"""
    try:
        raw = run_timing()
        preds = build_timing_predictions(raw)
        return JSONResponse(preds)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/timing/realtime")
async def get_realtime():
    """实时行情"""
    try:
        raw = run_timing()
        return JSONResponse(raw.get("realtime", {}))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/timing/ma")
async def get_ma():
    """MA状态"""
    try:
        raw = run_timing()
        return JSONResponse({
            "hs300": raw.get("hs300_ma200", {}),
            "hk": raw.get("hk_hs300", {}),
            "hk_tech": raw.get("hk_hstech", {}),
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/timing/fund")
async def get_fund():
    """南北资金"""
    try:
        raw = run_timing()
        return JSONResponse({
            "north": raw.get("north_money", {}),
            "south": raw.get("south_money", {}),
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/timing/vix")
async def get_vix():
    """VIX数据"""
    try:
        raw = run_timing()
        return JSONResponse(raw.get("vix", {}))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/timing/black_thursday")
async def get_black_thursday():
    """黑色星期四详情"""
    try:
        raw = run_timing()
        return JSONResponse(raw.get("black_thursday", {}))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/timing")
async def post_timing(request: Request):
    """完整择时数据（POST兼容）"""
    try:
        body = await request.json()
        force = body.get("force", False)
        data = run_timing(force=force)
        return JSONResponse(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/generate_report")
async def generate_report(request: Request):
    """生成报告 HTML"""
    try:
        body = await request.json()
        scan_results = body.get("scan_results", [])
        timing_data = run_timing()
        html = render_report_html(scan_results, timing_data)
        return HTMLResponse(html)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stocks")
async def search_stocks(q: str = ""):
    """搜索股票"""
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


def render_report_html(scan_results: List[Dict], timing_data: Dict) -> str:
    """生成报告 HTML（简单版）"""
    date_str = datetime.now().strftime("%Y-%m-%d")
    predictions = build_timing_predictions(timing_data)

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>每日报告 {date_str}</title>
<style>
body {{ font-family: Arial, sans-serif; background: #1a1a2e; color: #eee; padding: 20px; }}
h1 {{ color: #e94560; }}
.section {{ background: #16213e; border-radius: 10px; padding: 15px; margin: 15px 0; }}
.bullish {{ color: #4ade80; }} .bearish {{ color: #f87171; }} .neutral {{ color: #94a3b8; }}
</style></head><body>
<h1>📊 哮天每日收盘报告 {date_str}</h1>
<div class="section">
<h2>🎯 A股综合预测</h2>
<p>预测: <span class="{'bullish' if predictions['a_share']['prediction']=='看涨' else 'bearish' if predictions['a_share']['prediction']=='看跌' else 'neutral'}">{predictions['a_share']['prediction']}</span>
 | 置信度: {predictions['a_share']['confidence']}</p>
<p>{predictions['a_share']['reasoning']}</p>
</div>
<div class="section">
<h2>🎯 恒生综合预测</h2>
<p>预测: <span class="{'bullish' if predictions['hk_share']['prediction']=='看涨' else 'bearish' if predictions['hk_share']['prediction']=='看跌' else 'neutral'}">{predictions['hk_share']['prediction']}</span>
 | 置信度: {predictions['hk_share']['confidence']}</p>
<p>{predictions['hk_share']['reasoning']}</p>
</div>
<div class="section">
<h2>📈 择时摘要</h2>
<p>沪深300 MA200: {timing_data.get('hs300_ma200', {}).get('signal', 'N/A')}</p>
<p>恒生指数 MA200: {timing_data.get('hk_hs300', {}).get('signal', 'N/A')}</p>
<p>北向资金: {timing_data.get('north_money', {}).get('signal', 'N/A')} ({timing_data.get('north_money', {}).get('cum_net', 0):.1f}亿)</p>
<p>南向资金: {timing_data.get('south_money', {}).get('signal', 'N/A')} ({timing_data.get('south_money', {}).get('cum_net', 0):.1f}亿)</p>
<p>VIX: {timing_data.get('vix', {}).get('vix', 'N/A')}</p>
<p>黑色星期四: {timing_data.get('black_thursday', {}).get('signal_desc', 'N/A')}</p>
</div>
<div class="section">
<h2>📋 选股扫描 ({len(scan_results)} 只)</h2>
{''.join(f"<p>{r.get('name',r.get('code','?'))}: {', '.join(r.get('signals',[])[:3])}</p>" for r in scan_results[:10])}
</div>
</body></html>"""
    return html


# ═══════════════════════════════════════════════════════
# HTML 首页模板
# ═══════════════════════════════════════════════════════

HTML_CONTENT = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>哮天每日收盘报告</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0f1117;--card:#171b26;--card2:#1e2330;
  --border:#2a2f3e;--text:#c5c9d8;--text2:#8890a4;
  --bull:#3ddc84;--bear:#ff6b6b;--mid:#888aa0;
  --accent:#e07c3e;--blue:#4d9de0;
  --tab-bg:#1a1f2e;--tab-active:#232840;
}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
.container{max-width:1400px;margin:0 auto;padding:10px}
.header{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;background:var(--card);border-bottom:1px solid var(--border);border-radius:8px;margin-bottom:12px}
.header h1{font-size:18px;color:var(--accent);font-weight:700}
.header .subtitle{font-size:12px;color:var(--text2)}
.tabs{display:flex;gap:4px;background:var(--card);padding:6px;border-radius:8px;margin-bottom:12px}
.tab{padding:8px 20px;border-radius:6px;cursor:pointer;font-size:14px;color:var(--text2);transition:all .2s;border:none;background:transparent}
.tab:hover{background:var(--card2);color:var(--text)}
.tab.active{background:var(--tab-active);color:#fff;font-weight:600}
.content{background:var(--card);border-radius:10px;padding:16px;min-height:600px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
.card{background:var(--card2);border-radius:8px;padding:14px;border:1px solid var(--border)}
.card-title{font-size:14px;font-weight:600;color:var(--text);margin-bottom:10px;display:flex;align-items:center;gap:6px}
.card-title .icon{font-size:16px}
.stat-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.stat{background:var(--card);border-radius:6px;padding:8px 10px}
.stat .label{font-size:11px;color:var(--text2);margin-bottom:2px}
.stat .value{font-size:16px;font-weight:700}
.stat .value.bull{color:var(--bull)}
.stat .value.bear{color:var(--bear)}
.stat .value.mid{color:var(--mid)}
.full-stat{background:var(--card);border-radius:6px;padding:10px;margin-top:8px}
.full-stat .label{font-size:11px;color:var(--text2)}
.full-stat .value{font-size:20px;font-weight:700;margin:4px 0}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}
.badge.bull{background:rgba(61,220,132,.15);color:var(--bull)}
.badge.bear{background:rgba(255,107,107,.15);color:var(--bear)}
.badge.mid{background:rgba(136,138,160,.15);color:var(--mid)}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{padding:6px 8px;text-align:left;border-bottom:1px solid var(--border)}
th{color:var(--text2);font-weight:600}
.signal-list{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
.signal-tag{padding:2px 7px;border-radius:4px;font-size:11px;background:rgba(77,157,224,.15);color:var(--blue)}
.hidden{display:none}
.loading{color:var(--text2);font-size:13px;padding:20px;text-align:center}
.error{color:var(--bear);font-size:13px;padding:10px;background:rgba(255,107,107,.1);border-radius:6px}
.section-title{font-size:13px;font-weight:600;color:var(--text2);margin:14px 0 8px;padding-bottom:4px;border-bottom:1px solid var(--border)}
.prediction-card{background:var(--card);border-radius:8px;padding:12px;margin-top:10px}
.pred-row{display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--border)}
.pred-row:last-child{border-bottom:none}
.pred-label{font-size:12px;color:var(--text2)}
.pred-value{font-size:13px;font-weight:600}
.conf-high{color:var(--bear)}.conf-mid{color:#fbbf24}.conf-low{color:var(--text2)}
.footer{text-align:center;padding:20px;color:var(--text2);font-size:11px}
#spinner{display:none;width:20px;height:20px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .6s linear infinite;margin-left:8px}
@keyframes spin{to{transform:rotate(360deg)}}
.kv{display:flex;justify-content:space-between;font-size:12px;padding:3px 0}
.kv .v{font-weight:600}
canvas{max-width:100%}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div>
      <h1>🐕 哮天每日收盘报告</h1>
      <div class="subtitle">马克·米勒维尼趋势策略 · 选股扫描 + 择时监测 + 每日报告</div>
    </div>
    <div style="display:flex;align-items:center">
      <span id="lastUpdate" style="font-size:12px;color:var(--text2)"></span>
      <div id="spinner"></div>
    </div>
  </div>

  <div class="tabs">
    <button class="tab active" onclick="showTab('scan')">📈 选股扫描</button>
    <button class="tab" onclick="showTab('sepa')">🎯 SEPA选股</button>
    <button class="tab" onclick="showTab('timing')">⏱️ 择时监测</button>
    <button class="tab" onclick="showTab('report')">📊 每日报告</button>
  </div>

  <div id="tab-scan" class="content">
    <div style="margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap">
      <select id="marketSel" onchange="loadStocks()" style="padding:6px 10px;border-radius:6px;border:1px solid var(--border);background:var(--card2);color:var(--text);font-size:13px">
        <option value="A">全部 A股</option>
        <option value="SZ">深圳主板</option>
        <option value="SS">上海主板</option>
        <option value="HK">港股</option>
      </select>
      <input id="stockSearch" placeholder="搜索股票代码/名称…" oninput="debounceSearch()" style="padding:6px 10px;border-radius:6px;border:1px solid var(--border);background:var(--card2);color:var(--text);font-size:13px;width:180px">
      <select id="signalSel" onchange="renderStocks()" style="padding:6px 10px;border-radius:6px;border:1px solid var(--border);background:var(--card2);color:var(--text);font-size:13px">
        <option value="">全部信号</option>
        <option value="RSI_14超卖">RSI超卖</option>
        <option value="MACD_金叉信号线">MACD金叉</option>
        <option value="KDJ_超卖金叉">KDJ超卖金叉</option>
        <option value="CCI_20超卖">CCI超卖</option>
        <option value="布林带_下轨支撑">布林下轨</option>
        <option value="VCP_波动收缩">VCP收缩</option>
        <option value="趋势_第二阶段">第二阶段</option>
        <option value="52周_接近新高">52周新高</option>
        <option value="均线_价格>MA20">价格>MA20</option>
      </select>
      <button onclick="runScan()" style="padding:6px 16px;background:var(--accent);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600">🔍 执行扫描</button>
      <button onclick="exportCSV()" style="padding:6px 12px;background:var(--card2);color:var(--text);border:1px solid var(--border);border-radius:6px;cursor:pointer;font-size:13px">📥 导出CSV</button>
    </div>
    <div id="scanStatus" style="font-size:12px;color:var(--text2);margin-bottom:8px"></div>
    <div id="scanResults" class="cards"></div>
  </div>

  <div id="tab-sepa" class="content hidden">
    <div style="margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap">
      <input id="sepaStock" placeholder="输入股票代码或名称，如 000001" style="padding:6px 10px;border-radius:6px;border:1px solid var(--border);background:var(--card2);color:var(--text);font-size:13px;width:260px">
      <button onclick="runSEPA()" style="padding:6px 16px;background:var(--accent);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600">🔍 SEPA分析</button>
    </div>
    <div id="sepaResults"></div>
  </div>

  <div id="tab-timing" class="content hidden">
    <div style="margin-bottom:12px">
      <button onclick="refreshTiming()" style="padding:6px 14px;background:var(--accent);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600">🔄 刷新数据</button>
      <span id="timingUpdate" style="font-size:12px;color:var(--text2);margin-left:12px"></span>
    </div>
    <div class="cards">

      <!-- 实时行情 -->
      <div class="card" id="t_realtime">
        <div class="card-title">📡 实时行情</div>
        <div id="tr_loading" class="loading">加载中…</div>
        <div id="tr_content" style="display:none"></div>
      </div>

      <!-- MA200择时 -->
      <div class="card" id="t_ma">
        <div class="card-title">📈 MA200 择时</div>
        <div id="tma_loading" class="loading">加载中…</div>
        <div id="tma_content" style="display:none"></div>
      </div>

      <!-- 南北资金 -->
      <div class="card" id="t_fund">
        <div class="card-title">💰 南北向资金</div>
        <div id="tf_loading" class="loading">加载中…</div>
        <div id="tf_content" style="display:none"></div>
      </div>

      <!-- VIX -->
      <div class="card" id="t_vix">
        <div class="card-title">😱 VIX 恐慌指数</div>
        <div id="tvix_loading" class="loading">加载中…</div>
        <div id="tvix_content" style="display:none"></div>
      </div>

      <!-- 预测信号 -->
      <div class="card" id="t_predictions">
        <div class="card-title">🎯 预测信号</div>
        <div id="tp_loading" class="loading">加载中…</div>
        <div id="tp_content" style="display:none"></div>
      </div>

      <!-- 黑色星期四 -->
      <div class="card" id="t_bt">
        <div class="card-title">🐺 黑色星期四</div>
        <div id="tbt_loading" class="loading">加载中…</div>
        <div id="tbt_content" style="display:none"></div>
      </div>

    </div>
  </div>

  <div id="tab-report" class="content hidden">
    <div style="margin-bottom:12px;display:flex;gap:8px">
      <button onclick="generateDailyReport()" style="padding:8px 18px;background:var(--accent);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:14px;font-weight:600">📊 生成今日报告</button>
      <button onclick="downloadReportImage()" style="padding:8px 14px;background:var(--card2);color:var(--text);border:1px solid var(--border);border-radius:6px;cursor:pointer;font-size:14px">🖼️ 下载图片</button>
    </div>
    <div id="reportContent"></div>
  </div>

</div>

<div class="footer">
  哮天每日收盘报告 · 基于马克·米勒维尼趋势策略 · 数据仅供参考，不构成投资建议
</div>

<script>
let _stocks = [];
let _timingData = null;
let _predictionsData = null;
let _scanResults = [];
let _searchTimer = null;

function debounceSearch(){clearTimeout(_searchTimer);_searchTimer=setTimeout(renderStocks,300)}

function showTab(name){
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.content').forEach(c=>c.classList.add('hidden'));
  const tabMap={scan:'tab-scan',sepa:'tab-sepa',timing:'tab-timing',report:'tab-report'};
  const btn=document.querySelector(`.tab[onclick="showTab('${name}')"]`);
  if(btn)btn.classList.add('active');
  const el=document.getElementById(tabMap[name]);
  if(el)el.classList.remove('hidden');
  if(name==='timing'&&!_timingData){loadAllTiming();}
}

async function loadAllTiming(){
  _timingData=await fetchJSON('/api/timing');
  document.getElementById('timingUpdate').textContent='更新: '+(_timingData.timestamp||'');
  loadTimingRealtime();
  loadTimingMA();
  loadTimingFund();
  loadTimingVIX();
  loadTimingPredictions();
  loadTimingBT();
}

function refreshTiming(){
  document.getElementById('spinner').style.display='inline-block';
  fetch('/api/timing/refresh',{method:'POST'}).then(r=>r.json()).then(d=>{
    _timingData=d.data||d;
    document.getElementById('timingUpdate').textContent='更新: '+(_timingData.timestamp||'');
    loadTimingRealtime();loadTimingMA();loadTimingFund();loadTimingVIX();loadTimingPredictions();loadTimingBT();
    document.getElementById('spinner').style.display='none';
  }).catch(()=>{document.getElementById('spinner').style.display='none';});
}

function loadTimingRealtime(){
  const d=_timingData?.realtime||{};
  const el=document.getElementById('tr_content');
  const loading=document.getElementById('tr_loading');
  if(!el)return;
  let html='<div class="stat-grid">';
  const items={'sh000300':'沪深300','sh000001':'上证指数','sz399001':'深证成指','hkHSI':'恒生指数','hkHSTECH':'恒生科技'};
  for(const [code,label] of Object.entries(items)){
    const info=d[code]||{};
    const pct=info.chg_pct||0;
    const cls=pct>0?'bull':pct<0?'bear':'mid';
    html+=`<div class="stat"><div class="label">${label}</div><div class="value ${cls}">${info.price||'--'}</div><div class="value ${cls}" style="font-size:12px">${pct>=0?'+':''}${pct.toFixed(2)}%</div></div>`;
  }
  html+='</div>';
  el.innerHTML=html;el.style.display='block';loading.style.display='none';
}

function loadTimingMA(){
  const hs300=_timingData?.hs300_ma200||{};
  const hk=_timingData?.hk_hs300||{};
  const hktech=_timingData?.hk_hstech||{};
  const el=document.getElementById('tma_content');
  const loading=document.getElementById('tma_loading');
  if(!el)return;
  const sig=(s)=>{if(!s)return['--','mid'];if(s.includes('看涨'))return[s,'bull'];if(s.includes('看跌'))return[s,'bear'];return[s,'mid'];};
  const [hs,sht]=sig(hs300.signal);
  const [hks,hkst]=sig(hk.signal);
  const [hkts,hktst]=sig(hktech.signal);
  el.innerHTML=`
  <div class="stat-grid">
    <div class="stat"><div class="label">沪深300 MA200</div><div class="value ${sht}">${hs}</div><div style="font-size:11px;color:var(--text2);margin-top:4px">价格: ${hs300.close||'--'} | MA: ${hs300.ma||'--'}</div><div style="font-size:11px;color:var(--text2)">偏离: ${hs300.pct_above!=null?hs300.pct_above.toFixed(2)+'%':'--'}</div></div>
    <div class="stat"><div class="label">恒生指数 MA200</div><div class="value ${hkst}">${hks}</div><div style="font-size:11px;color:var(--text2);margin-top:4px">价格: ${hk.close||'--'} | MA: ${hk.ma||'--'}</div><div style="font-size:11px;color:var(--text2)">偏离: ${hk.pct_above!=null?hk.pct_above.toFixed(2)+'%':'--'}</div></div>
    <div class="stat"><div class="label">恒生科技 MA200</div><div class="value ${hktst}">${hkts}</div><div style="font-size:11px;color:var(--text2);margin-top:4px">价格: ${hktech.close||'--'} | MA: ${hktech.ma||'--'}</div></div>
  </div>
  ${renderRecentSignals(hs300.recent_signals,'沪深300')}
  ${renderRecentSignals(hk.recent_signals,'恒生指数')}`;
  el.style.display='block';loading.style.display='none';
}

function loadTimingFund(){
  const north=_timingData?.north_money||{};
  const south=_timingData?.south_money||{};
  const el=document.getElementById('tf_content');
  const loading=document.getElementById('tf_loading');
  if(!el)return;
  const nc=north.cum_net>=0?'bull':'bear';
  const sc=south.cum_net>=0?'bull':'bear';
  el.innerHTML=`
  <div style="margin-bottom:10px">
    <div style="font-size:12px;color:var(--text2);margin-bottom:4px">🇨🇳 北向资金（沪股通+深股通）</div>
    <div class="stat-grid">
      <div class="stat"><div class="label">今日净买入</div><div class="value ${nc}">${(north.today_net||0).toFixed(2)} 亿</div></div>
      <div class="stat"><div class="label">5日累计</div><div class="value ${nc}">${(north.cum_net||0).toFixed(2)} 亿</div></div>
      <div class="stat"><div class="label">5日均线</div><div class="value mid">${(north.ma_net||0).toFixed(2)} 亿</div></div>
      <div class="stat"><div class="label">信号</div><div class="value ${nc}">${north.signal||'--'}</div></div>
    </div>
  </div>
  <div>
    <div style="font-size:12px;color:var(--text2);margin-bottom:4px">🇭🇰 南向资金（港股通）</div>
    <div class="stat-grid">
      <div class="stat"><div class="label">今日净买入</div><div class="value ${sc}">${(south.today_net||0).toFixed(2)} 亿</div></div>
      <div class="stat"><div class="label">5日累计</div><div class="value ${sc}">${(south.cum_net||0).toFixed(2)} 亿</div></div>
      <div class="stat"><div class="label">5日均线</div><div class="value mid">${(south.ma_net||0).toFixed(2)} 亿</div></div>
      <div class="stat"><div class="label">信号</div><div class="value ${sc}">${south.signal||'--'}</div></div>
    </div>
  </div>`;
  el.style.display='block';loading.style.display='none';
}

function loadTimingVIX(){
  const vix=_timingData?.vix||{};
  const el=document.getElementById('tvix_content');
  const loading=document.getElementById('tvix_loading');
  if(!el)return;
  const v=vix.vix;
  let cls='mid',label='正常波动',desc='';
  if(v!==null&&v!==undefined){
    if(v>=30){cls='bear';label='极度恐慌';desc='市场恐慌情绪极高，风险资产承压';}
    else if(v>=25){cls='bear';label='波动较大';desc='市场波动较大，保持谨慎';}
    else if(v>=15){cls='mid';label='正常区间';desc='市场正常波动';}
    else{cls='bull';label='低波动';desc='市场平静，风险偏好较高';}
  }
  el.innerHTML=`
  <div class="stat-grid">
    <div class="stat"><div class="label">VIX 当前值</div><div class="value ${cls}" style="font-size:24px">${v??'--'}</div></div>
    <div class="stat"><div class="label">状态</div><div class="value ${cls}">${label}</div></div>
  </div>
  ${v!==null&&v!==undefined?`<div class="full-stat"><div class="label">解读</div><div class="value" style="font-size:13px;color:var(--text)">${desc}</div></div>`:''}
  <div style="font-size:11px;color:var(--text2);margin-top:6px">数据源: ${vix.source||'--'}</div>`;
  el.style.display='block';loading.style.display='none';
}

async function loadTimingPredictions(){
  try{
    const resp=await fetch('/api/timing/predictions');
    const d=await resp.json();
    _predictionsData=d;
    renderPredictions(d);
  }catch(e){
    document.getElementById('tp_content').innerHTML=`<div class="error">加载预测数据失败: ${e}</div>`;
    document.getElementById('tp_content').style.display='block';
    document.getElementById('tp_loading').style.display='none';
  }
}

function renderPredictions(d){
  const el=document.getElementById('tp_content');
  const loading=document.getElementById('tp_loading');
  if(!el)return;

  const sub=d.sub_predictions||{};
  const a_share=d.a_share||{};
  const hk_share=d.hk_share||{};

  const confIcon=(c)=>c==='高置信'?'🔴':c==='中置信'?'🟡':'⚪';
  const confCls=(c)=>c==='高置信'?'conf-high':c==='中置信'?'conf-mid':'conf-low';
  const predCls=(p)=>p==='看涨'?'bull':p==='看跌'?'bear':'mid';

  const subRows=[
    {label:'沪深300MA200',key:'hs300_ma200',target:'A股'},
    {label:'北向资金',key:'north_money',target:'A股'},
    {label:'黑色星期四',key:'black_thursday',target:'A股'},
    {label:'VIX恐慌指数',key:'vix',target:'A股'},
    {label:'南向资金',key:'south_money',target:'恒生'},
    {label:'恒生MA200',key:'hk_ma200',target:'恒生'},
  ];

  let html='<div style="margin-bottom:14px">';

  // A股综合 + 恒生综合
  html+=`<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px">`;
  for(const [label,item] of [['🇨🇳 A股综合预测',a_share],['🇭🇰 恒生综合预测',hk_share]]){
    const pc=predCls(item.prediction);
    html+=`<div class="prediction-card">
      <div style="font-size:13px;color:var(--text2);margin-bottom:6px">${label}</div>
      <div style="display:flex;align-items:center;gap:8px">
        <span class="value ${pc}" style="font-size:22px;font-weight:700">${item.prediction||'--'}</span>
        <span class="${confCls(item.confidence)}" style="font-size:12px">${confIcon(item.confidence)} ${item.confidence||''}</span>
      </div>
      <div style="font-size:11px;color:var(--text2);margin-top:6px">${item.reasoning||''}</div>
    </div>`;
  }
  html+='</div>';

  // 6分项
  html+='<div class="section-title">📊 分项预测明细</div>';
  for(const row of subRows){
    const s=sub[row.key]||{};
    const pc=predCls(s.prediction);
    html+=`<div class="pred-row">
      <span class="pred-label">${s.reasoning||row.label}</span>
      <span style="display:flex;align-items:center;gap:6px">
        <span class="pred-value ${pc}">${s.prediction||'--'}</span>
        <span class="${confCls(s.confidence||'')}" style="font-size:11px">${confIcon(s.confidence||'')}</span>
      </span>
    </div>`;
  }
  html+='</div>';
  el.innerHTML=html;el.style.display='block';loading.style.display='none';
}

async function loadTimingBT(){
  const bt=_timingData?.black_thursday||{};
  const el=document.getElementById('tbt_content');
  const loading=document.getElementById('tbt_loading');
  if(!el)return;
  renderBT(bt);
  el.style.display='block';loading.style.display='none';
}

function renderBT(bt){
  const el=document.getElementById('tbt_content');
  if(!el)return;
  const sig=bt.signal;
  const sigCls=sig===1?'bull':sig===0?'bear':'mid';
  const sigLabel=bt.signal_desc||'--';
  let html=`
  <div class="stat-grid">
    <div class="stat"><div class="label">当前信号</div><div class="value ${sigCls}" style="font-size:18px">${sigLabel}</div></div>
    <div class="stat"><div class="label">市场状态</div><div class="value">${bt.market_type||'--'}</div></div>
    <div class="stat"><div class="label">年化收益</div><div class="value bull">${bt.annual||'--'}</div></div>
    <div class="stat"><div class="label">数据日期</div><div class="value" style="font-size:12px">${bt.data_date||'--'}</div></div>
  </div>`;

  const recent=bt.recent_signals||[];
  if(recent.length>0){
    html+=`<div class="section-title">📋 近${recent.length}次信号历史</div>`;
    html+=`<table><thead><tr><th>日期</th><th>信号</th><th>收益率</th><th>市场</th></tr></thead><tbody>`;
    for(const r of recent.slice(0,5)){
      const cls=r.signal==='看涨'?'bull':r.signal==='看跌'?'bear':'mid';
      html+=`<tr><td>${r.date}</td><td class="value ${cls}">${r.signal}</td><td>${r.return_pct||'--'}</td><td>${r.market||''}</td></tr>`;
    }
    html+='</tbody></table>';
  }
  el.innerHTML=html;
}

function renderRecentSignals(signals,label){
  if(!signals||!signals.length)return'';
  let html=`<div class="section-title">${label}近期翻转</div>`;
  for(const s of signals.slice(0,3)){
    const cls=s.signal&&s.signal.includes('看涨')?'bull':'bear';
    html+=`<div style="display:flex;justify-content:space-between;font-size:12px;padding:2px 0"><span>${s.date}</span><span class="value ${cls}">${s.signal||''}</span><span>${s.return_pct||''}</span></div>`;
  }
  return html;
}

async function fetchJSON(url){
  const r=await fetch(url);
  return r.json();
}

async function loadStocks(){
  document.getElementById('spinner').style.display='inline-block';
  try{
    const market=document.getElementById('marketSel').value;
    const r=await fetch(`/api/stocks?q=${market}`);
    _stocks=await r.json();
    renderStocks();
  }catch(e){console.error(e);}
  document.getElementById('spinner').style.display='none';
}

function renderStocks(){
  const q=(document.getElementById('stockSearch')?.value||'').toUpperCase();
  const sigFilter=document.getElementById('signalSel')?.value||'';
  let rows=_stocks;
  if(q)rows=rows.filter(s=>s.code.toUpperCase().includes(q)||(s.name||'').toUpperCase().includes(q));
  const el=document.getElementById('scanResults');
  if(!el)return;
  if(!rows.length){el.innerHTML='<div class="error">未找到股票，请先点击"执行扫描"</div>';return;}
  let html='';
  for(const s of rows.slice(0,60)){
    const sigs=s.signals||[];
    const cls=sigs.some(x=>x.includes('超卖')||x.includes('金叉'))?'bull':sigs.some(x=>x.includes('超买'))?'bear':'mid';
    const market=s.code.endsWith('.HK')?'HK':s.code.endsWith('.SS')?'SS':'SZ';
    html+=`<div class="card" style="cursor:pointer" onclick="showStockDetail('${s.code}')">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div><div style="font-weight:600;font-size:14px">${s.name||s.code}</div><div style="font-size:11px;color:var(--text2)">${s.code} · ${s.sector||'--'}</div></div>
        <div class="badge ${cls}">${sigs.length}信号</div>
      </div>
      <div class="signal-list">${sigs.slice(0,4).map(sg=>`<span class="signal-tag">${sg}</span>`).join('')}</div>
    </div>`;
  }
  el.innerHTML=html||'<div class="error">未找到股票</div>';
}

function showStockDetail(code){
  alert('股票详情: '+code+' (可扩展为详细分析页面)');
}

async function runScan(){
  document.getElementById('spinner').style.display='inline-block';
  document.getElementById('scanStatus').textContent='正在扫描…';
  try{
    const r=await fetch('/api/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})});
    _scanResults=await r.json();
    document.getElementById('scanStatus').textContent=`扫描完成: ${_scanResults.length} 只符合条件`;
    renderStocks();
  }catch(e){document.getElementById('scanStatus').textContent='扫描失败: '+e;}
  document.getElementById('spinner').style.display='none';
}

function exportCSV(){
  if(!_scanResults.length){alert('无数据可导出');return;}
  const rows=[['代码','名称','行业','信号','RSI','MACD','CCI','MA20','MA200','阶段']];
  for(const s of _scanResults){
    const snap=s.snapshot||{};
    rows.push([s.code,s.name,s.sector||'',(s.signals||[]).join(';'),snap.rsi14,snap.macd,snap.cci20,snap.ma20,snap.ma200,snap.stage]);
  }
  const csv=rows.map(r=>r.map(v=>`"${v}"`).join(',')).join('\\n');
  const blob=new Blob(['\\ufeff'+csv],{type:'text/csv;charset=utf-8'});
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');a.href=url;a.download='scan_results.csv';a.click();
  URL.revokeObjectURL(url);
}

async function runSEPA(){
  const stock=document.getElementById('sepaStock').value.trim();
  if(!stock){alert('请输入股票代码');return;}
  const el=document.getElementById('sepaResults');
  el.innerHTML='<div class="loading">SEPA分析中…</div>';
  try{
    const r=await fetch(`/api/sepa?code=${encodeURIComponent(stock)}`);
    const d=await r.json();
    renderSEPA(d);
  }catch(e){el.innerHTML=`<div class="error">分析失败: ${e}</div>`;}
}

function renderSEPA(d){
  const el=document.getElementById('sepaResults');
  if(!d||!d.snapshot){el.innerHTML='<div class="error">未找到数据</div>';return;}
  const s=d.snapshot;
  const signals=d.signals||[];
  el.innerHTML=`
  <div class="cards">
    <div class="card"><div class="card-title">${d.name||d.code} (${d.code})</div>
      <div class="section-title">价格与均线</div>
      <div class="kv"><span>当前价格</span><span class="v bull">${s.close}</span></div>
      <div class="kv"><span>MA5</span><span class="v">${s.ma5||'--'}</span></div>
      <div class="kv"><span>MA20</span><span class="v">${s.ma20||'--'}</span></div>
      <div class="kv"><span>MA50</span><span class="v">${s.ma50||'--'}</span></div>
      <div class="kv"><span>MA200</span><span class="v">${s.ma200||'--'}</span></div>
      <div class="section-title">技术指标</div>
      <div class="kv"><span>RSI(14)</span><span class="v ${s.rsi14<30?'bull':s.rsi14>70?'bear':'mid'}">${s.rsi14||'--'}</span></div>
      <div class="kv"><span>MACD</span><span class="v ${s.macd>0?'bull':'bear'}">${s.macd||'--'}</span></div>
      <div class="kv"><span>CCI(20)</span><span class="v ${s.cci20<-100?'bull':s.cci20>100?'bear':'mid'}">${s.cci20||'--'}</span></div>
      <div class="kv"><span>ATR(14)</span><span class="v">${s.atr14||'--'}</span></div>
      <div class="section-title">趋势状态</div>
      <div class="kv"><span>趋势阶段</span><span class="v">${s.stage||'--'}</span></div>
      <div class="kv"><span>趋势斜率</span><span class="v ${(s.slope20||0)>0?'bull':'bear'}">${s.slope20||'--'}</span></div>
      <div class="kv"><span>52周新高</span><span class="v">${s.near_52w_high?'是':'否'}</span></div>
    </div>
    <div class="card"><div class="card-title">触发信号 (${signals.length})</div>
      <div class="signal-list">${signals.map(sg=>`<span class="signal-tag">${sg}</span>`).join('')}</div>
      <div class="section-title">风险指标</div>
      <div class="kv"><span>波动率</span><span class="v">${s.bb_width?((s.bb_width)*100).toFixed(1)+'%':'--'}</span></div>
      <div class="kv"><span>量比</span><span class="v ${(s.vol_ratio||0)>2?'bull':''}">${s.vol_ratio||'--'}</span></div>
      <div class="kv"><span>Supertrend</span><span class="v">${s.supertrend_dir||'--'}</span></div>
      <div class="kv"><span>ADX</span><span class="v ${(s.adx||0)>25?'bull':''}">${s.adx||'--'}</span></div>
    </div>
  </div>`;
}

async function generateDailyReport(){
  const el=document.getElementById('reportContent');
  el.innerHTML='<div class="loading">正在生成报告…</div>';
  try{
    const r=await fetch('/api/generate_report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scan_results:_scanResults})});
    const html=await r.text();
    el.innerHTML=html;
  }catch(e){el.innerHTML=`<div class="error">生成失败: ${e}</div>`;}
}

function downloadReportImage(){
  alert('图片下载功能需要后端生成，请稍候…');
}

// 初始化
document.addEventListener('DOMContentLoaded',()=>{
  loadStocks();
  document.getElementById('lastUpdate').textContent=new Date().toLocaleString('zh-CN');
});
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════
# 图片报告生成
# ═══════════════════════════════════════════════════════

def generate_report_image(scan_results: List[Dict], timing_data: Dict, output_dir: str) -> Optional[str]:
    """用 PIL 生成深色主题图片报告"""
    if not PIL_AVAILABLE:
        return None
    try:
        W, H = 1200, 1600
        img = Image.new("RGB", (W, H), "#0f1117")
        draw = ImageDraw.Draw(img)

        font_paths = [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]
        font_large = None
        font_med = None
        font_small = None
        for fp in font_paths:
            if os.path.exists(fp):
                try:
                    font_large = ImageFont.truetype(fp, 36)
                    font_med = ImageFont.truetype(fp, 24)
                    font_small = ImageFont.truetype(fp, 18)
                    break
                except Exception:
                    continue

        if font_large is None:
            font_large = ImageFont.load_default()
            font_med = ImageFont.load_default()
            font_small = ImageFont.load_default()

        predictions = build_timing_predictions(timing_data)
        date_str = datetime.now().strftime("%Y-%m-%d")
        y = 20

        # Header
        draw.text((40, y), f"🐕 哮天每日收盘报告  {date_str}", fill="#e07c3e", font=font_large)
        y += 60

        # A股综合预测
        a_share = predictions.get("a_share", {})
        a_pred = a_share.get("prediction", "震荡")
        a_conf = a_share.get("confidence", "低置信")
        a_color = "#3ddc84" if a_pred == "看涨" else "#ff6b6b" if a_pred == "看跌" else "#888aa0"
        draw.text((40, y), f"🇨🇳 A股综合预测: {a_pred}  {a_conf}", fill=a_color, font=font_med)
        y += 45
        draw.text((40, y), f"   {a_share.get('reasoning', '')}", fill="#c5c9d8", font=font_small)
        y += 50

        # 恒生综合预测
        hk_share = predictions.get("hk_share", {})
        hk_pred = hk_share.get("prediction", "震荡")
        hk_conf = hk_share.get("confidence", "低置信")
        hk_color = "#3ddc84" if hk_pred == "看涨" else "#ff6b6b" if hk_pred == "看跌" else "#888aa0"
        draw.text((40, y), f"🇭🇰 恒生综合预测: {hk_pred}  {hk_conf}", fill=hk_color, font=font_med)
        y += 45
        draw.text((40, y), f"   {hk_share.get('reasoning', '')}", fill="#c5c9d8", font=font_small)
        y += 50

        # 分隔线
        draw.line([(40, y), (W - 40, y)], fill="#2a2f3e", width=2)
        y += 20

        # 择时摘要
        draw.text((40, y), "📊 择时摘要", fill="#e07c3e", font=font_med)
        y += 40

        timing_items = [
            ("沪深300 MA200", timing_data.get("hs300_ma200", {}).get("signal", "N/A")),
            ("恒生指数 MA200", timing_data.get("hk_hs300", {}).get("signal", "N/A")),
            ("北向资金", f"{timing_data.get('north_money',{}).get('signal','N/A')} ({timing_data.get('north_money',{}).get('cum_net',0):.1f}亿)"),
            ("南向资金", f"{timing_data.get('south_money',{}).get('signal','N/A')} ({timing_data.get('south_money',{}).get('cum_net',0):.1f}亿)"),
            ("VIX", str(timing_data.get("vix", {}).get("vix", "N/A"))),
            ("黑色星期四", timing_data.get("black_thursday", {}).get("signal_desc", "N/A")),
        ]
        for label, value in timing_items:
            draw.text((60, y), f"  {label}: {value}", fill="#c5c9d8", font=font_small)
            y += 30
            if y > H - 200:
                break

        # 分隔线
        draw.line([(40, y), (W - 40, y)], fill="#2a2f3e", width=2)
        y += 20

        # 选股扫描
        draw.text((40, y), f"📈 选股扫描 ({len(scan_results)} 只)", fill="#e07c3e", font=font_med)
        y += 40
        for r in scan_results[:15]:
            name = r.get("name", r.get("code", "?"))
            signals = (r.get("signals", []) or [])[:3]
            draw.text((60, y), f"  {name}: {', '.join(signals)}", fill="#c5c9d8", font=font_small)
            y += 28
            if y > H - 80:
                break

        # Footer
        draw.line([(40, H - 40), (W - 40, H - 40)], fill="#2a2f3e", width=1)
        draw.text((40, H - 30), "基于马克·米勒维尼趋势策略 · 数据仅供参考，不构成投资建议", fill="#8890a4", font=font_small)

        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, f"daily_report_{date_str}.png")
        img.save(out_path, "PNG")
        return out_path
    except Exception as e:
        import sys; sys.stderr.write(f"generate_report_image error: {e}\n")
        return None


# ═══════════════════════════════════════════════════════
# 启动入口
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="哮天每日收盘报告工具")
    parser.add_argument("--port", type=int, default=7878, help="服务端口 (默认 7878)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="监听地址")
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    args = parser.parse_args()

    print(f"🚀 启动哮天每日收盘报告服务...")
    print(f"   端口: {args.port}")
    print(f"   访问: http://localhost:{args.port}")
    print(f"   择时数据缓存: 30分钟")
    print(f"   按 Ctrl+C 停止")

    uvicorn.run(
        "daily_report_app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
