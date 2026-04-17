#!/bin/bash
# 首次部署：下载A股K线数据（不进入git）
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$SCRIPT_DIR/data"
A_STOCK_DIR="$DATA_DIR/a_stock"
TMP_DIR="$DATA_DIR/.tmp_download"

echo "📥 首次部署：下载A股K线数据…"
mkdir -p "$A_STOCK_DIR" "$TMP_DIR"

if [ -f "$SCRIPT_DIR/scripts/kline_downloader.py" ]; then
    echo "使用 kline_downloader.py 下载…"
    python3 "$SCRIPT_DIR/scripts/kline_downloader.py" --output "$A_STOCK_DIR" --market a
else
    echo "⚠️ kline_downloader.py 不存在，请手动下载A股K线到 $A_STOCK_DIR"
fi

rm -rf "$TMP_DIR"
echo "✅ A股K线数据已保存到 $A_STOCK_DIR"
echo ""
echo "=== 下一步 ==="
echo "1. 启动：cd $SCRIPT_DIR/app && python3 daily_report_app.py"
echo "2. 编辑 app/daily_report_app.py 中的 DATA_ROOT 指向 data/"
