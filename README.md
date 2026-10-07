# XingRen — 网页元数据抓取看板

抓取网页元数据（标题、缩略图、favicon、结构化详情字段），以 Raindrop 风格的卡片看板展示。
基于 [Scrapling](https://github.com/D4Vinci/Scrapling)（TLS 指纹伪装 + 三级降级抓取），数据存 SQLite，界面为本地 Web 应用。

## 功能

- **三级降级抓取**：`Fetcher`（TLS 指纹）→ `DynamicFetcher`（JS 渲染）→ `StealthyFetcher`（反 Cloudflare），配代理失败自动回退直连
- **元数据提取**：标题按 `og:title → twitter:title → <title>` 优先级；缩略图 `og:image`；favicon `link[rel*="icon"]`，相对路径自动补全
- **详情字段解析**：抓取时顺带解析 `.space-y-2 > *` 中的「标签: 值」结构（发行日期、番号、类型链接等），在看板「详情」弹层展示
- **Raindrop 看板**：卡片网格（缩略图在上、标题在下）、搜索过滤、添加 / 重命名 / 重新抓取 / 删除
- **服务端图片代理**：缩略图与 favicon 经本地代理加载，避免浏览器直连外站被重置
- **SQLite 存储**：`metadata.db`，首次启动自动导入历史 `metadata.json`；合并规则保证抓取失败不覆盖已有数据和手动补充的标题

## 目录结构

```
XingRen/
├── metadata_fetcher.py   # 核心：三级降级抓取 + 元数据/详情提取
├── fields.py             # 「标签: 值」字段解析（主项目与 web_demo 共用）
├── records.py            # SQLite 存储层（含 JSON 历史数据自动导入）
├── webui/                # 主界面：Raindrop 看板（端口 4509）
│   ├── server.py         # 静态托管 + REST API
│   └── index.html / style.css / app.js
├── web_demo/             # 独立演示：CSS 选择器试验台（端口 8765）
│   ├── server.py         # 静态托管 + /api/fetch（输入网址按选择器提取）
│   └── index.html / style.css / app.js / fixture.html
├── metadata.db           # SQLite 数据库（运行时生成，已 gitignore）
├── metadata.json         # 历史数据备份（只读，导入后不再更新）
└── requirements.txt
```

## 环境准备

```bash
# 1. conda 环境（本项目使用 Ximages）
conda activate Ximages

# 2. 安装依赖（可加清华源）
pip install -r requirements.txt -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple

# 3. 下载浏览器内核（DynamicFetcher / StealthyFetcher 需要）
patchright install chromium
```

## 使用

### 看板（主界面）

```bash
python webui/server.py          # 默认 http://127.0.0.1:4509/
python webui/server.py --port 8000   # 自定义端口
```

- 顶部粘贴 URL 回车「添加」；右侧搜索框过滤；代理框默认 `http://127.0.0.1:7892`（留空直连）
- 卡片悬停显示操作条：**详情 / 重命名 / 重新抓取 / 删除**
- 点缩略图或标题在新标签打开链接

### 选择器试验台（演示）

```bash
cd web_demo
python server.py                # http://127.0.0.1:8765/
```

输入网址 + CSS 选择器（如 `.space-y-2 > *`），服务端抓取并展示匹配元素与字段卡片；
也可用 `http://127.0.0.1:8765/fixture.html` 快速体验「标签: 值」解析。

## API（webui）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/records` | 全部记录（按插入顺序） |
| POST | `/api/fetch` | `{url, proxy}` 抓取并合并入库，返回记录 |
| PATCH | `/api/record` | `{url, title}` 重命名 |
| DELETE | `/api/record?url=…` | 删除记录 |
| GET | `/api/img?src=…&proxy=…` | 服务端代抓图片（缩略图/favicon 用） |

## 说明

- **代理**：抓取外国网站时在界面填本地代理（Clash 等，默认 7892）；代理不可用会自动回退直连再试整条降级链
- **合并规则**：按 URL 去重；抓取失败保留原记录；页面无标题时不覆盖手动补充的标题
- **数据迁移**：`metadata.db` 为空且存在 `metadata.json` 时自动导入；之后数据只写库，JSON 留作备份
- **调试抓取链**：抓取日志会打印到启动 `server.py` 的终端
