import sys
import os

# 添加项目路径
sys.path.insert(0, '/Users/tonyleung/.openclaw/agency-agents/stock-analyst/project/stock-analyst/daily_report_app')

from daily_report_app import run_screening, DEFAULT_CFG

def test_run_screening():
    """直接测试 run_screening 函数"""
    print("Testing run_screening function...")
    
    # 简化的测试参数
    params = {
        "market": "hk",
        "hk_kline_dir": "/Users/tonyleung/Downloads/股票/港股/Kline",
        "a_kline_dir": "/Users/tonyleung/Downloads/股票/A股/Kline",
        "hk_fin_dir": "",
        "a_fin_dir": "",
        "ma50_above": True,
        "ma150_above": True,
        "ma200_above": False,
        "min_vol_ratio": 1.0,
        "min_vcp_score": 60,
        "min_rev_yoy": 25,
        "min_profit_yoy": 30,
        "min_roe": 15,
        "min_cagr": 20,
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
        print("Loading K-line data...")
        print(f"HK K-line dir: {params['hk_kline_dir']}")
        print(f"Dir exists: {os.path.exists(params['hk_kline_dir'])}")
        
        # 测试 K 线数据加载
        import glob
        hk_files = glob.glob(os.path.join(params['hk_kline_dir'], "*.csv"))
        print(f"Found {len(hk_files)} HK CSV files")
        if hk_files:
            print(f"First 3 files: {hk_files[:3]}")
        
        print("\nRunning screening...")
        results, funnel = run_screening(params)
        
        print(f"\nTotal stocks scanned: {funnel.get('total', 0)}")
        print(f"Final results: {len(results)}")
        print(f"Funnel stages:")
        for key, value in funnel.items():
            print(f"  {key}: {value}")
        
        if results:
            print("\nFirst 5 results:")
            for i, result in enumerate(results[:5]):
                print(f"{i+1}. {result['code']} - {result['name']} (VCP: {result['vcp_score']})")
        else:
            print("\nNo results found!")
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_run_screening()