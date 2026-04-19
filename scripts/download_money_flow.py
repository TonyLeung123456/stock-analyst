#!/usr/bin/env python3
"""
南北向资金数据下载脚本
自动从 AKShare 获取数据并保存到指定目录
"""

import os
import pandas as pd
import akshare as ak

# 配置
data_dir = "/Users/tonyleung/.openclaw/agency-agents/stock-analyst/data"
north_dir = os.path.join(data_dir, "north")
south_dir = os.path.join(data_dir, "south")

# 创建目录
os.makedirs(north_dir, exist_ok=True)
os.makedirs(south_dir, exist_ok=True)

print("📥 开始下载南北向资金数据...")

# 1. 北向资金数据 (历史 + 今日)
try:
    print("   北向资金: 获取历史数据...")
    # 历史数据
    df_north_hist = ak.stock_hsgt_hist_em()
    if df_north_hist is not None and not df_north_hist.empty:
        # 处理数据 - 动态获取列名
        columns = df_north_hist.columns.tolist()
        print(f"   北向资金: 列名: {columns}")
        
        # 查找净流入列
        net_buy_col = None
        for col in columns:
            if "净买额" in col:
                net_buy_col = col
                break
        if not net_buy_col:
            for col in columns:
                if "净流入" in col:
                    net_buy_col = col
                    break
        
        if net_buy_col:
            df_north_hist = df_north_hist.rename(columns={
                columns[0]: "date",  # 第一列通常是日期
                net_buy_col: "net_buy"
            })
            df_north_hist = df_north_hist[["date", "net_buy"]]
            df_north_hist["date"] = pd.to_datetime(df_north_hist["date"])
            
            # 保存为 tushare_only.csv 格式
            north_csv = os.path.join(north_dir, "tushare_only.csv")
            df_north_hist.to_csv(north_csv, index=False)
            print(f"   北向资金: 历史数据已保存到 {north_csv}")
            print(f"   北向资金: 共 {len(df_north_hist)} 条记录")
        else:
            print("   北向资金: 未找到净流入列")
    else:
        print("   北向资金: 历史数据获取失败")
except Exception as e:
    print(f"   北向资金: 历史数据下载错误: {e}")

# 2. 南向资金数据 (历史 + 今日)
try:
    print("   南向资金: 获取历史数据...")
    # 历史数据 - 不使用 indicator 参数
    df_south_hist = ak.stock_hsgt_hist_em()
    if df_south_hist is not None and not df_south_hist.empty:
        # 处理数据 - 动态获取列名
        columns = df_south_hist.columns.tolist()
        print(f"   南向资金: 列名: {columns}")
        
        # 查找净流入列
        net_buy_col = None
        for col in columns:
            if "净流入" in col and "南向" in col:
                net_buy_col = col
                break
        if not net_buy_col:
            for col in columns:
                if "净流入" in col:
                    net_buy_col = col
                    break
        
        if net_buy_col:
            df_south_hist = df_south_hist.rename(columns={
                columns[0]: "date",  # 第一列通常是日期
                net_buy_col: "net_buy"
            })
            df_south_hist = df_south_hist[["date", "net_buy"]]
            df_south_hist["date"] = pd.to_datetime(df_south_hist["date"])
            
            # 保存
            south_csv = os.path.join(south_dir, "south_money_daily.csv")
            df_south_hist.to_csv(south_csv, index=False)
            print(f"   南向资金: 历史数据已保存到 {south_csv}")
            print(f"   南向资金: 共 {len(df_south_hist)} 条记录")
        else:
            print("   南向资金: 未找到净流入列")
    else:
        print("   南向资金: 历史数据获取失败")
except Exception as e:
    print(f"   南向资金: 历史数据下载错误: {e}")

# 3. 获取今日实时数据
try:
    print("   获取今日实时数据...")
    summary = ak.stock_hsgt_fund_flow_summary_em()
    if summary is not None and not summary.empty:
        print("   今日数据获取成功:")
        
        # 北向资金今日数据
        north_rows = summary[
            (summary["板块"].astype(str).isin(["沪股通", "深股通"])) &
            (summary["资金方向"] == "北向")
        ]
        north_today = 0.0
        for _, row in north_rows.iterrows():
            val = row.get("成交净买额", 0)
            if isinstance(val, (int, float)):
                north_today += float(val)
        print(f"   北向资金今日: {north_today:.2f} 亿元")
        
        # 南向资金今日数据
        south_rows = summary[
            (summary["板块"].astype(str).isin(["港股通(沪)", "港股通(深)"])) &
            (summary["资金方向"] == "南向")
        ]
        south_today = 0.0
        for _, row in south_rows.iterrows():
            val = row.get("成交净买额", 0)
            if isinstance(val, (int, float)):
                south_today += float(val)
        print(f"   南向资金今日: {south_today:.2f} 亿元")
    else:
        print("   今日数据获取失败")
except Exception as e:
    print(f"   今日数据获取错误: {e}")

print("✅ 数据下载完成！")
print("   数据已保存到:")
print(f"   - 北向资金: {north_dir}")
print(f"   - 南向资金: {south_dir}")