# 哮天每日报告系统

马克·米勒维尼趋势交易体系 + A股政策市研判

## 目录结构

```
stock-analyst/
├── app/
│   └── daily_report_app.py   # 主程序（端口7878）
├── scripts/                  # 工具脚本
│   ├── kline_downloader.py   # K线下载
│   ├── stock_scanner.py      # 选股扫描
│   ├── sepa_vcp_app.py      # SEPA分析
│   └── 黑色星期四择时策略.py  # 择时策略
├── data/                     # 数据（git版本控制）
│   ├── north/               # 北向资金
│   ├── south/               # 南向资金
│   └── hk/                  # 港股K线
├── setup.sh                 # 首次部署脚本
└── requirements.txt
```

## 首次部署

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 下载A股K线（不进入git）
./setup.sh

# 3. 启动
cd app && python3 daily_report_app.py
# 访问 http://localhost:7878
```

## 标签页说明

| 标签 | 说明 |
|------|------|
| Dashboard | 大盘行情 + 南北资金 |
| 选股扫描 | 港/A股技术指标扫描 |
| SEPA分析 | 单股深度分析 |
| 择时监测 | 黑色星期四 + 南北资金 + MA200 |
| 每日报告 | 一键生成报告图片 |
