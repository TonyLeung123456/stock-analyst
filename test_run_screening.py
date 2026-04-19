import os, sys
import glob
import csv

# 直接实现 _read_klines 函数
def _read_klines(csv_path: str):
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

# 测试配置
hk_kline_dir = '/Users/tonyleung/Downloads/股票/港股/Kline'
print(f"HK Kline dir: {hk_kline_dir}")
print(f"Dir exists: {os.path.exists(hk_kline_dir)}")

# 测试 glob.glob
print('\nTesting glob.glob:')
files = glob.glob(os.path.join(hk_kline_dir, "*.csv"))
print(f'Found {len(files)} CSV files')
if files:
    print('First 5 files:', files[:5])
else:
    print('No files found!')

# 测试 _read_klines 函数
print('\nTesting _read_klines:')
if files:
    test_file = files[0]
    print(f'Testing file: {test_file}')
    kl = _read_klines(test_file)
    print(f'Rows read: {len(kl)}')
    if kl:
        print('First row:', kl[0])
        print('Last row:', kl[-1])
    else:
        print('No rows read from file!')
else:
    print('No files to test!')

# 测试 hk_stocks 加载
hk_stocks = {}
print('\nTesting hk_stocks loading:')
for path in files[:5]:  # 只测试前5个文件
    code = os.path.basename(path).replace(".HK.csv", "")
    kl = _read_klines(path)
    if kl:
        hk_stocks[code] = kl
        print(f'Loaded {code}: {len(kl)} rows')
    else:
        print(f'Failed to load {code}')

print(f'\nTotal hk_stocks: {len(hk_stocks)}')