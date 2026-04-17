#!/usr/bin/env python3
"""
# 国内数据直连，不走系统代理（代理由ClashX管理，国际站走代理）
os.environ["http_proxy"] = ""
os.environ["https_proxy"] = ""
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""


南向资金择时策略（恒生指数 + 恒生科技指数）
逻辑：N日累计南向净流入 > Z日均线 → 持有对应指数，否则空仓

参数（可直接修改）：
  SHORT      = 5     # 短期窗口（累计天数）
  MEDIAN     = 20    # 中期均线窗口
  THRESHOLD  = 0.0   # 累计 > 均线*(1+THRESHOLD) 才触发买入
  BENCHMARK  = "BOTH" # "HS" | "HSTECH" | "BOTH"
  LOOKBACK_START = ""  # 回测起始日期，如 "2026-01-01"（留空则从头开始）
  LOOKBACK_END   = ""  # 回测截止日期，如 "2026-12-31"（留空则到最新）
"""

import os, sys, json, requests
import pandas as pd
import numpy as np

# ── 可调参数 ─────────────────────────────────────────────────────────────────
SHORT          = 5
MEDIAN         = 20
THRESHOLD      = 0.0
BENCHMARK      = "BOTH"
LOOKBACK_START = "2026-01-01"   # 留空则从头 ""，如 "2026-01-01"
LOOKBACK_END   = ""              # 留空则到最新，如 "2026-12-31"
DATA_DIR       = os.path.expanduser("~/.openclaw/Downloads/股票/南向资金")
OUTPUT_DIR     = os.path.expanduser("~/.openclaw/Downloads/股票/择时回测")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# 1. 加载南向资金数据
# ─────────────────────────────────────────────────────────────────────────────
south_file = os.path.join(DATA_DIR, "south_money_daily.csv")

import akshare as ak
if not os.path.exists(south_file):
    print("📥 下载南向资金历史数据（akshare）...")
    df_s = ak.stock_hsgt_hist_em(symbol="南向资金")
    df_s = df_s.dropna(subset=["当日成交净买额"]).copy()
    df_s = df_s.rename(columns={
        "日期":"date","当日成交净买额":"net_buy","买入成交额":"buy_amt",
        "卖出成交额":"sell_amt","历史累计净买额":"cum_net_buy",
        "当日资金流入":"daily_inflow","当日余额":"daily_balance",
        "持股市值":"hold_mkt_cap","沪深300":"hs_index","沪深300-涨跌幅":"hs_chg",
    })
    df_s["date"] = pd.to_datetime(df_s["date"]).dt.strftime("%Y-%m-%d")
    df_s["hs_index"] = pd.to_numeric(df_s["hs_index"], errors="coerce")
    df_s = df_s[["date","net_buy","buy_amt","sell_amt","hs_index","hs_chg"]]
    df_s.to_csv(south_file, index=False, float_format="%.4f")
    print(f"   南向资金已保存: {len(df_s)} 行")

df_s = pd.read_csv(south_file, parse_dates=["date"])
print(f"📊 南向资金: {len(df_s)} 行, {df_s['date'].iloc[0].date()} ~ {df_s['date'].iloc[-1].date()}")

# ─────────────────────────────────────────────────────────────────────────────
# 2. 恒生指数（腾讯财经 API）
# ─────────────────────────────────────────────────────────────────────────────
def get_tencent_index(code):
    """腾讯财经 K线格式: [date, open, close, high, low]"""
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?_var=kline_dayqfq&param={code},day,2024-01-01,2026-12-31,2000,qfq")
    r = requests.get(url, headers={"User-Agent":"Mozilla/5.0","Referer":"https://gu.qq.com/"}, timeout=10)
    data = json.loads(r.text.replace("kline_dayqfq=", ""))
    key = list(data["data"].keys())[0]
    days = data["data"][key]["day"]
    rows = []
    for d in days:
        rows.append({
            "date":  d[0],
            "open":  float(d[1]),
            "close": float(d[2]),
            "high":  float(d[3]),
            "low":   float(d[4]),
        })
    return pd.DataFrame(rows)

hsi_file = os.path.join(DATA_DIR, "hsi_daily.csv")
if not os.path.exists(hsi_file):
    print("📥 从腾讯财经获取恒生指数...")
    df_hs = get_tencent_index("hkHSI")
    df_hs["date"] = pd.to_datetime(df_hs["date"])
    df_hs.to_csv(hsi_file, index=False)
    print(f"   恒生指数: {len(df_hs)} 行, {df_hs['date'].iloc[0].date()} ~ {df_hs['date'].iloc[-1].date()}")
else:
    df_hs = pd.read_csv(hsi_file, parse_dates=["date"])
    print(f"📈 恒生指数: {len(df_hs)} 行, {df_hs['date'].iloc[0].date()} ~ {df_hs['date'].iloc[-1].date()}")

# ─────────────────────────────────────────────────────────────────────────────
# 3. 恒生科技指数（腾讯财经 API）
# ─────────────────────────────────────────────────────────────────────────────
def get_hstech():
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           "?_var=kline_dayqfq&param=hkHSTECH,day,2024-01-01,2026-12-31,2000,qfq")
    r = requests.get(url, headers={"User-Agent":"Mozilla/5.0","Referer":"https://gu.qq.com/"}, timeout=10)
    data = json.loads(r.text.replace("kline_dayqfq=", ""))
    days = data["data"]["hkHSTECH"]["day"]
    rows = []
    for d in days:
        rows.append({
            "date":  d[0],
            "close": float(d[2]),
            "high":  float(d[3]),
            "low":   float(d[4]),
        })
    return pd.DataFrame(rows)

hstech_file = os.path.join(DATA_DIR, "hstech_daily.csv")
if not os.path.exists(hstech_file):
    print("📥 从腾讯财经获取恒生科技指数...")
    try:
        df_hstech = get_hstech()
        df_hstech["date"] = pd.to_datetime(df_hstech["date"])
        # 保存时转字符串，读回时自动 parse_dates
        df_hstech_save = df_hstech.copy()
        df_hstech_save["date"] = df_hstech_save["date"].dt.strftime("%Y-%m-%d")
        df_hstech_save.to_csv(hstech_file, index=False)
        print(f"   恒生科技: {len(df_hstech)} 行, {df_hstech['date'].iloc[0].date()} ~ {df_hstech['date'].iloc[-1].date()}")
    except Exception as e:
        print(f"   ⚠️ 恒生科技获取失败: {e}")
        df_hstech = pd.DataFrame()
else:
    df_hstech = pd.read_csv(hstech_file, parse_dates=["date"])

if len(df_hstech) > 0:
    print(f"📈 恒生科技: {len(df_hstech)} 行, {df_hstech['date'].iloc[0].date()} ~ {df_hstech['date'].iloc[-1].date()}")

# ─────────────────────────────────────────────────────────────────────────────
# 4. 计算南向资金信号
# ─────────────────────────────────────────────────────────────────────────────
df_s["cum_net"] = df_s["net_buy"].rolling(window=SHORT, min_periods=SHORT).sum()
df_s["ma_net"]  = df_s["net_buy"].rolling(window=MEDIAN, min_periods=MEDIAN).mean() * SHORT
df_s["signal"]  = (df_s["cum_net"] > df_s["ma_net"] * (1 + THRESHOLD)).astype(int)

# ─────────────────────────────────────────────────────────────────────────────
# 5. 回测函数
# ─────────────────────────────────────────────────────────────────────────────
def calc(ret):
    if len(ret) == 0 or ret.isna().all():
        return {}
    cum   = (1 + ret).prod() - 1
    n     = len(ret)
    ann   = (1 + cum) ** (252 / n) - 1 if n > 0 and cum > -1 else -1
    std   = ret.std()
    sharpe = ret.mean() / std * np.sqrt(252) if std > 0 else 0
    vol   = std * np.sqrt(252)
    carr  = (1 + ret).cumprod()
    peak  = carr.cummax()
    dd    = (carr - peak) / peak
    mdd   = dd.min()
    wins  = (ret > 0).sum()
    loss  = (ret < 0).sum()
    wr    = wins / (wins + loss) * 100 if (wins + loss) > 0 else 0
    return dict(cum=cum, ann=ann, sharpe=sharpe, vol=vol, mdd=mdd, wr=wr,
                 wins=wins, losses=loss, hold=int((ret != 0).sum()), total=len(ret))

def backtest(df_price, label):
    # 确保 date 格式一致
    dp = df_price.copy()
    dp["date"] = pd.to_datetime(dp["date"])
    ds = df_s.copy()
    ds["date"] = pd.to_datetime(ds["date"])
    dp = dp[["date","close"]]
    dp = dp.dropna(subset=["close"])
    dp = dp.sort_values("date").reset_index(drop=True)

    dfm = pd.merge(ds[["date","net_buy","cum_net","ma_net","signal"]], dp, on="date", how="inner")
    dfm = dfm.sort_values("date").reset_index(drop=True)

    # ── 回测区间过滤（先过滤，再计算收益率）─────────────────────────────────
    if LOOKBACK_START:
        dfm = dfm[dfm["date"] >= pd.to_datetime(LOOKBACK_START)].reset_index(drop=True)
    if LOOKBACK_END:
        dfm = dfm[dfm["date"] <= pd.to_datetime(LOOKBACK_END)].reset_index(drop=True)

    # 收益率必须在区间过滤后再算
    # 基准收益：用区间前最近的实际交易日收盘价作为基准起点
    first_close = dfm["close"].iloc[0]
    first_dt    = dfm["date"].iloc[0]
    # 用指数自身的完整历史数据（df_price）找区间前的最近收盘价
    dp_all = df_price.copy()
    dp_all["date"] = pd.to_datetime(dp_all["date"])
    dp_all = dp_all.sort_values("date").reset_index(drop=True)
    prev_rows = dp_all[dp_all["date"] < first_dt]
    if len(prev_rows) > 0:
        prev_close   = prev_rows["close"].iloc[-1]
        bm_first_ret = first_close / prev_close - 1
    else:
        prev_close   = first_close
        bm_first_ret = 0.0

    dfm["position"] = dfm["signal"].shift(1).fillna(0)
    dfm["ret"]      = dfm["close"].pct_change()
    # 第一天用区间前的基准计算收益率
    dfm.loc[dfm.index[0], "ret"] = bm_first_ret
    dfm["strategy"] = dfm["position"] * dfm["ret"]
    # 去除信号尚未稳定的行（rolling 窗口未满 MEDIAN 天）
    dfm = dfm[dfm["signal"].notna()].reset_index(drop=True)
    if len(dfm) == 0:
        return {}, pd.DataFrame()

    s = calc(dfm["strategy"])
    b = calc(dfm["ret"])
    last = dfm.iloc[-1]
    sig = "🟢 看涨（持有）" if last["position"] == 1 else "🔴 看跌（空仓）"

    return {
        "label":        label,
        "period":       f"{dfm['date'].iloc[0].date()} ~ {dfm['date'].iloc[-1].date()}",
        "days":         len(dfm),
        "sig":          sig,
        "latest_date":  last["date"].date(),
        "latest_close": last["close"],
        "net_buy":      last["net_buy"],
        "cum_net":      last["cum_net"],
        "ma_net":       last["ma_net"],
        **s,
        "bm_ann": b.get("ann",0),
        "bm_mdd": b.get("mdd",0),
        "bm_wr":  b.get("wr",0),
        "bm_cum": b.get("cum",0),
    }, dfm

# ─────────────────────────────────────────────────────────────────────────────
# 6. 运行回测
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 65)
lb = f"  回溯区间={LOOKBACK_START or '起始'}~{LOOKBACK_END or '最新'}" if (LOOKBACK_START or LOOKBACK_END) else ""
print(f"  南向资金择时策略  |  短期={SHORT}天  中期={MEDIAN}天  阈值={THRESHOLD*100:.0f}%{lb}")
print("=" * 65)

results = {}
all_df  = {}

if BENCHMARK in ("HS", "BOTH"):
    m, df = backtest(df_hs, "恒生指数")
    if m:
        results["HS"]  = m
        all_df["HS"]   = df

if BENCHMARK in ("HSTECH", "BOTH"):
    if len(df_hstech) > 0:
        m, df = backtest(df_hstech, "恒生科技指数")
        if m:
            results["HSTECH"] = m
            all_df["HSTECH"]  = df
    else:
        print("⚠️ 恒生科技无数据，跳过")

for key, r in results.items():
    label = r["label"]
    print()
    print(f"  ┌─ {label} {'─' * max(0, 54 - len(label))}┐")
    print(f"  │ 回测区间 : {r['period']}  ({r['days']} 天)         │")
    print(f"  │ 持仓天数 : {r['hold']} 天 ({r['hold']/r['total']*100:.1f}%)                     │")
    print(f"  ├─────────────┬──────────┬──────────┬──────────┤")
    print(f"  │             │   策略   │ 买入持有 │   差异   │")
    print(f"  │  区间涨跌    │ {r['cum']*100:>+7.2f}% │ {r['bm_cum']*100:>+7.2f}% │ {(r['cum']-r['bm_cum'])*100:>+7.2f}% │")
    print(f"  │    年化收益    │ {r['ann']*100:>+7.2f}% │ {r['bm_ann']*100:>+7.2f}% │ {(r['ann']-r['bm_ann'])*100:>+7.2f}% │")
    print(f"  │ {'夏普比率':^10} │ {r['sharpe']:>+8.2f}  │    {'—':^6}  │    {'—':^6}  │")
    print(f"  │ {'年化波动':^10} │ {r['vol']*100:>+7.2f}% │    {'—':^6}  │    {'—':^6}  │")
    print(f"  │ {'最大回撤':^10} │ {r['mdd']*100:>+7.2f}% │ {r['bm_mdd']*100:>+7.2f}% │ {(r['mdd']-r['bm_mdd'])*100:>+7.2f}% │")
    print(f"  │ {'胜率':^10} │ {r['wr']:>+7.1f}% │ {r['bm_wr']:>+7.1f}% │ {r['wr']-r['bm_wr']:>+7.1f}% │")
    print(f"  ├─────────────┴──────────┴──────────┴──────────┤")
    print(f"  │ ⏱️ 最新信号 : {r['sig']}  ({r['latest_date']})           │")
    print(f"  │     最新收盘 : {r['latest_close']:.2f}                             │")
    print(f"  │     今日南向 : {r['net_buy']:+,.2f}亿  {SHORT}日累计:{r['cum_net']:+,.2f}亿  {MEDIAN}日均线:{r['ma_net']:+,.2f}亿  │")
    print(f"  └{'─'*62}┘")

# ─────────────────────────────────────────────────────────────────────────────
# 7. 导出 CSV
# ─────────────────────────────────────────────────────────────────────────────
if results:
    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    for key, df in all_df.items():
        df_out = df[["date","close","net_buy","cum_net","ma_net",
                      "signal","position","ret","strategy"]].copy()
        df_out["cum_strategy"] = (1 + df["strategy"]).cumprod().values
        df_out["cum_benchmark"]= (1 + df["ret"]).cumprod().values
        carr = (1 + df["strategy"]).cumprod()
        df_out["drawdown"]    = ((carr - carr.cummax()) / carr.cummax()).values
        lbl  = "HS" if key == "HS" else "HSTECH"
        lb_tag = f"_{LOOKBACK_START or 'start'}_{LOOKBACK_END or 'end'}" if (LOOKBACK_START or LOOKBACK_END) else ""
        latest  = os.path.join(OUTPUT_DIR, f"south_timing_{lbl}_latest.csv")
        version = os.path.join(OUTPUT_DIR, f"south_timing_{lbl}_S{SHORT}_M{MEDIAN}{lb_tag}_{ts}.csv")
        df_out.to_csv(latest,  index=False, float_format="%.6f")
        df_out.to_csv(version, index=False, float_format="%.6f")
        print(f"\n📁 导出 {lbl}: {latest}")
        print(f"   版本文件: {version}  ({len(df_out)} 行)")
