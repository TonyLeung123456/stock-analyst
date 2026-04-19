import csv, os

# 测试 _read_klines 函数
path = '/Users/tonyleung/Downloads/股票/港股/Kline/0001.HK.csv'
print('File exists:', os.path.exists(path))

# 模拟 _read_klines 函数
rows = []
for enc in ("utf-8", "gbk", "gb2312"):
    try:
        with open(path, encoding=enc, errors="ignore") as f:
            reader = csv.DictReader(f)
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
                except (ValueError, KeyError) as e:
                    print(f"Error processing row: {e}")
                    continue
        break
    except Exception as e:
        print(f"Error reading file with {enc}: {e}")
        continue

print('Rows read:', len(rows))
if rows:
    print('First row:', rows[0])
    print('Last row:', rows[-1])
else:
    print('No rows read!')

# 检查文件内容
print('\nFile content preview:')
with open(path, 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()[:10]
    for i, line in enumerate(lines):
        print(f'Line {i+1}: {line.strip()}')