<h1>
  <img src="img/favicon.png" alt="" width="64" height="64" align="middle">&nbsp;&nbsp;
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="img/textlogo.png">
    <img src="img/textlogo_br.png" alt="LoomStroke" width="400" align="middle">
  </picture>
</h1>

[日本語](README.md) | [English](README.en.md) | [简体中文](README.zh-CN.md) | [繁體中文（台灣）](README.zh-TW.md)

### [patreon](https://www.patreon.com/c/LoomStroke)

LoomStroke 是一款基于 PySide6 和 wgpu 的桌面绘画应用。
支持栅格与矢量编辑、多页文稿、文字、漫画分格和气泡，
并可与 ComfyUI、LLM 及图像生成服务联动。

## 下载 Windows 版 ZIP

### [下载最新版 LoomStroke（ZIP）](https://github.com/NakamuraShippo/LoomStroke/archive/refs/heads/main.zip)

**无需预先安装 Python。** 需要 64 位 Windows，以及首次安装时的互联网连接。
如果未找到 Python，安装程序会自动下载仅供本应用使用的 Python。

1. 通过上方链接下载 ZIP 文件。
2. 右键单击 ZIP，选择“全部解压缩”，然后打开解压后的 `LoomStroke-main` 文件夹。
3. 双击 `install.bat`，等待出现“Installation completed successfully.”。
4. 双击 `update.bat`，等待更新到最新版本。
5. 双击 `boot_LoomStroke.bat` 启动应用。

以后只需运行 `boot_LoomStroke.bat` 即可启动，无需安装 Git 或输入命令。
如需在不启用 AI 功能的情况下启动，请双击 `boot_LoomStroke_noAI.bat`。
请务必先解压 ZIP，不要直接在压缩包内运行文件。
安装完成后，请继续使用原来的解压文件夹。

此链接下载发布仓库 `main` 分支的最新版本。ZIP 包含应用本体、说明文档和许可证，
Python 和虚拟环境会在安装时准备，模型权重和用户数据不包含在 ZIP 中。
详情请参阅[安装与启动指南](docs/user/INSTALL_AND_RUN.md)。

更新已安装的 ZIP 版本时，请先保存文稿并关闭 LoomStroke，然后双击 `update.bat`。
旧版应用文件会保留在 `.update-backups/` 中，用户数据和运行环境会得到保留。
如果是首次添加此更新功能，请从最新版 ZIP 中将 `update.bat` 和 `update.ps1` 两个文件
复制到当前应用文件夹中。
公开的 Git 版本也可使用同一个 `update.bat` 更新，需要 Git for Windows 和 `main` 分支。
如果存在本地更改或自行创建的提交，更新程序会停止，不会丢弃这些内容。
另请参阅 [Git 版更新说明](docs/user/INSTALL_AND_RUN.md#git版のアップデートwindows)。开发文件夹不在更新范围内。

2026 年 10 月 20 日，我们在未安装 Python 的 64 位 Windows 11 环境中，确认了从解压发布的 ZIP 包、
通过两个 BAT 文件自动安装和启动，到在新建文稿中绘画、撤销/重做、保存及正常退出的流程。
验证使用了 Windows Sandbox，日常使用无需 Windows Sandbox。

本仓库是按 LoomStroke License v1.0 公开源代码的版本。
使用条款以 [LICENSE](LICENSE) 为准，日文译本请参阅 [LICENSE_JA](LICENSE_JA)。
允许使用本软件，包括商业用途。再分发、共享、以分发为目的的修改、
销售、转让等行为需要事先取得著作权人的书面许可。
本应用的许可证不限制您如何使用自己创作的作品。
第三方代码和资源适用各自的许可证。

## 运行要求

- Python 3.11 或更高版本（Windows 上的 `install.bat` 会在需要时自动准备）
- 支持 wgpu 的 GPU 及驱动程序（Windows 使用 D3D12，Linux 使用 Vulkan，macOS 使用 Metal）
- 操作系统凭据存储（付费网关保存和读取 API 密钥时需要）

各操作系统上的实际运行情况取决于具体环境。本快照的验证不包含 macOS 实机验证。

## 安装与启动

在 64 位 Windows 上，即使未安装 Python，也可以直接双击 `install.bat`。
需要时，安装程序会下载官方 CPython 3.13.16，使用固定的 SHA-256 校验值验证后，将其准备在 `.runtime/` 中。
无需管理员权限，也无需修改系统 Python 或 PATH。
安装程序会复用现有的 `.venv` / `venv`；如果均不存在，则创建 `.venv`，并安装应用及其必需依赖。
安装失败时会保留屏幕上的错误信息并退出，不会删除或替换现有虚拟环境。
安装完成后，请双击 `update.bat`，等待更新到最新版本后，
再使用 `boot_LoomStroke.bat` 启动。

`install.bat` 默认也会安装 NanoBanana 的附加依赖（`google-genai`）。原有的 `--nanobanana` 选项仍可使用。
自动执行时可添加 `--no-pause`；如需指定创建新虚拟环境所用的 Python，
请添加 `--python "C:\path\python.exe"`。
使用 `--no-python-download` 可禁用 Python 自动下载。
`.runtime/` 和 `.venv/` 用于启动应用，安装后请将它们保留在原位置。

如需手动安装，请在仓库根目录执行：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m loomstroke
```

在 macOS / Linux 上，请使用 `source .venv/bin/activate` 激活虚拟环境。

绘图必需的 `wgpu==0.31.0` 已包含在依赖定义中，会与 `rendercanvas` 一起自动安装。
它也列在 `requirements.txt` 中，无需单独安装。
该文件是固定版本的运行依赖清单，包含 NanoBanana 所需的附加 SDK，
不包含测试、格式化、类型检查或构建工具。

如需安装 NanoBanana 的附加依赖，请在同一虚拟环境中执行：

```powershell
python -m pip install -e ".[nanobanana]"
```

`keyring` 是必需依赖。API 密钥通过 `SecretProvider` 使用操作系统的凭据存储。
如果该存储不可用，付费网关将拒绝保存和读取 API 密钥。
连接外部服务需要通过应用设置中的 `privacy/allow_external_services` 授权。
AI Selection 所需的 SAM3.1 运行环境和模型权重不随应用附带，使用相关功能时需另行准备。

## 说明文档

- [用户指南入口](docs/user/index.html)
- [图文使用手册（12种语言）](docs/user/manual/index_zh_CN.html)
- [SAM 3.1安装指南（12种语言）](docs/user/manual/sam31_zh_CN.html)
- [日文手册](docs/user/index_ja.html)
- [安装与启动](docs/user/INSTALL_AND_RUN.md)
- [启动方法与故障排除](docs/user/RUN_INSTRUCTIONS.md)

说明文档反映了截至 2026 年 10 月 22 日的开发版，包含 CMYK 绘画、图层样式、
RGB/CMYK 颜色面板、矢量描边宽度编辑等内容。
也包含沿路径排列文字、文字图层自动命名、不启用 AI 的启动方式、ZIP 更新、ComfyUI 参数显示与
退出处理、漫画分格变更后的即时撤销/重做，以及移出屏幕的浮动面板恢复。

可从主菜单“滤镜”中选择与图层滤镜相同的 11 种滤镜，并直接应用到目标图层。
对于矢量和文字图层，会显示栅格化确认；在滤镜设置中点击确定后，会将栅格化与滤镜一起应用。
取消确认或设置时会保留原图层，应用后也可通过一次撤销恢复。

## 源代码结构

- `src/loomstroke/`：应用本体、内置插件、渲染处理及运行资源
- `img/`：应用标志和图标
- `docs/user/`：用户指南
- `LICENSES/` 和 `THIRD_PARTY_LICENSES`：第三方许可证声明

不包含开发仓库历史、非公开测试素材、测试套件、内部开发文档、
用户数据、API 密钥、模型权重、虚拟环境或已构建的分发包。
公开范围请参阅 [PUBLICATION.md](PUBLICATION.md)。

## 第三方组件

PySide6 / Qt 按 LGPL-3.0 条款使用。
LoomStroke 自有许可证不限制第三方许可证授予的权利，
例如替换动态链接库的权利。

Mapbox earcut 的移植部分采用 ISC License，附带的 ICC 配置文件遵循各自的授权条款。
详情请参阅 [THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES)、[LICENSES](LICENSES/)，
以及 `src/loomstroke/io/resources/icc/` 中的声明。

在托管平台上进行浏览、fork 等操作时，也适用该平台的服务条款。
许可申请的联系方式见 LICENSE。
