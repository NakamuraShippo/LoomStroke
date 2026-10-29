<h1>
  <img src="img/favicon.png" alt="" width="64" height="64" align="middle">&nbsp;&nbsp;
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="img/textlogo.png">
    <img src="img/textlogo_br.png" alt="LoomStroke" width="400" align="middle">
  </picture>
</h1>

[日本語](README.md) | [English](README.en.md) | [简体中文](README.zh-CN.md) | [繁體中文（台灣）](README.zh-TW.md)

### [patreon](https://www.patreon.com/c/LoomStroke)

LoomStroke 是一款以 PySide6 和 wgpu 開發的桌面繪圖應用程式。
支援點陣與向量編輯、多頁稿件、文字、漫畫分格與對話框，
並可與 ComfyUI、LLM 及影像生成服務整合。

## 下載 Windows 版 ZIP

### [下載最新版 LoomStroke（ZIP）](https://github.com/NakamuraShippo/LoomStroke/archive/refs/heads/main.zip)

**無須事先安裝 Python。** 需要 64 位元 Windows，以及首次安裝時的網際網路連線。
如果找不到 Python，安裝程式會自動下載僅供本應用程式使用的 Python。

1. 透過上方連結下載 ZIP 檔案。
2. 在 ZIP 檔案上按滑鼠右鍵，選擇「解壓縮全部」，然後開啟解壓縮後的 `LoomStroke-main` 資料夾。
3. 按兩下 `install.bat`，等待出現「Installation completed successfully.」。
4. 按兩下 `update.bat`，等待更新至最新版本。
5. 按兩下 `boot_LoomStroke.bat` 啟動應用程式。

之後只需執行 `boot_LoomStroke.bat` 即可啟動，無須安裝 Git 或輸入指令。
若要在不啟用 AI 功能的情況下啟動，請按兩下 `boot_LoomStroke_noAI.bat`。
請務必先解壓縮 ZIP，不要直接在壓縮檔內執行檔案。
安裝完成後，請繼續使用原來的解壓縮資料夾。

此連結會下載發布儲存庫 `main` 分支的最新版本。ZIP 包含應用程式本體、說明文件和授權條款，
Python 和虛擬環境會在安裝時準備，模型權重與使用者資料不包含在 ZIP 中。
詳情請參閱[安裝與啟動指南](docs/user/INSTALL_AND_RUN.md)。

更新已安裝的 ZIP 版本時，請先儲存稿件並關閉 LoomStroke，再按兩下 `update.bat`。
舊版應用程式檔案會保留在 `.update-backups/` 中，使用者資料與執行環境也會保留。
若是首次加入此更新功能，請從最新版 ZIP 中將 `update.bat` 和 `update.ps1` 兩個檔案
複製到目前的應用程式資料夾中。
公開的 Git 版本也可使用同一個 `update.bat` 更新，需要 Git for Windows 與 `main` 分支。
如果存在本機變更或自行建立的提交，更新程式會停止，不會捨棄這些內容。
另請參閱 [Git 版更新說明](docs/user/INSTALL_AND_RUN.md#git版のアップデートwindows)。開發資料夾不在更新範圍內。

2026 年 10 月 20 日，我們在未安裝 Python 的 64 位元 Windows 11 環境中，確認了從解壓縮發布的 ZIP 檔、
透過兩個 BAT 檔案自動安裝與啟動，到在新建稿件中繪圖、復原/重做、儲存及正常結束的流程。
驗證使用了 Windows Sandbox，日常使用無須 Windows Sandbox。

本儲存庫是依 LoomStroke License v1.0 公開原始碼的版本。
使用條款以 [LICENSE](LICENSE) 為準，日文譯本請參閱 [LICENSE_JA](LICENSE_JA)。
允許使用本軟體，包括商業用途。再次散布、分享、以散布為目的的修改、
銷售、轉讓等行為，須事先取得著作權人的書面許可。
本應用程式的授權條款不限制您如何使用自己創作的作品。
第三方程式碼與資源適用各自的授權條款。

## 系統需求

- Python 3.11 或更新版本（Windows 上的 `install.bat` 會在需要時自動準備）
- 支援 wgpu 的 GPU 與驅動程式（Windows 使用 D3D12，Linux 使用 Vulkan，macOS 使用 Metal）
- 作業系統認證存放區（付費閘道儲存與讀取 API 金鑰時需要）

各作業系統上的實際執行情況取決於具體環境。本快照的驗證不包含 macOS 實機驗證。

## 安裝與啟動

在 64 位元 Windows 上，即使未安裝 Python，也可以直接按兩下 `install.bat`。
需要時，安裝程式會下載官方 CPython 3.13.16，使用固定的 SHA-256 雜湊值驗證後，將其準備在 `.runtime/` 中。
無須系統管理員權限，也無須修改系統 Python 或 PATH。
安裝程式會沿用現有的 `.venv` / `venv`；若兩者皆不存在，則建立 `.venv`，並安裝應用程式與必要的相依套件。
安裝失敗時會保留畫面上的錯誤訊息並結束，不會刪除或取代現有的虛擬環境。
安裝完成後，請按兩下 `update.bat`，等待更新至最新版本後，
再使用 `boot_LoomStroke.bat` 啟動。

可使用 `install.bat --nanobanana` 啟用 NanoBanana 的額外相依套件。
自動執行時可加上 `--no-pause`；若要指定建立新虛擬環境所用的 Python，
請加上 `--python "C:\path\python.exe"`。
使用 `--no-python-download` 可停用 Python 自動下載。
`.runtime/` 和 `.venv/` 用於啟動應用程式，安裝後請將它們保留在原位置。

若要手動安裝，請在儲存庫根目錄執行：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m loomstroke
```

在 macOS / Linux 上，請使用 `source .venv/bin/activate` 啟用虛擬環境。

繪圖所需的 `wgpu==0.31.0` 已包含在相依套件定義中，會與 `rendercanvas` 一起自動安裝。
它也列在 `requirements.txt` 中，無須個別安裝。
該檔案是固定版本的執行環境相依套件清單，包含 NanoBanana 所需的額外 SDK，
不包含測試、格式化、型別檢查或建置工具。

若要安裝 NanoBanana 的額外相依套件，請在同一個虛擬環境中執行：

```powershell
python -m pip install -e ".[nanobanana]"
```

`keyring` 是必要的相依套件。API 金鑰透過 `SecretProvider` 使用作業系統的認證存放區。
若該存放區無法使用，付費閘道將拒絕儲存與讀取 API 金鑰。
連線至外部服務需要透過應用程式設定中的 `privacy/allow_external_services` 允許。
AI Selection 所需的 SAM3.1 執行環境與模型權重不隨應用程式附帶，使用相關功能時須另行準備。

## 說明文件

- [使用者指南入口](docs/user/index.html)
- [日文手冊](docs/user/index_ja.html)
- [安裝與啟動](docs/user/INSTALL_AND_RUN.md)
- [啟動方式與疑難排解](docs/user/RUN_INSTRUCTIONS.md)

說明文件反映了截至 2026 年 10 月 22 日的開發版，包含 CMYK 繪圖、圖層樣式、
RGB/CMYK 色彩面板、向量筆畫寬度編輯等內容。
也包含沿路徑排列文字、文字圖層自動命名、不啟用 AI 的啟動方式、ZIP 更新、ComfyUI 參數顯示與
結束處理、漫畫分格變更後的即時復原/重做，以及移出螢幕的浮動面板還原。

可從主選單「濾鏡」中選擇與圖層濾鏡相同的 11 種濾鏡，並直接套用至目標圖層。
對於向量與文字圖層，會顯示點陣化確認；在濾鏡設定中按下確定後，會將點陣化與濾鏡一起套用。
取消確認或設定時會保留原圖層，套用後也可透過一次復原還原。

## 原始碼結構

- `src/loomstroke/`：應用程式本體、內建外掛程式、繪圖處理及執行資源
- `img/`：應用程式標誌與圖示
- `docs/user/`：使用者指南
- `LICENSES/` 和 `THIRD_PARTY_LICENSES`：第三方授權聲明

不包含開發儲存庫歷史、非公開測試素材、測試套件、內部開發文件、
使用者資料、API 金鑰、模型權重、虛擬環境或已建置的發布套件。
公開範圍請參閱 [PUBLICATION.md](PUBLICATION.md)。

## 第三方元件

PySide6 / Qt 依 LGPL-3.0 條款使用。
LoomStroke 自有授權條款不限制第三方授權條款授予的權利，
例如替換動態連結程式庫的權利。

Mapbox earcut 的移植部分採用 ISC License，隨附的 ICC 描述檔遵循各自的授權條款。
詳情請參閱 [THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES)、[LICENSES](LICENSES/)，
以及 `src/loomstroke/io/resources/icc/` 中的聲明。

在託管平台上進行瀏覽、fork 等操作時，也適用該平台的服務條款。
許可申請的聯絡方式見 LICENSE。
