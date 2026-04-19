import json
import requests

# 测试 API 调用
def test_api():
    url = 'http://localhost:7878/api/screen'
    
    # 重置后的默认参数
    data = {
        "market": "both",
        "config": {
            "hk_kline_dir": "/Users/tonyleung/Downloads/股票/港股/Kline",
            "a_kline_dir": "/Users/tonyleung/Downloads/股票/A股/Kline"
        },
        "fundamental": {
            "rev_yoy": 25,      # 营业收入同比增长率 > 25%
            "prof_yoy": 30,      # 净利润同比增长率 > 30%
            "roe": 15,           # ROE > 15%
            "cagr_3y": 20,       # 3年CAGR > 20%
            "pe_max": 100,
            "pb_max": 20
        },
        "technical": {
            "min_vol_ratio": 1.0,  # 最近10个交易日平均成交量大于120日均量
            "require_ma50": True,  # 股价处于50日均线上方
            "require_ma150": True, # 股价处于150日均线上方
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
            "min_score": 60
        }
    }
    
    try:
        response = requests.post(url, json=data, timeout=30)
        response.raise_for_status()
        result = response.json()
        
        print(f"Total stocks scanned: {result.get('stage_counts', {}).get('total', 0)}")
        print(f"Final results: {len(result.get('results', []))}")
        print(f"Funnel stages:")
        for key, value in result.get('stage_counts', {}).items():
            print(f"  {key}: {value}")
        
        if result.get('results'):
            print("\nFirst 10 results:")
            for i, result in enumerate(result['results'][:10]):
                print(f"{i+1}. {result['code']} - {result['name']} (VCP: {result['vcp_score']})")
        else:
            print("\nNo results found!")
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_api()