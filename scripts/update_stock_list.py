#!/usr/bin/env python3
"""
股票行业信息更新脚本
支持 A股 和 港股 的行业数据获取

用法:
    python3 update_industry.py              # 更新全部
    python3 update_industry.py --hk        # 只更新港股
    python3 update_industry.py --cn        # 只更新A股
    python3 update_industry.py --gen      # 强制重新生成 list.txt（如果不存在）

数据源:
    A股: cn-a-stock MCP (http://82.156.17.205/cnstock/mcp)
    港股: akshare stock_hk_company_profile_em()

生成列表数据源:
    A股: akshare stock_info_a_code_name()
    港股: akshare stock_hsgt_sh_hk_spot_em() + stock_hsgt_sz_hk_spot_em() (港股通标的)

输出格式: 代码,名称,行业
缓存文件: /tmp/stock_industry_cache.json (A股), /tmp/hk_industry_cache.json (港股)
"""

import os
import sys
import json
import time
import argparse
import requests
import akshare as ak
import warnings
warnings.filterwarnings('ignore')

# 路径配置
HK_LIST_PATH = "/Users/tonyleung/.openclaw/Downloads/股票/港股/list.txt"
CN_LIST_PATH = "/Users/tonyleung/.openclaw/Downloads/股票/A股/list.txt"
CN_CACHE_FILE = "/tmp/stock_industry_cache.json"
HK_CACHE_FILE = "/tmp/hk_industry_cache.json"
MCP_URL = "http://82.156.17.205/cnstock/mcp"


# ============= 列表生成 =============
def generate_cn_list():
    """生成A股列表（股票代码+中文名）"""
    print("📥 正在从 akshare 获取A股列表...")
    try:
        df = ak.stock_info_a_code_name()
        stocks = []
        for _, row in df.iterrows():
            code = str(row['code']).zfill(6)
            name = row['name']
            stocks.append((code, name))
        print(f"   获取到 {len(stocks)} 只A股")
        return stocks
    except Exception as e:
        print(f"   获取失败: {e}")
        return []


def generate_hk_list():
    """生成港股列表（只包含港股通标的，股票代码+中文名）"""
    print("📥 正在获取港股通标的列表...")
    
    # 从 akshare 获取港股通标的（沪深）
    try:
        # 沪港通标的
        print("   获取沪港通标的...")
        df_sh = ak.stock_hsgt_sh_hk_spot_em()
        stocks = []
        for _, row in df_sh.iterrows():
            code = str(row['代码']).strip()
            name = str(row['名称']).strip()
            if code and name:
                stocks.append((code, name))
        print(f"   沪港通: {len(stocks)} 只")
        
        # 深港通标的
        print("   获取深港通标的...")
        try:
            df_sz = ak.stock_hsgt_sz_hk_spot_em()
            for _, row in df_sz.iterrows():
                code = str(row['代码']).strip()
                name = str(row['名称']).strip()
                if code and name:
                    # 避免重复
                    if code not in [s[0] for s in stocks]:
                        stocks.append((code, name))
            print(f"   深港通: {len(stocks)} 只")
        except:
            print("   深港通获取失败，继续使用沪港通数据")
        
        print(f"   共获取到 {len(stocks)} 只港股通标的")
        return stocks
        
    except Exception as e:
        print(f"   港股通标的获取失败: {e}")
    
    # 备选：从 akshare 港股实时行情获取（所有港股）
    print("   尝试备选方案（所有港股）...")
    try:
        df = ak.stock_hk_spot()
        stocks = []
        for _, row in df.iterrows():
            code = str(row['代码'])
            name = row['中文名称']
            if code and name:
                stocks.append((code, name))
        print(f"   从实时行情获取到 {len(stocks)} 只港股")
        return stocks
    except Exception as e:
        print(f"   备选方案也失败: {e}")
        return []


# ============= A股行业获取 =============
def call_cn_a_stock_mcp(symbol: str) -> str:
    """调用 cn-a-stock MCP 获取A股行业（只取第一个）"""
    session = requests.Session()
    session.trust_env = False
    
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "brief", "arguments": {"symbol": symbol}}
    }
    try:
        resp = session.post(MCP_URL, json=payload, 
                          headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}, 
                          timeout=15)
        for line in resp.text.split('\n'):
            if line.startswith('data:'):
                data = json.loads(line[5:])
                content = data.get('result', {}).get('content', [])
                if content and content[0].get('type') == 'text':
                    text = content[0]['text']
                    try:
                        text = text.encode('latin-1').decode('utf-8')
                    except:
                        pass
                    for ln in text.split('\n'):
                        if '行业概念' in ln and ':' in ln:
                            industries = ln.split(':', 1)[1].strip()
                            return industries.split()[0] if industries else ''
    except:
        pass
    return ""


def update_cn_industry():
    """更新A股行业"""
    print("📥 正在获取A股行业信息...")
    
    # 检查是否需要生成列表
    if not os.path.exists(CN_LIST_PATH):
        print(f"   {CN_LIST_PATH} 不存在，正在生成...")
        stocks_data = generate_cn_list()
        if not stocks_data:
            print("   ❌ 无法获取A股列表，退出")
            return
        # 写入基础列表（无行业）
        os.makedirs(os.path.dirname(CN_LIST_PATH), exist_ok=True)
        with open(CN_LIST_PATH, 'w', encoding='utf-8') as f:
            for code, name in stocks_data:
                f.write(f"{code},{name},\n")
        print(f"   ✅ 已生成基础列表: {CN_LIST_PATH}")
    
    # 读取列表
    stocks = []
    with open(CN_LIST_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split(',', 2)
            code = parts[0].strip()
            name = parts[1].strip() if len(parts) > 1 else ""
            # MCP 格式
            symbol = f"SH{code}" if code.startswith('6') else f"SZ{code}"
            stocks.append((code, name, symbol))
    
    print(f"   共 {len(stocks)} 只A股")
    
    # 加载缓存
    cache = {}
    if os.path.exists(CN_CACHE_FILE):
        try:
            cache = json.load(open(CN_CACHE_FILE))
        except:
            pass
    
    print(f"   缓存已有 {len(cache)} 条")
    
    new_count = 0
    skip_count = 0
    error_count = 0
    
    for i, (code, name, symbol) in enumerate(stocks):
        if i % 50 == 0:
            print(f"   进度: {i+1}/{len(stocks)} (新增:{new_count} 跳过:{skip_count} 错误:{error_count})", flush=True)
            # 定期保存
            with open(CN_CACHE_FILE, 'w') as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
        
        key = symbol.upper()
        if key in cache and cache[key]:
            skip_count += 1
            continue
        
        industry = call_cn_a_stock_mcp(key)
        if industry:
            cache[key] = industry
            new_count += 1
        else:
            error_count += 1
        
        time.sleep(0.25)
    
    # 最终保存
    with open(CN_CACHE_FILE, 'w') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    
    # 更新文件
    with open(CN_LIST_PATH, 'w', encoding='utf-8') as f:
        for code, name, symbol in stocks:
            ind = cache.get(symbol.upper(), "")
            f.write(f"{code},{name},{ind}\n")
    
    print(f"   完成! 新增:{new_count} 跳过:{skip_count} 错误:{error_count}")
    print(f"   ✅ A股行业已更新: {CN_LIST_PATH}")


# ============= 港股行业获取 =============
def get_hk_industry(code: str) -> str:
    """获取港股行业"""
    try:
        profile = ak.stock_hk_company_profile_em(symbol=code)
        if '所属行业' in profile.columns:
            return profile['所属行业'].iloc[0]
    except:
        pass
    return ""


def update_hk_industry():
    """更新港股行业"""
    print("📥 正在获取港股行业信息...")
    
    # 检查是否需要生成列表
    if not os.path.exists(HK_LIST_PATH):
        print(f"   {HK_LIST_PATH} 不存在，正在生成...")
        stocks_data = generate_hk_list()
        if not stocks_data:
            print("   ❌ 无法获取港股列表，退出")
            return
        # 写入基础列表（无行业）
        os.makedirs(os.path.dirname(HK_LIST_PATH), exist_ok=True)
        with open(HK_LIST_PATH, 'w', encoding='utf-8') as f:
            for code, name in stocks_data:
                f.write(f"{code},{name},\n")
        print(f"   ✅ 已生成基础列表: {HK_LIST_PATH}")
    
    # 读取列表
    stocks = []
    with open(HK_LIST_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split(',', 2)
            code = parts[0].strip()
            name = parts[1].strip() if len(parts) > 1 else ""
            stocks.append((code, name))
    
    print(f"   共 {len(stocks)} 只港股")
    
    # 加载缓存
    cache = {}
    if os.path.exists(HK_CACHE_FILE):
        try:
            cache = json.load(open(HK_CACHE_FILE))
        except:
            pass
    
    print(f"   缓存已有 {len(cache)} 条")
    
    new_count = 0
    skip_count = 0
    error_count = 0
    
    for i, (code, name) in enumerate(stocks):
        if i % 20 == 0:
            print(f"   进度: {i+1}/{len(stocks)} (新增:{new_count} 跳过:{skip_count} 错误:{error_count})", flush=True)
            # 定期保存
            with open(HK_CACHE_FILE, 'w') as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
        
        if code in cache and cache[code]:
            skip_count += 1
            continue
        
        industry = get_hk_industry(code)
        if industry:
            cache[code] = industry
            new_count += 1
        else:
            error_count += 1
        
        time.sleep(0.3)
    
    # 最终保存
    with open(HK_CACHE_FILE, 'w') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    
    # 更新文件
    with open(HK_LIST_PATH, 'w', encoding='utf-8') as f:
        for code, name in stocks:
            ind = cache.get(code, "")
            f.write(f"{code},{name},{ind}\n")
    
    print(f"   完成! 新增:{new_count} 跳过:{skip_count} 错误:{error_count}")
    print(f"   ✅ 港股行业已更新: {HK_LIST_PATH}")


# ============= 主程序 =============
def main():
    parser = argparse.ArgumentParser(description='更新股票行业信息')
    parser.add_argument('--hk', action='store_true', help='只更新港股')
    parser.add_argument('--cn', action='store_true', help='只更新A股')
    parser.add_argument('--gen', action='store_true', help='强制重新生成 list.txt（如果不存在）')
    args = parser.parse_args()
    
    update_hk = not args.cn
    update_cn = not args.hk
    
    print("=" * 50)
    print("股票行业更新脚本")
    print("=" * 50)
    
    # 如果指定 --gen，删除现有 list.txt 以强制重新生成
    if args.gen:
        if update_hk and os.path.exists(HK_LIST_PATH):
            print(f"   删除旧港股列表: {HK_LIST_PATH}")
            os.remove(HK_LIST_PATH)
        if update_cn and os.path.exists(CN_LIST_PATH):
            print(f"   删除旧A股列表: {CN_LIST_PATH}")
            os.remove(CN_LIST_PATH)
    
    if update_hk:
        print()
        update_hk_industry()
    
    if update_cn:
        print()
        update_cn_industry()
    
    print()
    print("=" * 50)
    print("✅ 行业更新完成")
    print("=" * 50)


if __name__ == '__main__':
    main()
