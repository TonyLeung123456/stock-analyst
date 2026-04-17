#!/usr/bin/env python3
"""
# 国内数据直连，不走系统代理（代理由ClashX管理，国际站走代理）
os.environ["http_proxy"] = ""
os.environ["https_proxy"] = ""
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""


北向资金择时策略（沪深300）
逻辑：N日累计北向净流入 > N日均线（Z日基准）→ 看涨持有，否则空仓

参数（可直接修改）：
  SHORT   = 5     # 短期窗口（累计天数）
  MEDIAN  = 20    # 均线窗口（Z日均值作为比较基准）
  THRESHOLD = 0.0 # 累计 > 均线*(1+THRESHOLD) 才触发买入
"""

import os, sys
import pandas as pd
import numpy as np

# ── 可调参数 ─────────────────────────────────────────────────────────────────
SHORT      = 5      # 短期累计窗口（天）
MEDIAN     = 20     # 中期均线窗口（天），作为比较基准
THRESHOLD  = 0.0    # 触发阈值：cum > ma*(1+THRESHOLD)
DATA_DIR   = os.path.expanduser("~/.openclaw/Downloads/股票/北向资金")
OUTPUT_DIR = os.path.expanduser("~/.openclaw/Downloads/股票/择时回测")
EXPORT_CSV = True

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── 1. 加载北向资金数据 ──────────────────────────────────────────────────────
north_file = os.path.join(DATA_DIR, "north_money_daily.csv")
if not os.path.exists(north_file):
    print(f"❌ 找不到北向资金数据: {north_file}")
    print("   请先运行: python3 scripts/north_money_downloader.py")
    sys.exit(1)

df_n = pd.read_csv(north_file, parse_dates=["date"])
df_n = df_n.sort_values("date").reset_index(drop=True)
print(f"📊 北向资金: {len(df_n)} 行, {df_n['date'].iloc[0].date()} ~ {df_n['date'].iloc[-1].date()}")

# ── 2. 计算信号 ──────────────────────────────────────────────────────────────
# 短期N日累计净流入
df_n["cum_net"] = df_n["net_buy"].rolling(window=SHORT, min_periods=SHORT).sum()
# 中期Z日均线（作为判断基准）
df_n["ma_net"]  = df_n["net_buy"].rolling(window=MEDIAN, min_periods=MEDIAN).mean() * SHORT
# 信号：短期累计 > 中期均线 → 持有（次日生效）
df_n["signal"]  = (df_n["cum_net"] > df_n["ma_net"] * (1 + THRESHOLD)).astype(int)

print(f"   有效信号起始日: {df_n[df_n['signal'].notna()]['date'].iloc[0].date()}")
print(f"   信号触发率: {df_n['signal'].mean()*100:.1f}%  (持有天数占比)")

# ── 3. 获取沪深300 ────────────────────────────────────────────────────────────
import akshare as ak
print("📥 从 akshare 获取沪深300 (sh000300)...")
df_hs = ak.stock_zh_index_daily(symbol="sh000300")
df_hs = df_hs.reset_index()[["date","close"]]
df_hs["date"] = pd.to_datetime(df_hs["date"])
print(f"   成功: {len(df_hs)} 行, {df_hs['date'].iloc[0].date()} ~ {df_hs['date'].iloc[-1].date()}")

# ── 4. 合并 ──────────────────────────────────────────────────────────────────
df = pd.merge(df_n[["date","net_buy","cum_net","ma_net","signal"]],
              df_hs[["date","close"]], on="date", how="inner")
df = df.sort_values("date").reset_index(drop=True)
df["position"] = df["signal"].shift(1).fillna(0)   # 信号次日生效
df["ret"]      = df["close"].pct_change()
df["strategy"] = df["position"] * df["ret"]
df = df.dropna(subset=["ret"]).reset_index(drop=True)

# 去除信号不稳定的前 MEDIAN 天
df = df[df["signal"].notna()].reset_index(drop=True)
print(f"回测区间: {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()}, {len(df)} 天")

# ── 5. 核心指标 ───────────────────────────────────────────────────────────────
def calc_metrics(ret_series, label=""):
    cum     = (1 + ret_series).prod() - 1
    ann     = cum ** (252 / len(ret_series)) - 1 if cum > -1 else -1
    sharpe  = ret_series.mean() / ret_series.std() * np.sqrt(252) if ret_series.std() > 0 else 0
    vol     = ret_series.std() * np.sqrt(252)
    cum_arr = (1 + ret_series).cumprod()
    peak    = cum_arr.cummax()
    dd      = (cum_arr - peak) / peak
    mdd     = dd.min()
    wins    = (ret_series > 0).sum()
    losses  = (ret_series < 0).sum()
    wr      = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
    return cum, ann, sharpe, vol, mdd, wr, wins, losses

s_cum, s_ann, s_sharpe, s_vol, s_mdd, s_wr, s_wins, s_losses = calc_metrics(df["strategy"])
b_cum, b_ann, b_sharpe, b_vol, b_mdd, b_wr, _,  _             = calc_metrics(df["ret"])

# 最新信号
last    = df.iloc[-1]
hold    = int(df["position"].sum())
total   = len(df)
sig_txt = "🟢 看涨（持有）" if last["position"] == 1 else "🔴 看跌（空仓）"

# ── 6. 打印结果 ──────────────────────────────────────────────────────────────
print()
print("=" * 60)
print(f"  北向资金择时策略  |  短期={SHORT}天  中期={MEDIAN}天  阈值={THRESHOLD*100:.0f}%")
print("=" * 60)
print(f"  回测区间   : {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()} ({len(df)} 天)")
print(f"  持仓天数   : {hold} 天 ({hold/total*100:.1f}%)")
print()
print(f"  ┌{'─'*46}┐")
print(f"  │ {'年化收益':12s} {s_ann*100:>+9.2f}% {b_ann*100:>+9.2f}% {((s_ann-b_ann)*100):>+9.2f}% │")
print(f"  │ {'夏普比率':12s} {s_sharpe:>+10.2f} {b_sharpe:>+10.2f} {s_sharpe-b_sharpe:>+10.2f} │")
print(f"  │ {'年化波动':12s} {s_vol*100:>+9.2f}% {b_vol*100:>+9.2f}% {((s_vol-b_vol)*100):>+9.2f}% │")
print(f"  │ {'最大回撤':12s} {s_mdd*100:>+9.2f}% {b_mdd*100:>+9.2f}% {((s_mdd-b_mdd)*100):>+9.2f}% │")
print(f"  │ {'胜率':12s} {s_wr:>+9.1f}% {b_wr:>+9.1f}% {s_wr-b_wr:>+9.1f}% │")
print(f"  └{'─'*46}┘")
print()
print(f"  ⏱️  最新信号   : {sig_txt}  ({last['date'].date()})")
print(f"     最新收盘    : {last['close']:.2f}")
print(f"     今日北向    : {last['net_buy']:+,.2f} 亿")
print(f"     {SHORT}日累计      : {last['cum_net']:+,.2f} 亿")
print(f"     {MEDIAN}日均线    : {last['ma_net']:+,.2f} 亿")
print(f"     信号差值    : {last['cum_net']-last['ma_net']:+,.2f} 亿")
print("=" * 60)

# ── 7. 导出 CSV ──────────────────────────────────────────────────────────────
if EXPORT_CSV:
    ts  = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    out = df[["date","close","net_buy","cum_net","ma_net",
              "signal","position","ret","strategy"]].copy()
    out["cum_strategy"] = (1 + df["strategy"]).cumprod().values
    out["cum_benchmark"]= (1 + df["ret"]).cumprod().values
    cum_arr = (1 + df["strategy"]).cumprod()
    out["drawdown"]     = ((cum_arr - cum_arr.cummax()) / cum_arr.cummax()).values

    latest_csv = os.path.join(OUTPUT_DIR, "north_timing_latest.csv")
    out.to_csv(latest_csv, index=False, float_format="%.6f")

    ver_csv = os.path.join(OUTPUT_DIR, f"north_timing_S{SHORT}_M{MEDIAN}_{ts}.csv")
    out.to_csv(ver_csv, index=False, float_format="%.6f")
    print(f"\n📁 最新结果: {latest_csv}")
    print(f"📁 版本文件: {ver_csv}")
    print(f"   共 {len(out)} 行")
