#!/usr/bin/env python3
"""
股票 K 线数据下载器（支持 A 股、港股）
- 数据源: 腾讯财经 API
- 自动识别市场：沪市(.SS)、深市(.SZ)、港股(.HK)
- 支持从文件批量读取股票代码
- 支持增量更新：对比本地 CSV，只下载缺少的日期
- CSV 格式: date,symbol,currency,open,high,low,close,volume
- 腾讯格式: date,open,close,high,low,volume（复权调整后，open在前）

用法:
    python kline_downloader.py 0700.HK 0005.HK 2318.HK -d 365
    python kline_downloader.py -i hk_list.csv -o ~/hk_data
    python kline_downloader.py 600519.SS 000001.SZ 300750.SZ -d 365
    python kline_downloader.py -i cn_list.csv -o ~/cn_data --force
"""

import csv
import os
import time
import datetime
import argparse
import ssl
import json
import urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── 关键：macOS 系统代理指向 127.0.0.1:7890（Clash）但无响应
# Python 3.x 用 _scproxy 读 macOS 系统配置，与环境变量无关，必须 patch
try:
    import _scproxy
    _scproxy._get_proxies = lambda: {}
except ImportError:
    pass

FIELDNAMES = ['date', 'symbol', 'currency', 'open', 'close', 'high', 'low', 'volume']

# 腾讯财经 API
_TEGENT_BASE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def _build_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _tq_code(symbol: str) -> str:
    """股票代码 → 腾讯代码前缀"""
    is_hk = symbol.endswith('.HK')
    is_ss = symbol.endswith('.SS')
    is_sz = symbol.endswith('.SZ')

    if is_hk:
        # 港股：5位码，如 00700 → hk00700
        return 'hk' + symbol.replace('.HK', '').zfill(5)
    elif is_ss:
        # 沪市：sh600519
        return 'sh' + symbol.replace('.SS', '')
    elif is_sz:
        # 深市：sz000001
        return 'sz' + symbol.replace('.SZ', '')
    return ''


def _fetch_kline(symbol: str, start: str, end: str) -> list[dict]:
    """从腾讯财经拉 K 线数据"""
    tq_code = _tq_code(symbol)
    if not tq_code:
        return []

    url = f"{_TEGENT_BASE}?_var=kline_dayhfq&param={tq_code},day,{start},{end},1000,qfq"
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
            'Referer': 'https://gu.qq.com/',
            'Accept': '*/*',
        }
    )
    ctx = _build_ssl_context()
    with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
        raw = resp.read().decode('utf-8')

    # 去掉 javascript 变量前缀
    raw = raw.replace('kline_dayhfq=', '', 1)
    data = json.loads(raw)

    tq_data = data.get('data', {}).get(tq_code, {})
    # 港股: day 数组；A 股（前复权）: qfqday 数组
    klines = tq_data.get('day') or tq_data.get('qfqday') or []
    if not klines:
        return []

    is_hk = symbol.endswith('.HK')
    currency = 'HKD' if is_hk else 'CNY'

    rows = []
    for item in klines:
        if not isinstance(item, list) or len(item) < 6:
            continue
        date_str, open_s, close_s, high_s, low_s, vol_s = item[0], item[1], item[2], item[3], item[4], item[5]
        rows.append({
            'date': date_str,
            'symbol': symbol,
            'currency': currency,
            'open': round(float(open_s), 2),
            'close': round(float(close_s), 2),
            'high': round(float(high_s), 2),
            'low': round(float(low_s), 2),
            'volume': int(float(vol_s)),
        })
    rows.sort(key=lambda r: r['date'])
    return rows


def _last_trading_day():
    """计算最近一个交易日。如果今天是非交易日，返回上一个交易日；如果是交易日，返回今天本身"""
    today = datetime.date.today()
    wd = today.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun
    if wd == 5:  # Saturday → Friday (1 day ago)
        delta = 1
    elif wd == 6:  # Sunday → Friday (2 days ago)
        delta = 2
    else:  # Mon-Fri → today (if it's a trading day, we want today's data)
        delta = 0
    return (today - datetime.timedelta(days=delta)).strftime('%Y-%m-%d')


def normalize_symbol(code: str) -> str:
    """
    标准化股票代码

    识别规则：
        港股 5位码   00700 → 00700.HK（保持5位）
        港股 4位码   0700  → 00700.HK
        沪市 6位码   600xxx/601xxx/603xxx/605xxx/688xxx → 600xxx.SS
        深市 6位码   000xxx/001xxx/002xxx/003xxx/300xxx/301xxx → 000xxx.SZ
        已有后缀     .HK / .SS / .SZ 直接返回（港股保持5位）
    """
    code = code.strip().replace(' ', '')
    code = code.translate(str.maketrans('０１２３４５６７８９', '0123456789'))

    for suffix in ('.HK', '.hk', '.Hk', '.SS', '.ss', '.SZ', '.sz'):
        if code.endswith(suffix):
            return code.upper()

    if code.isdigit() and len(code) in (4, 5):
        return code.zfill(5) + '.HK'

    if code.isdigit() and len(code) == 6:
        prefix = code[:3]
        if prefix in ('600', '601', '603', '605', '688'):
            return code + '.SS'
        elif prefix in ('000', '001', '002', '003', '300', '301'):
            return code + '.SZ'
        return ''

    return ''


def read_stock_list(path: Path) -> list[str]:
    """从文件读取股票代码列表（跳过空行和 # 注释）"""
    symbols = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            code = line.split('，')[0].split(',')[0].strip()
            normalized = normalize_symbol(code)
            if normalized:
                symbols.append(normalized)
            else:
                print(f"  ⚠️ 跳过无效代码: {code}")
    return symbols


def load_existing_dates(csv_path: Path) -> set[str]:
    """读取本地 CSV，返回已存在的日期集合"""
    if not csv_path.exists():
        return set()
    dates = set()
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            dates.add(row['date'])
    return dates


def get_last_date_fast(csv_path: Path) -> str | None:
    """用 tail -1 快速读取CSV最后一行的日期，不全量读文件"""
    import subprocess
    try:
        last_line = subprocess.check_output(['tail', '-1', str(csv_path)], text=True, timeout=2).strip()
        return last_line.split(',')[0] if last_line else None
    except Exception:
        return None


def build_index(kline_dir: Path) -> dict[str, dict]:
    """扫描目录建立股票index：{symbol: {last_date, file_mtime}}，跳过已是今天的数据"""
    import subprocess, os
    last_tday = _last_trading_day()
    index = {}
    files = [f for f in kline_dir.glob('*.csv') if not f.name.startswith('.')]
    for f in files:
        last_date = get_last_date_fast(f)
        index[f.stem] = {'last_date': last_date, 'file_mtime': os.path.getmtime(str(f))}
    stuck = {k: v for k, v in index.items() if v.get('last_date', '') < last_tday}
    return index, stuck


def get_stuck_from_index(index: dict) -> list[str]:
    """从index快速筛选未更新到今天的股票"""
    last_tday = _last_trading_day()
    return [sym for sym, info in index.items() if info.get('last_date', '') < last_tday]


def append_rows(csv_path: Path, rows: list[dict], index: dict | None = None):
    """追加新行到 CSV（保留原有数据），并更新index"""
    if not rows:
        print("  没有新增数据，跳过")
        return
    file_exists = csv_path.exists()
    # Ensure file ends with newline before appending
    if file_exists and not csv_path.read_bytes().endswith(b'\n'):
        csv_path.write_bytes(csv_path.read_bytes() + b'\n')
    with open(csv_path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)
    print(f"  ✅ 追加 {len(rows)} 条新数据 → {csv_path.name}")
    # 更新 index
    if index is not None and rows:
        new_date = rows[-1]['date']
        if new_date > index.get(csv_path.stem, {}).get('last_date', ''):
            index[csv_path.stem] = {'last_date': new_date, 'file_mtime': csv_path.stat().st_mtime}
    # 同步写回 index 文件
    if index is not None:
        index_file = csv_path.parent / '.kline_index.json'
        with open(index_file, 'w') as fp:
            json.dump(index, fp)


def download_kline(symbol: str, days: int = 365, retries: int = 5, force: bool = False) -> list[dict]:
    """从腾讯财经下载 K 线数据"""
    end_str = datetime.date.today().strftime('%Y-%m-%d')
    lookback = min(days * 2, 500) if force else min(days * 2, 120)
    start_str = (datetime.date.today() - datetime.timedelta(days=lookback)).strftime('%Y-%m-%d')

    last_err = None
    for attempt in range(retries):
        try:
            return _fetch_kline(symbol, start_str, end_str)
        except Exception as e:
            last_err = e
            wait = (attempt + 1) * 3
            print(f"  ⚠️ 下载异常: {e}，等待 {wait}s 重试（第{attempt+1}次）...")
            time.sleep(wait)
            continue

    print(f"  ❌ 下载失败（已重试{retries}次）: {last_err}")
    return []


def process_symbol(symbol: str, output_dir: Path, days: int, force: bool, index: dict | None = None):
    """处理单只股票：增量下载或全量下载"""
    csv_path = output_dir / f'{symbol}.csv'

    if not force:
        existing_dates = load_existing_dates(csv_path)
        if existing_dates:
            print(f"  本地已有 {len(existing_dates)} 条记录，增量模式...")
    else:
        existing_dates = set()
        print(f"  全量重下模式...")

    try:
        all_rows = download_kline(symbol, days, force=force)
    except Exception as e:
        print(f"  ❌ 下载失败: {e}")
        return

    if not all_rows:
        print("  ⚠️ 无数据")
        return

    if force:
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(all_rows)
        print(f"  ✅ 全量写入 {len(all_rows)} 条 → {csv_path.name}")
        if index is not None and all_rows:
            index[symbol] = {'last_date': all_rows[-1]['date'], 'file_mtime': csv_path.stat().st_mtime}
    else:
        new_rows = [r for r in all_rows if r['date'] not in existing_dates]
        if new_rows:
            append_rows(csv_path, new_rows, index)
            total = load_existing_dates(csv_path)
            print(f"  本地累计 {len(total)} 条记录")
        else:
            print("  ✅ 数据已是最新，无需更新")
            # 即使API无新数据，也用API返回的最近日期更新index
            if index is not None and all_rows:
                new_date = all_rows[-1]['date']
                old_date = index.get(symbol, {}).get('last_date', '')
                if new_date > old_date:
                    index[symbol] = {'last_date': new_date, 'file_mtime': csv_path.stat().st_mtime}
            if index is not None and all_rows:
                index[symbol] = {'last_date': all_rows[-1]['date'], 'file_mtime': csv_path.stat().st_mtime}


def main():
    parser = argparse.ArgumentParser(
        description='股票 K 线数据下载器（港股/A股，数据源：腾讯财经）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
代码识别规则：
  港股 4~5位数字 → .HK（5位）示例: 00700.HK, 0700, 0005.HK
  沪市 6位 (600/601/603/605/688) → .SS  示例: 600519.SS, 688981.SS
  深市 6位 (000/001/002/003/300/301) → .SZ  示例: 000001.SZ, 300750.SZ
        '''
    )
    parser.add_argument('symbols', nargs='*', help='股票代码（可多个，自动识别市场）')
    parser.add_argument('-i', '--input-file', type=str,
                        help='从文件读取股票代码（每行一个，# 开头为注释；默认 list.txt）')
    parser.add_argument('-o', '--output', type=str, default=None,
                        help='输出目录（默认 ./kline/）')
    parser.add_argument('-d', '--days', type=int, default=365,
                        help='下载天数（默认365天）')
    parser.add_argument('--force', action='store_true',
                        help='强制全量重下，忽略本地已有数据')
    parser.add_argument('--stuck-only', type=str, default=None,
                        help='只重下指定目录中未更新到今天的股票')
    parser.add_argument('--build-index', type=str, default=None,
                        help='重建 index 文件（用于首次初始化或数据不一致时），指定K线目录路径')

    args = parser.parse_args()

    # ── 处理 build-index ──
    build_dir = args.build_index or args.stuck_only
    if args.build_index:
        kline_dir = Path(args.build_index).expanduser()
        index_file = kline_dir / '.kline_index.json'
        print(f"🔨 正在扫描 {kline_dir} 建立 index（首次较慢，请耐心）...")
        index, stuck = build_index(kline_dir)
        with open(index_file, 'w') as fp:
            json.dump(index, fp)
        last_tday = _last_trading_day()
        print(f"✅ Index 已建立：共 {len(index)} 只股票，{len(stuck)} 只未更新到 {last_tday}")
        print(f"   Index 文件：{index_file}")
        print(f"   可用 --stuck-only {kline_dir} 直接运行更新")
        return

    # ── 处理 stuck-only ──
    if args.stuck_only:
        stuck_dir = Path(args.stuck_only).expanduser()
        index_file = stuck_dir / '.kline_index.json'
        last_tday = _last_trading_day()

        # 优先从 index 读取；无 index 时自动降级为全量扫描
        if index_file.exists():
            with open(index_file) as fp:
                index = json.load(fp)
            stuck_symbols = get_stuck_from_index(index)
            print(f"📋 从 index 加载（共 {len(index)} 只），未更新到 {last_tday}：{len(stuck_symbols)} 只")
        else:
            print(f"⚠️ 未找到 index 文件 ({index_file})，正在扫描目录...")
            index, stuck_symbols = None, []
            files = [f for f in stuck_dir.glob('*.csv') if not f.name.startswith('.')]
            for f in files:
                last_date = get_last_date_fast(f)
                if not last_date or last_date < last_tday:
                    stuck_symbols.append(f.stem)
            print(f"📋 扫描完成：{len(stuck_symbols)} 只未更新到 {last_tday}")

        if not stuck_symbols:
            print(f"✅ 所有股票数据已是最新（最近交易日: {last_tday}）")
            return

        print(f"开始并发更新（15 workers）...")
        output_dir = stuck_dir

        # 加载 index 供 download 使用
        if index_file.exists():
            with open(index_file) as fp:
                index = json.load(fp)
        else:
            index = {}

        # 并发下载，15 workers
        lock_print = __import__('threading').Lock()

        def worker(symbol):
            try:
                process_symbol(symbol, output_dir, args.days, args.force, index)
                return symbol, True, None
            except Exception as e:
                return symbol, False, str(e)

        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = {executor.submit(worker, sym): sym for sym in stuck_symbols}
            done = 0
            for fut in as_completed(futures):
                done += 1
                sym, ok, err = fut.result()
                if not ok:
                    with lock_print:
                        print(f"\n📈 [{done}/{len(stuck_symbols)}] {sym} ❌ {err}")

        # 下载完成后更新 index 文件
        if index:
            with open(index_file, 'w') as fp:
                json.dump(index, fp)

        print(f"\n{'='*50}")
        print(f"完成！共 {len(stuck_symbols)} 只")
        return

    symbols = []
    input_file = args.input_file if args.input_file else 'list.txt'
    input_path = Path(input_file).expanduser()
    if input_path.exists():
        symbols = read_stock_list(input_path)
        print(f"📋 从文件读取到 {len(symbols)} 只股票: {input_path}")
    elif args.input_file:
        print(f"❌ 文件不存在: {input_path}")
        return

    symbols += args.symbols

    if not symbols:
        print("❌ 未提供股票代码，请用参数指定或创建 list.txt")
        return

    seen = set()
    unique = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    symbols = unique

    output_dir = Path(args.output).expanduser() if args.output else Path.cwd() / 'kline'
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"📁 输出目录: {output_dir}")
    print(f"📅 下载范围: 近 {args.days} 天")
    print(f"📊 共 {len(symbols)} 只股票")

    lock_print = __import__('threading').Lock()
    index = None  # 初始化index变量为None

    def worker(symbol):
        try:
            process_symbol(symbol, output_dir, args.days, args.force, index)
            return symbol, True, None
        except Exception as e:
            return symbol, False, str(e)

    success, failed = 0, 0
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(worker, sym): sym for sym in symbols}
        done = 0
        for fut in as_completed(futures):
            sym, ok, err = fut.result()
            done += 1
            if not ok:
                with lock_print:
                    print(f"\n📈 [{done}/{len(symbols)}] {sym} ❌ {err}")
                failed += 1

    print(f"\n{'='*50}")
    print(f"完成！成功 {len(symbols)-failed} 只，失败 {failed} 只")


if __name__ == '__main__':
    main()
