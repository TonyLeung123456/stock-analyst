import json
import requests

# 测试 API 调用
def test_api():
    url = 'http://localhost:7878/api/screen'
    
    data = {
        "market": "hk",
        "config": {
            "hk_kline_dir": "/Users/tonyleung/Downloads/股票/港股/Kline"
        },
        "fundamental": {
            "rev_yoy": 0,
            "prof_yoy": 0,
            "roe": 0,
            "cagr_3y": 0,
            "pe_max": 100,
            "pb_max": 20
        },
        "technical": {
            "min_vol_ratio": 0.5,
            "require_ma50": False,
            "require_ma150": False,
            "require_ma200": False,
            "rsi_min": 0,
            "rsi_max": 100
        },
        "fund_flow": {
            "north_dir": "all",
            "south_dir": "all"
        },
        "sentiment": {
            "vix_max": 50,
            "vix_calm": False
        },
        "vcp": {
            "min_score": 0
        }
    }
    
    try:
        response = requests.post(url, json=data, timeout=30)
        response.raise_for_status()
        result = response.json()
        
        print(f"Total: {result.get('total', 0)}")
        print(f"Results: {len(result.get('results', []))}")
        print(f"Funnel: {result.get('stage_counts', {})}")
        
        if result.get('results'):
            first = result['results'][0]
            print(f"First result: {first.get('code')} - {first.get('name')} (VCP: {first.get('vcp_score')})")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_api()