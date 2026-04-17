# MEMORY.md - 股票分析大师核心记忆

## Agent 身份
- Agent ID: stock-analyst
- 定位: 马克·米勒维尼趋势交易体系 + 全球宏观 + A股政策市

## 用户偏好
_(待初始化，请通过对话积累)_

## 交易纪律
- 单笔止损: ≤7%
- 日内回撤红线: 10%
- 月度回撤红线: 15%（半仓）/ 20%（空仓）
- 单股仓位上限: 40%
- 现金仓位底线: 20%

## 核心关注指标
- 北向资金流向
- 美元指数 / 人民币汇率
- A50期货（隔夜风向标）
- VIX恐慌指数
- 10年期美债收益率
- DR007银行间利率

## App 凭证（Feishu 飞书机器人）
- App ID: cli_a9519feb77f81bd4
- App Secret: lvkB7sc36BuU0u4vvhrmXf3S0Eshit3n

## realtime_monitor.py 数据源修复（2026-04-14）
**背景**：用户启用国内网络，代理7890关闭，原依赖代理的国际数据源全部失效

**网络环境变化**：
- 代理 127.0.0.1:7890 已关闭（端口不通）
- 国内网络直连：腾讯财经、新浪财经、东方财富均可访问
- 国际站（Yahoo Finance、Binance、Coinbase等）：连接超时

**数据源替代方案**：
| 原数据 | 替代方案 | 状态 |
|--------|---------|------|
| 雅虎期货/商品 | 腾讯 `hf_GC/CL/SI/HG/NG` 直连 | ✅ 黄金/原油/白银/铜/天然气 |
| Binance BTC | 暂无国内替代 | ❌ |
| Yahoo USD/CNY | 暂无国内替代 | ❌ |
| AKShare 北向资金 | 直连 legulegu（部分有效） | ⚠️ 净买额为0，上涨/下跌数正常 |

**东方财富板块 API 参数（已确认）**：
- 行业板块：`fs=m:90+t:2`
- 概念板块：`fs=m:90+t:3`
- 全A涨跌统计：`fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048`

**HF期货格式（腾讯）**：
- 解析：`parts[0].split('=')[1].split(',')` → `[price, pct, ..., name]`
- 代码：`hf_GC`(黄金), `hf_CL`(WTI原油), `hf_SI`(白银), `hf_HG`(铜), `hf_NG`(天然气)

## MCP cn-a-stock 配置（2026-04-14）
- 公共服务端点：`http://82.156.17.205/cnstock/mcp`（streamable-http）
- 已通过 `openclaw mcp set` 写入 openclaw.json
- 工具：brief（基本信息+行情）/ medium（+财务）/ full（+KDJ/MACD/RSI/布林带）
- 本地部署：因 ta-lib C库 安装受阻（macOS 12 + brew Tier 3），暂用公共服务替代
- 注意：公共服务限时免费，可能不稳定；生产环境建议本地部署

## 技术指标库（2026-04-14）
- `ta` Python库：已安装（RSI/MACD/布林带，无需C库）
- ta-lib C库：brew/autotools/源码均无法安装，放弃
- 整合验证：ta + AKShare K线数据 ✅

## K线数据本地路径
| 市场 | 路径 |
|------|------|
| 港股 | `/Users/tonyleung/Downloads/股票/港股/Kline/` |
| A股 | `/Users/tonyleung/Downloads/股票/A股/Kline/` |

## 记忆索引
| 日期 | 内容 | 路径 |
|------|------|------|
| 2026-04-12 | 啸天(Xiāo Tiān)与肖天同音，飞书发消息时容易搞混 | 本文件 |
| 2026-04-12 | 记录正确App凭证 | MEMORY.md |
| 2026-04-12 | 周度策略复盘报告完成，涵盖中美关税升级至145%/125%、美伊谈判、A股量能萎缩等关键变量 | memory/2026-04-12.md |
| 2026-04-13 | 确认K线数据本地路径：港股/Users/tonyleung/Downloads/股票/港股/Kline、A股/Users/tonyleung/Downloads/股票/A股/Kline | MEMORY.md |
| 2026-04-16 | SEPA-VCP Web选股工具：路径 `/Users/tonyleung/.openclaw/workspace/scripts/sepa_vcp_app.py`、端口7860、启动命令 `python3 .../sepa_vcp_app.py`、停止 `lsof -ti :7860 | xargs kill` | MEMORY.md |
| 2026-04-16 | 哮天每日收盘报告工具：路径 `~/.openclaw/agency-agents/stock-analyst/daily_report_app/daily_report_app.py`、端口7878、启动 `python3 .../daily_report_app.py`、停止 `lsof -ti :7878 | xargs kill` | MEMORY.md |
| 2026-04-16 | 每日收盘报告工具(daily_report_app)功能：选股扫描(35+指标)、择时监测(北向/南向/MA200/VIX)、每日报告图片生成 | MEMORY.md |
