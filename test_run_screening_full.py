import os, sys
import glob
import csv

# 添加项目路径
sys.path.insert(0, '/Users/tonyleung/.openclaw/agency-agents/stock-analyst/project/stock-analyst/daily_report_app')

from daily_report_app import _read_klines, run_screening, DEFAULT_CFG

# 测试 run_screening 函数
def test_run_screening():
    print("Testing run_screening function...")
    
    # 准备测试参数
    params = {
        "market": "hk",
        "hk_fin_dir": "",
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
    
    # 调用函数
    results, funnel = run_screening(params)
    
    print(f"\nResults: {len(results)}")
    print(f"Funnel: {funnel}")
    
    if results:
        print("\nFirst 5 results:")
        for i, result in enumerate(results[:5]):
            print(f"{i+1}. {result['code']} - {result['name']} (VCP: {result['vcp_score']})")
    else:
        print("\nNo results found!")
        
        # 检查 hk_stocks 加载
        hk_kline_dir = DEFAULT_CFG["hk_kline_dir"]
        print(f"\nHK Kline dir: {hk_kline_dir}")
        print(f"Dir exists: {os.path.exists(hk_kline_dir)}")
        
        files = glob.glob(os.path.join(hk_kline_dir, "*.csv"))
        print(f"Found {len(files)} CSV files")
        
        if files:
            print("First 3 files:", files[:3])
            
            # 测试读取第一个文件
            test_file = files[0]
            print(f"\nTesting file: {test_file}")
            kl = _read_klines(test_file)
            print(f"Rows read: {len(kl)}")
            if kl:
                print(f"First row: {kl[0]}")
                print(f"Last row: {kl[-1]}")
            else:
                print("No rows read!")

if __name__ == "__main__":
    test_run_screening()