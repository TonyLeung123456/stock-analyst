import os
import glob
import csv

# 直接实现必要的函数
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

def calc_vcp_score(kl):
    """计算VCP评分"""
    if not kl or len(kl) < 60:
        return {"vcp_score": 0, "vcp_grade": "N/A", "is_contracting": False, "volume_ratio": 1.0}
    
    closes = [r["close"] for r in kl]
    volumes = [r["volume"] for r in kl]
    price = closes[-1]
    ma20 = sum(closes[-20:]) / 20
    ma50 = sum(closes[-50:]) / 50
    is_contracting = price > ma20 > ma50
    
    vol_5 = sum(volumes[-5:]) / 5
    vol_prev = sum(volumes[-20:-5]) / 15 if len(volumes) >= 20 else vol_5
    volume_ratio = vol_5 / vol_prev if vol_prev > 0 else 1.0
    
    # 简化的VCP评分计算
    score = 0
    if is_contracting:
        score += 50
    if volume_ratio > 1.0:
        score += 30
    if len(kl) > 100:
        score += 20
    
    return {
        "vcp_score": min(score, 100),
        "vcp_grade": "A" if score >= 80 else "B" if score >= 60 else "C",
        "is_contracting": is_contracting,
        "volume_ratio": volume_ratio
    }

def snapshot_indicators(kl):
    """计算指标快照"""
    if len(kl) < 5:
        return {}
    
    closes = [float(k["close"]) for k in kl]
    highs = [float(k["high"]) for k in kl]
    lows = [float(k["low"]) for k in kl]
    
    n = len(closes)
    ma50 = sum(closes[-50:]) / 50 if n >= 50 else None
    ma150 = sum(closes[-150:]) / 150 if n >= 150 else None
    ma200 = sum(closes[-200:]) / 200 if n >= 200 else None
    
    # 简化的RSI计算
    rsi14 = 50  # 简化处理
    
    return {
        "close": closes[-1],
        "ma50": ma50,
        "ma150": ma150,
        "ma200": ma200,
        "rsi14": rsi14
    }

def load_hk_fin(code, fin_dir=""):
    """加载港股财务数据"""
    return None  # 简化处理

def test_screening():
    """测试筛选逻辑"""
    print("Testing screening logic...")
    
    # 配置
    hk_kline_dir = '/Users/tonyleung/Downloads/股票/港股/Kline'
    print(f"HK Kline dir: {hk_kline_dir}")
    print(f"Dir exists: {os.path.exists(hk_kline_dir)}")
    
    # 加载港股数据
    hk_stocks = {}
    files = glob.glob(os.path.join(hk_kline_dir, "*.csv"))
    print(f"Found {len(files)} CSV files")
    
    for path in files[:10]:  # 只测试前10个文件
        code = os.path.basename(path).replace(".HK.csv", "")
        kl = _read_klines(path)
        if kl:
            hk_stocks[code] = kl
            print(f'Loaded {code}: {len(kl)} rows')
        else:
            print(f'Failed to load {code}')
    
    print(f'\nTotal hk_stocks: {len(hk_stocks)}')
    
    # 测试筛选逻辑
    results = []
    funnel = {"total": 0, "ma50": 0, "ma150": 0, "vol_ratio": 0, "vcp": 0, "fundamental": 0, "final": 0}
    
    for code, kl in hk_stocks.items():
        funnel["total"] += 1
        
        # 计算指标
        snap = snapshot_indicators(kl)
        vcp = calc_vcp_score(kl)
        fin = load_hk_fin(code, "")
        
        # 技术面筛选
        ma50_ok = True  # 不要求MA50
        ma150_ok = True  # 不要求MA150
        ma200_ok = True  # 不要求MA200
        vol_ok = vcp["volume_ratio"] >= 0.5
        vcp_ok = vcp["vcp_score"] >= 0
        rsi = snap.get("rsi14", 50)
        rsi_ok = 0 <= rsi <= 100
        
        # 追踪失败数量
        if not ma50_ok:
            funnel["ma50"] += 1
        if not ma150_ok:
            funnel["ma150"] += 1
        if not vol_ok:
            funnel["vol_ratio"] += 1
        if not vcp_ok:
            funnel["vcp"] += 1
        
        if not (ma50_ok and ma150_ok and vol_ok and vcp_ok and rsi_ok):
            continue
        
        # 基本面筛选（简化）
        fin_ok = True
        if fin:
            # 这里可以添加基本面筛选逻辑
            pass
        
        funnel["fundamental"] += 1
        
        # 资金面和情绪面筛选（简化）
        north_ok = True
        south_ok = True
        vix_ok = True
        
        if not (north_ok and south_ok and vix_ok):
            continue
        
        funnel["final"] += 1
        
        # 添加结果
        results.append({
            "code": code + ".HK",
            "name": code,
            "market": "港股",
            "vcp_score": vcp["vcp_score"],
            "volume_ratio": vcp["volume_ratio"],
            "close": snap.get("close"),
            "rev_yoy": fin.get("revenue_yoy") if fin else None,
            "profit_yoy": fin.get("net_profit_yoy") if fin else None,
            "roe": fin.get("roe") if fin else None,
            "cagr_3y": fin.get("cagr_3y") if fin else None
        })
    
    print(f'\nResults: {len(results)}')
    print(f'Funnel: {funnel}')
    
    if results:
        print("\nFirst 5 results:")
        for i, result in enumerate(results[:5]):
            print(f"{i+1}. {result['code']} - {result['name']} (VCP: {result['vcp_score']}, Vol Ratio: {result['volume_ratio']:.2f})")
    else:
        print("\nNo results found!")

if __name__ == "__main__":
    test_screening()