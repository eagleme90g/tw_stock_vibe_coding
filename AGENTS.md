# 專案說明書

## 1. 項目概述
- 本專案提供「台股即時與歷史股價擷取」的命令列工具，核心程式為 `realtime_stock_price.py`。
- 透過 TWSE/TPEX 公開 API 取得即時報價與五檔資訊，亦可整合 yfinance 取得日線歷史資料。
- 產生的 CSV、Excel 以及錯誤日誌會依照執行參數存放於指定目錄，支援 `OUTDIR` 參數或環境變數覆寫。

## 2. 安裝、環境變數、執行與建構
### 2.1 系統需求與依賴
- Python 3.10 以上版本。
- 建議使用虛擬環境（`python -m venv .venv`）。
- 套件依賴：`requests`、`pandas`、`openpyxl`、`yfinance`；如需打包則另安裝 `pyinstaller`。

### 2.2 環境變數
- `OUTDIR`：覆寫輸出資料夾，若未設定則使用執行目錄。
- 任何憑證或代理設定請改用環境變數或 `.env` 類檔案，避免硬編碼。

### 2.3 安裝步驟（UTF-8 編碼，Windows PowerShell 範例）
```powershell
python -m venv .venv
.\.venv\Scripts\Activate
pip install requests pandas openpyxl yfinance
```

### 2.4 執行指令
- 單次即時快照：
  ```powershell
  python realtime_stock_price.py 2330 2317
  ```
- 定期輪詢並指定輸出：
  ```powershell
  python realtime_stock_price.py 2330 --interval 10 --rounds 6 --outdir .\data
  ```
- 下載日線資料（yfinance）：
  ```powershell
  python realtime_stock_price.py 2330 --daily --daily-start 2024-01-01 --daily-end 2024-03-31
  ```
- 內建自測：
  ```powershell
  python realtime_stock_price.py 2330 --selftest
  ```

### 2.5 建構（打包）命令
```powershell
pip install pyinstaller  # 若尚未安裝
pyinstaller realtime_stock_price.py --onefile --name tw-stock-vibe
```

## 3. 目錄結構、頁面路由與 API 介面
### 3.1 目錄結構
```
project/
├─ realtime_stock_price.py      # 核心資料管線腳本
├─ prompt/                      # 提示詞或設定片段
├─ data/                        # 建議存放輸出結果（可由 OUTDIR 指向）
├─ experiments/                 # 實驗／notebook（避免隨程式發佈）
└─ AGENTS.md                    # 項目指南（本文件）
```
- 專案目前無前端與後端分離，也沒有多層架構；所有邏輯集中於單一腳本。
- 無網頁路由或畫面設定，使用者僅透過 CLI 參數操作。
- 無對外 REST API；若需服務化，建議於腳本外包裝額外的 API 層。

## 4. 技術棧與依賴說明
- **語言 / 版本**：Python 3.10+，檔案採 UTF-8 編碼。
- **主要套件**：
  - `requests`：呼叫 TWSE/TPEX 即時資料 API。
  - `pandas`：資料整理、表格輸出。
  - `openpyxl`：寫入 Excel 工作簿。
  - `yfinance`：補足日線歷史資料。
- **開發指引**：
  - 變數、函式與 CLI 旗標統一使用 snake_case。
  - 進入點使用 `if __name__ == "__main__":` 保護。
  - 錯誤記錄統一透過全域 `ErrorLogger`，並於執行後呼叫 `flush_to_file`。
  - Commit 訊息採命令式語氣，例如 `Add daily fetch option`。
- **安全性與配置**：
  - 不要硬編碼 API Key 或密碼；統一讀取環境變數。
  - 遵守 TWSE/TPEX 請求頻率限制，多檔輪詢時 `--interval` 建議大於 5 秒。
  - 若需代理或防火牆設定，請於文件或 PR 說明。

以上說明均以 UTF-8 編碼撰寫，符合項目需求。
