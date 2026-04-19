import sys
import os

# 添加项目路径
sys.path.insert(0, '/Users/tonyleung/.openclaw/agency-agents/stock-analyst/project/stock-analyst/daily_report_app')

from daily_report_app import run_screening, DEFAULT_CFG

def test_run_screening():
    """直接测试 run_screening 函数"""
    print("Testing run_screening function...")
    
    # 测试参数
    params = {
        "market": "hk",
        "hk_kline_dir": "/Users/tonyleung/Downloads/股票/港股/Kline",
        "a_kline_dir": DEFAULT_CFG["a_kline_dir"],
        "hk_fin_dir": "",
        "a_fin_dir": "",
        "ma50_above": False,
        "ma150_above": False,
        "ma200_above": False,
        "min_vol_ratio": 0.5,
        "min_vcp_score": 0,
        "min_rev_yoy": 0,
        "min_profit_yoy": 0,
        "min_roe": 0,
        "min_cagr": 0,
        "pe_max": 100,
        "pb_max": 20,
        "rsi_min": 0,
        "rsi_max": 100,
        "north_dir": "all",
        "south_dir": "all",
        "vix_max": 50,
        "vix_calm": False,
    }
    
    try:
        results, funnel = run_screening(params)
        print(f"Results: {len(results)}")
        print(f"Funnel: {funnel}")
        
        if results:
            print("First 5 results:")
            for i, result in enumerate(results[:5]):
                print(f"{i+1}. {result['code']} - {result['name']} (VCP: {result['vcp_score']})")
        else:
            print("No results found!")
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_run_screening()