#!/usr/bin/env python3
"""
# 国内数据直连，不走系统代理（代理由ClashX管理，国际站走代理）
os.environ["http_proxy"] = ""
os.environ["https_proxy"] = ""
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""


下载 A 股 + 港股财务数据到本地存档，加速后续运行。

用法:
  python3 scripts/download_financial_data.py          # 下载全部（首次）
  python3 scripts/download_financial_data.py --update  # 增量更新（已有存档时）
  python3 scripts/download_financial_data.py --force    # 强制重新下载全部

输出:
  /Users/tonyleung/Downloads/股票/A股/财报/{code}.csv   # A 股财务（季度+年度深度）
  /Users/tonyleung/Downloads/股票/港股/财报/{code}.csv  # 港股财务（年报三表合并，2001年至今）

缓存策略:
  - 首次：下载全部股票
  - 更新：只重新下载距今超过 7 天的文件
  - 强制更新：加 --force
"""

import os, sys, time, json, argparse
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── 路径配置 ──────────────────────────────────────────────────────────────
BASE_HK   = "/Users/tonyleung/Downloads/股票/港股/Kline"
BASE_CN   = "/Users/tonyleung/Downloads/股票/A股/Kline"
FIN_CN    = "/Users/tonyleung/Downloads/股票/A股/财报"
FIN_HK    = "/Users/tonyleung/Downloads/股票/港股/财报"

os.makedirs(FIN_CN, exist_ok=True)
os.makedirs(FIN_HK, exist_ok=True)

# ── A 股字段（扩大深度：保留更多分析指标列）───────────────────────────────
CN_FIELDS = [
    # 日期
    'REPORT_DATE',
    # 营收
    'TOTALOPERATEREVE',      # 营业总收入
    'TOTALOPERATEREVETZ',     # 营业总收入同比增长
    'YYZSRGDHBZC',           # 营业收入滚动环比增长率
    # 净利润
    'PARENTNETPROFIT',       # 归母净利润
    'PARENTNETPROFITTZ',     # 归母净利润同比增长
    'KCFJCXSYJLR',          # 扣除非经常性损益净利润
    'KFJLRGDHBZC',          # 扣非净利润滚动环比增长率
    # EPS
    'EPSJB',                 # 基本每股收益
    'EPSXS',                 # 稀释每股收益
    # BPS
    'BPS',                   # 每股净资产
    # 盈利能力
    'ROEJQ',                # 加权净资产收益率
    'ROEKCJQ',              # 扣非净资产收益率
    'NETPROFITRPHBZC',      # 净利润率
    'MLR',                  # 主营业务利润率
    'XSMLL',                # 销售毛利率
    # 成长能力
    'DJD_TOI_YOY',          # 营业收入当季同比增长
    'DJD_DPNP_YOY',         # 归母净利润当季同比增长
    'DJD_TOI_QOQ',          # 营业收入当季环比增长
    'DJD_DPNP_QOQ',         # 归母净利润当季环比增长
    # 每股指标
    'MGZBGJ',               # 每股资本公积金
    'MGWFPLR',              # 每股未分配利润
    'MGJYXJJE',             # 每股经营现金流
    # 偿债能力
    'LD',                    # 流动负债
    'SD',                    # 长期负债
    'ZCFZL',                # 资产负债率
    'XJLLB',                # 现金流动负债比
    # 营运能力
    'YSZKZZTS',            # 应收账款周转天数
    'CHZZTS',              # 存货周转天数
    'TOAZZL',              # 总资产周转率
]

# ── 工具函数 ─────────────────────────────────────────────────────────────
def get_stock_list():
    """建立股票列表"""
    stocks = []
    # 港股
    for f in os.listdir(BASE_HK):
        if not f.endswith('.HK.csv'):
            continue
        code = f.replace('.HK.csv', '')
        path = f"{BASE_HK}/{f}"
        try:
            lines = open(path).readlines()
        except:
            continue
        if len(lines) < 50:
            continue
        stocks.append({'code': code, 'market': 'HK', 'file': path})
    # A股
    for f in os.listdir(BASE_CN):
        if not f.endswith('.csv'):
            continue
        code = f.replace('.SZ.csv', '').replace('.SS.csv', '')
        suffix = 'SZ' if f.endswith('.SZ.csv') else 'SH'
        path = f"{BASE_CN}/{f}"
        try:
            lines = open(path).readlines()
        except:
            continue
        if len(lines) < 50:
            continue
        stocks.append({'code': code, 'market': 'CN', 'suffix': suffix, 'file': path})
    return stocks

def should_update(fpath, max_age_days=7, force=False):
    """检查文件是否需要更新"""
    if force:
        return True
    if not os.path.exists(fpath):
        return True
    mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
    return (datetime.now() - mtime) > timedelta(days=max_age_days)

# ── A 股下载 ─────────────────────────────────────────────────────────────
def download_cn_financial(code, suffix='SH', force=False):
    """下载单个 A 股财务数据（按报告期），保存为 CSV"""
    fpath = f"{FIN_CN}/{code}.csv"
    if not should_update(fpath, force=force):
        return code, 'skip', None

    try:
        import akshare as ak
        symbol = f"{code}.{suffix}"
        # 使用"按报告期"获取季度数据（最完整，历史可追溯至1998年）
        df = ak.stock_financial_analysis_indicator_em(symbol=symbol, indicator='按报告期')
        if df is None or df.empty:
            return code, 'empty', None

        available = [c for c in CN_FIELDS if c in df.columns]
        df_sel = df[available].copy()
        # 按报告期倒序排列（最新在前）
        df_sel = df_sel.sort_values('REPORT_DATE', ascending=False)
        df_sel.to_csv(fpath, index=False, encoding='utf-8-sig')
        return code, 'ok', len(df_sel)
    except Exception as e:
        return code, 'error', str(e)[:80]

# ── 港股下载（深度版：三表合并年报，2001年至今）───────────────────────────
def download_hk_financial(code, force=False):
    """
    下载单个港股财务数据（年度三表合并版）。

    数据源：stock_financial_hk_report_em
    - 资产负债表（BS_）: 年报历史最早到 2001 年
    - 利润表    （IS_）: 年报历史最早到 2001 年
    - 现金流量表（CF_）: 年报历史最早到 2001 年

    相比旧版 analysis_indicator（仅 9 条，2017 年起），
    年报三表合并可追溯至 2001 年（约 24 年深度）。
    """
    fpath = f"{FIN_HK}/{code}.csv"
    if not should_update(fpath, force=force):
        return code, 'skip', None

    hk_code = code.zfill(5)

    try:
        import akshare as ak

        # 并发下载三张表
        results = {}
        with ThreadPoolExecutor(max_workers=3) as ex:
            futures = {
                ex.submit(_download_hk_statement, hk_code, stype): stype
                for stype in ['资产负债表', '利润表', '现金流量表']
            }
            for f in as_completed(futures):
                stype, df = f.result()
                results[stype] = df

        # 以 REPORT_DATE 为索引合并三张宽表
        dfs = []
        for stype, df_wide in results.items():
            if df_wide is not None and not df_wide.empty:
                dfs.append(df_wide)

        if not dfs:
            return code, 'empty', None

        # 依次 join（自动按 index=REPORT_DATE 对齐）
        merged = dfs[0]
        for df_wide in dfs[1:]:
            if df_wide is not None and not df_wide.empty:
                merged = merged.join(df_wide, how='outer')

        if merged is None or merged.empty:
            return code, 'empty', None

        # 按日期倒序
        merged = merged.sort_index(ascending=False)
        merged = merged.reset_index()
        merged.to_csv(fpath, index=False, encoding='utf-8-sig')
        return code, 'ok', len(merged)

    except Exception as e:
        return code, 'error', str(e)[:80]

def _download_hk_statement(hk_code, stype):
    """
    下载单张港股年度报表并转为宽表格式。
    原始数据为长表（每行=一个指标+一个金额），转为以 REPORT_DATE 为索引、
    STD_ITEM_NAME 为列的宽表，添加前缀区分报表类型。
    """
    prefix_map = {'资产负债表': 'BS_', '利润表': 'IS_', '现金流量表': 'CF_'}
    prefix = prefix_map.get(stype, stype[:2] + '_')

    try:
        import akshare as ak
        df = ak.stock_financial_hk_report_em(
            stock=hk_code, symbol=stype, indicator='年度'
        )
        if df is None or df.empty:
            return stype, None

        # 检查必要的列是否存在
        if 'REPORT_DATE' not in df.columns or 'STD_ITEM_NAME' not in df.columns or 'AMOUNT' not in df.columns:
            return stype, None

        # 长表 → 宽表：每行一个日期，每列一个指标
        df_wide = df.pivot_table(
            index='REPORT_DATE',
            columns='STD_ITEM_NAME',
            values='AMOUNT',
            aggfunc='first'
        )
        
        if df_wide is None or df_wide.empty:
            return stype, None
            
        # 添加前缀
        df_wide.columns = [prefix + c for c in df_wide.columns]
        return stype, df_wide

    except Exception as e:
        # 打印到 stderr，不吞掉错误
        import sys
        print(f'  ⚠️ 港股 {hk_code} {stype} 下载失败: {e}', file=sys.stderr)
        return stype, None

# ── 主流程 ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='下载财务数据到本地（深度版）')
    parser.add_argument('--update', action='store_true', help='增量更新（跳过7天内已下载）')
    parser.add_argument('--force', action='store_true', help='强制重新下载全部')
    parser.add_argument('--workers', type=int, default=5, help='并发数（默认5）')
    args = parser.parse_args()

    force = args.force
    stocks = get_stock_list()
    hk_list = [s for s in stocks if s['market'] == 'HK']
    print(f"股票列表: 港股 {len(hk_list)} 只")

    # ── 港股（年报三表合并） ──
    print(f"\n[1/1] 港股财务数据（年度三表合并，2001年至今）...")
    todo_hk = [s for s in hk_list if should_update(f"{FIN_HK}/{s['code']}.csv", force=force)]
    skip_hk = len(hk_list) - len(todo_hk)
    print(f"  待下载: {len(todo_hk)} | 跳过（已存在/新鲜）: {skip_hk}")

    ok_hk = fail_hk = empty_hk = 0
    for i in range(0, len(todo_hk), 200):
        batch = todo_hk[i:i+200]
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(download_hk_financial, s['code'], force): s
                       for s in batch}
            for f in as_completed(futures):
                code, status, nrows = f.result()
                if status == 'ok':   ok_hk += 1
                elif status == 'empty': empty_hk += 1
                else: fail_hk += 1
        print(f"  进度: {min(i+200, len(todo_hk))}/{len(todo_hk)}  |  成功:{ok_hk}  空:{empty_hk}  失败:{fail_hk}")
        time.sleep(0.3)

    # ── 摘要 ──
    print(f"\n{'='*50}")
    print(f"  港股: 成功 {ok_hk} | 空 {empty_hk} | 失败 {fail_hk}")
    print(f"  数据目录:")
    print(f"    港股: {FIN_HK}")
    print(f"{'='*50}")

if __name__ == '__main__':
    main()
