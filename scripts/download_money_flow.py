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
            # 过滤掉 net_buy 为空的行
            df_north_hist = df_north_hist.dropna(subset=["net_buy"])
            
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
    # 历史数据 - 需要传入 symbol='南向资金'
    df_south_hist = ak.stock_hsgt_hist_em(symbol='南向资金')
    if df_south_hist is not None and not df_south_hist.empty:
        # 处理数据 - 动态获取列名
        columns = df_south_hist.columns.tolist()
        print(f"   南向资金: 列名: {columns}")
        
        # 查找净买额列
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
            print("   南向资金: 未找到净买额列")
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
        
        # 将今日数据添加到历史数据中
        today = pd.Timestamp.now().strftime("%Y-%m-%d")
        
        # 更新北向资金数据
        north_csv = os.path.join(north_dir, "tushare_only.csv")
        if os.path.exists(north_csv):
            df_north = pd.read_csv(north_csv)
            # 检查今日数据是否已存在
            if not (df_north["date"] == today).any():
                # 添加今日数据
                new_row = pd.DataFrame({"date": [today], "net_buy": [north_today * 10000]})  # 转换为万元
                df_north = pd.concat([df_north, new_row], ignore_index=True)
                # 按日期排序
                df_north = df_north.sort_values("date").reset_index(drop=True)
                # 保存
                df_north.to_csv(north_csv, index=False)
                print(f"   北向资金今日数据已添加到历史记录")
        
        # 更新南向资金数据
        if south_today != 0:
            south_csv = os.path.join(south_dir, "south_money_daily.csv")
            if os.path.exists(south_csv):
                df_south = pd.read_csv(south_csv)
                # 检查今日数据是否已存在
                if not (df_south["date"] == today).any():
                    # 添加今日数据
                    new_row = pd.DataFrame({"date": [today], "net_buy": [south_today]})
                    df_south = pd.concat([df_south, new_row], ignore_index=True)
                    # 按日期排序
                    df_south = df_south.sort_values("date").reset_index(drop=True)
                    # 保存
                    df_south.to_csv(south_csv, index=False)
                    print(f"   南向资金今日数据已添加到历史记录")
    else:
        print("   今日数据获取失败")
except Exception as e:
    print(f"   今日数据获取错误: {e}")

print("✅ 数据下载完成！")
print("   数据已保存到:")
print(f"   - 北向资金: {north_dir}")
print(f"   - 南向资金: {south_dir}")