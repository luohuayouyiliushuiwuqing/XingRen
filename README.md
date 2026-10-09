# XingRen — 网页元数据抓取看板

抓取网页元数据（标题、缩略图、favicon、结构化详情字段），以 Raindrop 风格的卡片看板展示。
基于 [Scrapling](https://github.com/D4Vinci/Scrapling)（TLS 指纹伪装 + 三级降级抓取），数据存 SQLite，界面为本地 Web 应用。

## 功能

- **三级降级抓取**：`Fetcher`（TLS 指纹）→ `DynamicFetcher`（JS 渲染）→ `StealthyFetcher`（反 Cloudflare），配代理失败自动回退直连
- **元数据提取**：标题按 `og:title → twitter:title → <title>` 优先级；缩略图 `og:image`；favicon `link[rel*="icon"]`，相对路径自动补全
- **详情字段解析**：抓取时顺带解析 `.space-y-2 > *` 中的「标签: 值」结构（发行日期、番号、类型链接等），在看板「详情」弹层展示
- **Raindrop 看板**：卡片网格（缩略图在上、标题在下）、左侧栏按域名/标签筛选、搜索过滤、添加 / 重命名 / 重新抓取 / 删除
- **导入 / 导出**：TXT 与 Raindrop HTML 导入——HTML 走**快照入库**（不联网、5000 条秒级完成），详情字段在打开时按需补抓；当前可见记录可导出 TXT / HTML
- **域名分组与代理映射**：按根域名分组（零散域名收进「其他」），URL 通配符规则 + 域名级开关只表达「要不要走代理」，地址统一填一次；走代理的域名在侧栏带「代理」徽标
- **服务端图片代理 + 本地缓存**：缩略图与 favicon 经本地代理加载（避免浏览器直连外站被重置），结果落盘缓存，二次加载不再走网络
- **SQLite 存储**：`metadata.db`（存放位置可在界面「存储」面板里点选迁移），合并规则保证抓取失败不覆盖已有数据和手动补充的标题

## 目录结构

```
XingRen/
├── pyproject.toml         # 打包配置：依赖来源、控制台入口
├── xingren/               # Python 包（绝对 import，无 sys.path hack）
│   ├── core/              # 与界面无关的核心逻辑
│   │   ├── fetcher.py     # 核心：三级降级抓取 + 元数据/详情提取
│   │   ├── fields.py      # 「标签: 值」字段解析（看板与试验台共用）
│   │   └── records.py     # SQLite 存储层
│   └── web/
│       ├── webui/         # 主界面：Raindrop 看板（默认 0.0.0.0:4000）
│       │   ├── server.py  # 路由分发 + REST API
│       │   ├── staticfiles.py / imgproxy.py / fsbrowse.py
│       │   │              # 静态文件解析 / 图片代抓 / 目录浏览（都不碰 socket）
│       │   ├── index.html / style.css
│       │   └── js/        # 12 个原生 ES 模块（无构建步骤），入口 main.js
│       └── demo/          # 独立演示：CSS 选择器试验台（端口 8765）
│           ├── server.py  # 静态托管 + /api/fetch（输入网址按选择器提取）
│           └── index.html / style.css / app.js / fixture.html
├── metadata.db            # SQLite 数据库（运行时生成，已 gitignore，位置可在界面迁移）
├── config.json            # 记录当前存储目录（运行时生成，已 gitignore）
├── CHANGE.md              # 变更记录
├── CLAUDE.md              # Claude Code 使用指引（约定不入库）
└── requirements.txt       # 全量 pinned 依赖（同时是 pyproject 的依赖来源）
```

## 环境准备

```bash
# 1. conda 环境（本项目使用 xingren）
conda activate xingren

# 2. 安装依赖（可加清华源）
pip install -r requirements.txt -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple

# 3. 下载浏览器内核（DynamicFetcher / StealthyFetcher 需要）
patchright install chromium

# 4. 安装本项目（editable；依赖已在第 2 步装好，故 --no-deps 跳过解析，
#    --no-build-isolation --no-index 全程不联网）
pip install -e . --no-deps --no-build-isolation --no-index
```

## 使用

### 看板（主界面）

```bash
xingren-webui                              # 默认 http://0.0.0.0:4000/
xingren-webui --port 8000                  # 自定义端口
xingren-webui --host 127.0.0.1             # 只监听本机（不对外暴露）
xingren-webui --host 0.0.0.0 --port 4000   # 显式指定

# 免安装方式（必须在仓库根目录执行）
python -m xingren.web.webui.server [--host 0.0.0.0] [--port 4000]
```

- 顶部粘贴 URL 回车「添加」；右侧搜索框过滤；`☰` 收起 / 展开左侧栏
- 左侧栏按**域名**与**标签**筛选看板：条目数 <10 的零散域名收进「其他」，走代理的域名带「代理」徽标
- 卡片悬停出现两处操作——缩略图右上角 **详情 / 重新抓取**，底部 **重命名 / 标签 / 删除**
- **导入** 支持 TXT（逐条抓取）与 Raindrop HTML（快照秒级入库，点开详情时按需补抓）；**导出** 按当前可见记录导出 TXT / HTML
- **代理** 打开代理面板：填全局代理地址、配 URL 通配符规则与域名级开关；打开页面自动探测本机 7889-7899 端口
- **存储** 打开存储面板：查看并迁移数据库、图片缓存的存放目录（整体迁移，立即生效）
- 点缩略图或标题在新标签打开链接

### 选择器试验台（演示）

```bash
xingren-demo                        # http://127.0.0.1:8765/

# 免安装方式（必须在仓库根目录执行）
python -m xingren.web.demo.server
```

输入网址 + CSS 选择器（如 `.space-y-2 > *`），服务端抓取并展示匹配元素与字段卡片；
也可用 `http://127.0.0.1:8765/fixture.html` 快速体验「标签: 值」解析。

## API（webui）

全部接口**无鉴权**，风险见下方「⚠ 无鉴权」。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/records` | 全部记录（含标签与 `fetched` 快照标记） |
| POST | `/api/fetch` | `{url, proxy}` 抓取并合并入库，返回记录 |
| POST | `/api/records/quick` | `{items:[{url,title,thumbnail,favicon,tags}]}` 快照批量入库，不联网 |
| PATCH | `/api/record` | `{url, title}` 重命名 |
| DELETE | `/api/record?url=…` | 删除记录 |
| GET | `/api/img?src=…&proxy=…` | 服务端代抓图片（缩略图/favicon 用，命中本地缓存不走网络） |
| GET | `/api/domains` | 域名列表（`display_name`、`need_proxy` 三态：null 跟随全局 / 1 用代理 / 0 直连） |
| PATCH | `/api/domain` | `{name, display_name?, need_proxy?}` 更新域名设置 |
| GET | `/api/tags` | 标签列表 |
| POST | `/api/tag` | `{name}` 新建标签 |
| DELETE | `/api/tag?id=…` | 删除标签 |
| POST | `/api/record/tag` | `{url, tag_id}` 给记录打标签 |
| DELETE | `/api/record/tag?url=…&tag_id=…` | 移除记录标签 |
| GET | `/api/proxy-rules` | URL 通配符规则列表 |
| POST | `/api/proxy-rule` | `{pattern, need_proxy}` 新增 / 更新规则 |
| DELETE | `/api/proxy-rule?id=…` | 删除规则 |
| GET | `/api/proxy-domains` | 最终会走代理的根域名（侧栏徽标数据源） |
| GET、POST | `/api/proxy-detect` | 探测本机 7889-7899 代理端口，返回地址或 `null` |
| GET | `/api/storage-dir` | 当前存储目录 / 数据库 / 缓存路径（旧 `/api/db-path` 等价） |
| POST | `/api/storage-dir` | `{path, migrate}` 切换存储目录并整体迁移本地数据 |
| GET | `/api/fs/list?path=…` | 目录浏览（空 path 返回盘符 / 根，供界面点选目录） |

## 说明

- **代理**：映射只表达「要不要走代理」，地址统一填在「代理」面板的全局代理里（Clash 等，默认 `http://127.0.0.1:7897`，打开页面自动探测本机 7889-7899）。优先级 **URL 模式规则 > 域名规则 > 全局默认**，命中即为强制值（含「强制直连」）；国内域名自动跳过代理，代理不可用会自动回退直连再试整条降级链
- **合并规则**：按 URL 去重；抓取失败保留原记录；页面无标题时不覆盖手动补充的标题
- **数据文件**：本地私有数据（`metadata.db`、图片缓存 `cache/img/` 等）统一放在**存储目录**——默认仓库根（已 gitignore），可在界面「存储」面板点选目录整体迁移，立即生效；位置记录在仓库根 `config.json`（已 gitignore）。非 editable 安装可用环境变量 `XINGREN_DATA_DIR` 指定基础目录
- **入口约定**：只支持 `xingren-webui` / `xingren-demo` 或 `python -m …`（后者需在仓库根目录执行）；不要直接 `python xingren/web/webui/server.py`，它依赖 `sys.path`，行为随安装状态变化
- **监听地址**：看板默认 `0.0.0.0:4000`（所有网卡，便于远程访问），可用 `--host` / `--port` 覆盖；选择器试验台仍固定 `127.0.0.1:8765`
- **⚠ 无鉴权**：上表全部接口都对外可达。`POST /api/fetch` 与 `GET /api/img` 会让**服务器**去抓取任意 URL（SSRF），`DELETE /api/record` 可直接删库，`GET /api/fs/list` 可列出服务器任意目录、`POST /api/storage-dir` 可把数据库搬走或重建。绑到 `0.0.0.0` 意味着任何能连上该端口的人都能调用这些接口。仅本机使用请加 `--host 127.0.0.1`；确需对外暴露时，在云安全组 / 防火墙上只放行可信 IP（云主机上还需放行安全组与 `firewalld`/`ufw` 的 4000 端口，否则外部访问会超时）；token 鉴权见 [TODO.md](TODO.md) 第 2 项
- **调试抓取链**：抓取日志会打印到启动命令所在的终端
