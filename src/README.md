# 源码说明

本目录包含译匠 Windows 桌面版的主要 Python 源码、前端静态文件和测试。

## 许可

- 代码：MIT，见仓库根目录 `LICENSE`；
- 169 / 260 知识库数据、内置规则/案例/术语：**不在 MIT 范围内**，见 `LICENSE-KNOWLEDGE-BASE.md`。

## 未包含的内容

开放源码仓库**不包含**：

- 169 / 260 付费知识库 JSON；
- `data/` 下的本地数据库、API Key、用户知识库和备份；
- `resources/` 下的随包资源与加密知识库；
- `native/` 下的窗口宿主二进制；
- 构建产物、安装包和下载文件。

因此，直接克隆本目录不能得到完整可运行环境；需要自备资源、配置自己的 API Key，并按下面方式准备数据目录。

## 环境

- Windows 10 / 11 x64
- Python 3.11+
- Node.js 仅用于前端资源（如需要）
- WebView2 Runtime（安装版需要）
- 依赖：`pip install -r requirements.txt`

## 开发运行

```powershell
python app.py
```

或使用桌面入口：

```powershell
python desktop.py
```

默认本地服务监听 `127.0.0.1`；首次运行会创建 `data/` 目录。

## 目录

```text
src/
├─ app.py               FastAPI 服务与 API
├─ desktop.py           Windows 桌面入口
├─ pipeline.py          翻译 / 审校 / 修复主流程
├─ retrieval.py         知识库检索
├─ v2_selector.py       规则选择与硬条件
├─ prompts.py           提示词
├─ config.py            配置与密钥存储
├─ store.py             SQLite 历史与缓存
├─ userkb.py            用户知识库与备份
├─ static/              前端页面
└─ tests/               单元测试
```

## 测试

```powershell
python -m unittest discover -s tests
```

测试可能需要准备 `resources/kb_v2.json` 等基线资源；开源仓库不包含付费知识库数据。

## 构建

安装包使用 PyInstaller onedir + Inno Setup。具体构建脚本和资源准备不在本开源目录内；可参考 `Yijiang.spec` 与 `installer_free.iss`。

## 说明

译匠软件本体免费；模型调用使用用户自己的 API Key；169/260 知识库是可选付费数据，不购买也能使用软件全部功能，也可以导入自己的知识库。
