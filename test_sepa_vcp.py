import json
import requests
import time

# 测试 API 调用，模拟用户点击"运行筛选"按钮
def test_screening():
    url = 'http://localhost:7878/api/screen'
    
    # 模拟用户打开页面时的默认参数
    default_params = {
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
            "rsi_min": 0,          # RSI(14) > 0
            "rsi_max": 100         # RSI(14) < 100
        },
        "fund_flow": {
            "north_dir": "all",
            "south_dir": "all"
        },
        "sentiment": {
            "vix_max": 50,          # VIX < 50
            "vix_calm": False
        },
        "vcp": {
            "min_score": 60
        }
    }
    
    # 模拟用户点击"重置"按钮后的参数（应该与默认参数相同）
    reset_params = default_params.copy()
    
    print("=== 测试 SEPA × VCP 选股系统 ===")
    print("\n1. 测试默认参数（打开页面时的配置）")
    test_params(default_params, "默认参数")
    
    print("\n2. 测试重置参数（点击'重置'按钮后的配置）")
    test_params(reset_params, "重置参数")
    
    # 测试不同的市场
    print("\n3. 测试仅港股市场")
    hk_params = default_params.copy()
    hk_params["market"] = "hk"
    test_params(hk_params, "仅港股市场")
    
    print("\n4. 测试仅A股市场")
    a_params = default_params.copy()
    a_params["market"] = "cn"
    test_params(a_params, "仅A股市场")

def test_params(params, test_name):
    """测试指定参数的筛选结果"""
    url = 'http://localhost:7878/api/screen'
    
    try:
        print(f"\n测试 {test_name}...")
        start_time = time.time()
        response = requests.post(url, json=params, timeout=60)
        response.raise_for_status()
        result = response.json()
        end_time = time.time()
        
        print(f"  响应时间: {end_time - start_time:.2f} 秒")
        print(f"  总共扫描: {result.get('stage_counts', {}).get('total', 0)}")
        print(f"  最终结果: {len(result.get('results', []))}")
        print(f"  筛选漏斗:")
        for key, value in result.get('stage_counts', {}).items():
            if key == 'total':
                continue
            print(f"    {key}: {value}")
        
        if result.get('results'):
            print(f"  前5个结果:")
            for i, result in enumerate(result['results'][:5]):
                print(f"    {i+1}. {result['code']} - {result['name']} (VCP: {result['vcp_score']})")
        else:
            print(f"  无结果")
            
    except Exception as e:
        print(f"  错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_screening()