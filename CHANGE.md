# CHANGE.md

变更记录，倒序（最新在上）。每条含三字段：**修改时间 / 用户名 / 系统名**。

维护约定：每完成一批修改即追加一条，按"修改"计（未 commit 的改动同样记录）。
字段取值：时间 `date "+%Y-%m-%d %H:%M:%S %z"`；用户 `git config user.name`；
系统 `/etc/os-release` 的 `NAME`+`VERSION` · `uname -srm` · `hostname`。

---

## 2026-10-08 17:05:22 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: 数据库重构——域名独立管理 + 多对多标签（5 文件，+543 / -255）

  - `records.py`（重写）：新建 `domains` / `tags` / `record_tags` 三张表；
    迁移 `group_name` → tags（一次性，完成后删除 `group_name` 列）；
    自动从现有记录提取域名填充 `domains` 表；
    新增 domains/tags/record_tags 完整 CRUD 函数；
    `_row_to_record` 返回 `domain`（从 URL 提取）和 `tags` 列表
  - `server.py`（重写）：新增 `GET /api/domains`、`GET /api/tags`、
    `POST /api/tag`、`DELETE /api/tag`、`POST /api/record/tag`、
    `DELETE /api/record/tag`、`PATCH /api/domain` 端点；
    fetch/img 端点按域名查代理（`get_domain_proxy` 优先于全局代理）；
    移除 `group_name` 相关逻辑
  - `app.js`（重写）：state 改为 `selectedDomain` + `selectedTag` 双维度过滤；
    `buildSidebar` 改为域名+标签两段列表（点击切换过滤，再点取消）；
    卡片操作条「分组」→「标签」；`openTag` 弹窗显示所有标签，✓/+ 切换关联；
    新增 `openDomain` 域名管理弹窗（编辑别名和代理）；
    `render()` 按域名+标签组合过滤，按域名分组显示
  - `index.html`：替换分组弹窗为标签弹窗（标签列表+新建输入）+ 域名管理弹窗
  - `style.css`：`.sidebar-label`（域名/标签分段标题）、`.tag-item`/`.tag-toggle`
    （标签选择项）、`.tag-badge`（卡片标签徽标）、`.domain-form`（域名表单）

  验证：JS 语法 / Python 编译 / 数据库迁移（group_name 删除，domains 11 条自动填充）/
  服务启动 200 / API 全部 200 / 前端 25 处新功能引用。

## 2026-10-08 16:12:38 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: 域名下再分组——用户自定义子分组（7 文件，+294 / -46）

  - `records.py`：schema 加 `group_name TEXT NOT NULL DEFAULT ''`；
    `_ensure()` 迁移逻辑（`ALTER TABLE` 兼容已有数据库）；
    `_row_to_record` 解析；`upsert_record` 保留分组名（`CASE WHEN … = ''` 不覆盖）；
    新增 `set_group_name(url, group_name)` —— 与 `set_title` 同模式
  - `server.py`：`PATCH /api/record` 扩展为同时处理 `title` 和 `group_name`；
    import 加 `set_group_name`
  - `app.js`：state 加 `selectedGroup` / `groupTarget`；
    卡片操作条加「分组」按钮（详情/重命名/**分组**/重新抓取/删除）；
    卡片域名行加 `.group-badge` 显示分组名；
    `buildSidebar` 改为域名 → 子分组两级树（点击域名展开子分组列表，
    子分组缩进显示；「未分组」作为默认子分组名）；
    `render()` 三级分支：选中子分组 → 平铺；选中域名 → 子分组分组；
    全部 → 域名 → 子分组两级分组（单域名单子分组时退化为平铺）；
    新增 `openGroup` / `submitGroup` 弹窗逻辑；`init()` 加分组弹窗事件
  - `index.html`：加分组弹窗（`#groupMask` + `#groupInput`）
  - `style.css`：`.sidebar-domain` / `.sidebar-sub`（缩进）/ `.group-badge`（徽标）/
    `.sub-group-header` / `.domain-content`

  验证：JS 语法 / Python 编译 / 数据库迁移（23 条记录兼容，group_name 读写正确）/
  服务启动 200 / HTML groupMask / JS 17 处分组引用。

  TODO 第 6 项完成，移入「已完成」。

## 2026-10-08 15:38:55 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: TODO.md 第 6 项改为「域名下再分组（用户自定义子分组）」，替代原 Raindrop 标签方案

  - 不采用 Raindrop 的扁平标签，改为 域名 → 子分组 两级层级
  - 数据库：`records` 表加 `group_name TEXT NOT NULL DEFAULT ''` 列，
    与 `title` 同逻辑保留（抓取不覆盖手动分配的分组）
  - UI：侧边栏改为域名 → 子分组两级树；卡片显示子分组名；
    操作菜单分配/修改子分组；「未分组」作为默认

## 2026-10-08 15:32:09 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: TODO.md 新增第 6 项——标签数据库设计（JSON TEXT 列方案已定）

  - 方案：`tags TEXT NOT NULL DEFAULT '[]'`，与现有 `details` 同构，
    用 `json.loads` / `json.dumps` 读写，SQLite `json_each()` 可展开查询
  - 不需要额外 `tags` 表 / `record_tags` 关联表（个人工具规模过度工程化）
  - 改动范围已列出：records.py / fetcher.py / server.py / app.js / style.css

## 2026-10-08 15:25:17 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: 导入性能优化——并发抓取 + 跳过已存在（`app.js`，`importFromFile` 重写）

  - 并发度 5：用 `Promise.all` + worker 池模式同时处理 5 条 URL，
    替代原来逐条串行。100 条 × 20s：33 分钟 → 约 7 分钟
  - 跳过已存在：用 `Set` 收集 `state.records` 中已有 URL，
    导入前过滤掉已存在的，状态栏显示跳过数量
  - 文件内去重：`[...new Set(parseUrls(...))]` 去除重复 URL

  验证：JS 语法通过；CONCURRENCY = 5，worker 池 + Promise.all 模式正确。

## 2026-10-08 15:18:22 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: 导入功能支持 HTML 文件（Raindrop.io / Netscape Bookmark 格式）

  - `index.html`：`<input accept=".txt,.html,.htm">`
  - `app.js`：新增 `parseUrls(text, fileName)` 按文件后缀分流——
    `.html` / `.htm` 用 `DOMParser` 解析，提取所有 `<A HREF="...">` 的 href；
    其余按 TXT 逐行过滤 `http` 开头的行
    导入逻辑不变：逐条 POST `/api/fetch`，进度实时显示

  验证：JS 语法通过；Raindrop HTML 示例 3/3 URL 提取正确。

## 2026-10-08 15:08:43 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: 支持从 TXT 文件批量导入 URL（`app.js` + `index.html`，+38）

  - `index.html`：toolbar 加「导入」按钮（`btn-ghost`）+ 隐藏 `<input type="file" accept=".txt">`
  - `app.js`：新增 `importFromFile(file)`——读取文件文本，按行分割，
    过滤以 `http://` 或 `https://` 开头的行作为 URL，逐条 POST `/api/fetch`，
    每条完成后实时渲染，状态栏显示进度（`导入中 3/50（成功 2，失败 0）`），
    完成后汇总成功/失败数；按钮在导入期间 disabled 防重复点击

  验证：JS 语法通过；解析测试 5/5 正确（跳过注释、空行、非 URL 行）。

## 2026-10-08 14:52:37 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: 左侧域名分类导航栏（`index.html` + `style.css` + `app.js`，+172 / -32）

  - `index.html`：toolbar 加 `☰` 折叠按钮；`<main>` 内加 `<aside id="sidebar">`；
    `<div id="board">` 包一层 `<div class="board-wrap">` 独立控制 padding 和滚动
  - `style.css`：`<main>` 从单列 padding 改为 `display: flex` 布局；
    sidebar 宽 220px，`.collapsed` 宽度归零 + `overflow: hidden`，过渡动画 0.2s；
    侧边栏项（`.sidebar-item`）悬停/选中高亮，计数徽标圆角灰底；
    卡片区移入 `.board-wrap` 独立滚动
  - `app.js`：新增 `state.selectedDomain`（null = 全部）和 `buildSidebar()`——
    从 `state.records` 实时提取域名分组，按计数降序排列，点击设置选中并重新渲染；
    `render()` 根据 `selectedDomain` 分支：选中 → 平铺该域名记录，
    全部 → 保留原有域名分组+折叠逻辑；`init()` 加 sidebarToggle 点击事件

  验证：JS 语法通过；服务启动 200；HTML/JS 中 sidebar / buildSidebar 引用完整。

## 2026-10-08 14:35:42 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: 域名分组改为可折叠（`app.js` + `style.css`，解决大组数百上千条的展示问题）

  - `app.js`：域名头加 `▸` 箭头标记，`recs.length > 20` 的组默认收起，
    点击域名头 `classList.toggle("collapsed")` 切换展开/收起
  - `style.css`：`.collapsed .domain-grid { display: none }` 隐藏卡片网格，
    箭头 `rotate(90deg)` 表示展开状态，收起时边框改为虚线以视觉区分

  验证：JS 语法通过；12 条 CSS 域名规则完整。

## 2026-10-08 14:23:18 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: 域名分组改为按可注册域名聚合（`app.js`，2 处改动）

  - 新增 `rootDomain(host)` 函数：取主机名最后 2 段作为分组键，
    对 `.com.cn` / `.net.cn` / `.org.cn` 等多段后缀取最后 3 段
  - `render()` 分组键从 `hostOf(r.url)` 改为 `rootDomain(hostOf(r.url))`：
    `chat.deepseek.com` / `platform.deepseek.com` / `fe-static.deepseek.com` /
    `cdn.deepseek.com` → 同归 `deepseek.com` 组（4 条）

  验证：13 个真实 URL 分组测试 + 5 个多段后缀边界用例全部通过。

## 2026-10-08 13:56:50 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: 国内网站自动跳过代理直连（后端 `fetcher.py`，1 处改动）

  - 新增 `_is_domestic(url)` 判断：匹配 `.cn` 及其二级后缀（`.com.cn` /
    `.net.cn` / `.org.cn`），以及 30 个常见国内域名（baidu / bilibili / zhihu /
    taobao / jd / 163 / aliyun / mi.com 等），子域名自动匹配（`mimo.mi.com` → `mi.com`）
  - `fetch_page()` 开头加一行：`if proxy and _is_domestic(url): proxy = None`
    代理为空后三级降级链照常走直连，无需改后续逻辑
  - IP 地址（如 `120.26.238.72`）不会被误判为国内域名

  验证：15 个用例全部通过（10 个 True + 5 个 False）；编译通过。

  **追加修复**（同一批次）：`/api/img` 图片代理走的是 `server.py` 的
  `handle_img`（`curl_cffi.requests.get`），不经过 `fetch_page`，所以
  国内域名判断不生效——`mimo.mi.com` 等国内站图片仍走 `127.0.0.1:7892`（不存在）→ 502。
  修法：`server.py` import `_is_domestic`，`handle_img` 请求前加同样的
  `if proxy and _is_domestic(src): proxy = None`。
  补充 `deepseek.com`、`xiaomimimo.com`、`hdslb.com`（B站CDN）、
  `127.net`（网易CDN）、`netease.com`、`kimi.com` 到国内域名列表。
  18 个图片 URL 用例全部通过（国内 15 个直连、外国 3 个走代理）。

## 2026-10-08 13:48:11 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: 看板按域名自动分组展示（纯前端，后端零改动）

  - `app.js`：`render()` 重写，用 `Map` 按 `hostOf()` 分组，组按记录数降序排列；
    0 或 1 个域名时退化为原平铺（不显示域名头），多域名才渲染分组区块；
    状态栏追加域名计数（`X 条匹配，Y 个域名`）
  - `style.css`：新增 `.domain-group` / `.domain-header` / `.domain-name` /
    `.domain-count` / `.domain-grid` 样式，域名头带下划线分隔 + 圆角计数徽标
  - 搜索过滤行为不变：过滤后重新分组，空域名组自动消失
  - 不入库、不改 schema、不改后端——域名从 URL 实时提取

  验证：JS 语法检查通过；服务启动正常；`render()` 中 `domain-group` /
  `domain-header` / `domain-grid` / `sorted.length` 逻辑完整；
  当前仅 1 个域名（mimo.mi.com），前端正确退化为平铺。

## 2026-10-08 12:27:13 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: 新增 `TODO.md` 未完成事项清单

  - 第 1 项为 README 部署说明（域名 A 记录 → Caddy/nginx 终结 HTTPS →
    看板改绑 `--host 127.0.0.1` → 安全组只放行 80/443），等域名下来照配
  - 待办 5 项均来自本次会话实际遇到的问题：看板加 token 鉴权、
    试验台补 `--host`/`--port`、服务器侧 `setup.cfg` 与 `packaging` 遗留项、
    抓取代理默认值不可持久化
  - 已完成 1 项：`0.0.0.0:4000` 那批改动已由 `62e8a2e` 于 11:01:41 提交。
    **勘误**：本条初稿将其列为第 3 项待办，系沿用了 11:00 的旧 `git status`
    而未重新核对，实际早已提交，现移入「已完成」并重排编号
  - CLAUDE.md 补一句维护约定：待办统一记在 `TODO.md`，完成时勾选移入
    「已完成」，勾选与改动本身都要在 CHANGE.md 留痕

## 2026-10-08 11:00:16 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: 看板服务默认监听改为 `0.0.0.0:4000`（原硬编码 `127.0.0.1:4509`）

  - `xingren/web/webui/server.py` 新增 `--host` 参数，与既有 `--port` 一样取 `--name value` 形式；
    默认值改为 `0.0.0.0` / `4000`，启动横幅区分外网地址与本机地址并提示可用 `--host 127.0.0.1` 收窄
  - 启动横幅与请求日志 `print` 加 `flush=True`：stdout 重定向为块缓冲，
    否则 `nohup` 启动后 `tail -f` 看不到启动信息与访问日志
  - README / CLAUDE.md 同步端口与 `--host` 用法，并补「监听地址」与
    「⚠ 无鉴权」两条说明（`/api/fetch`、`/api/img` 会由服务器侧发起任意 URL 请求，
    `DELETE /api/record` 可删数据；对外暴露需在云安全组/防火墙放行 4000 并限制来源 IP）

  验证：默认启动后 `127.0.0.1:4000` 与网卡 IP `192.168.254.20:4000` 均 200、
  `ss` 显示 `LISTEN 0.0.0.0:4000`；`--host 127.0.0.1` 下网卡地址拒绝连接（000）；
  `xingren-webui --port 4002` 返回 7 条记录；重定向日志即时可见。
  选择器试验台未改动，仍为 `127.0.0.1:8765`。

## 2026-10-08 10:04:42 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: 标准化项目架构为 `xingren` 包结构，并建立本变更记录约定

  - 新增 `pyproject.toml`（setuptools）：依赖动态读取 `requirements.txt`，
    控制台入口 `xingren-webui` / `xingren-demo`，静态资源经 `package-data` 随 wheel 分发
  - 平铺模块迁入包内：`metadata_fetcher.py`→`xingren/core/fetcher.py`、
    `fields.py`→`xingren/core/fields.py`、`records.py`→`xingren/core/records.py`；
    `webui/`→`xingren/web/webui/`、`web_demo/`→`xingren/web/demo/`，各层补 `__init__.py`
  - 删除两个 server 里的 `sys.path.insert(0, …)` 黑魔法，统一改为绝对 import
  - 数据文件路径改为回溯仓库根（editable 安装下与原行为一致），
    新增 `XINGREN_DATA_DIR` 环境变量兜底非 editable 安装的错误落点
  - 清除 `metadata.json` 自动导入死代码：源文件已于 `bf4f40b` 删除，
    `JSON_PATH` 与 `_ensure()` 中的导入分支永不触发，连同 `_initialized` 标志一并移除
  - 新增 `CHANGE.md`，并把维护约定写入 `CLAUDE.md` 供后续会话延续
  - 文档同步：README / CLAUDE.md 更新目录树与启动命令（改为 `-m` / 控制台入口），
    修正环境名 `Ximages`→`xingren`（实际存在的 conda 环境名为 xingren），
    移除已过期的 metadata.json 描述；各模块 docstring 中的路径引用一并更新
  - `.gitignore` 追加 `dist/`、`build/`、`*.egg-info/`（打包产物）

  验证：包导入 / 数据路径 / fixture 离线解析（8 元素 → 8 字段）与重构前基线一致；
  `python -m`、`xingren-webui`、`xingren-demo` 三条入口实测通过，
  `/api/records` 返回 7 条记录，静态资源 200，目录穿越 404；
  wheel 内含 7 个静态文件。用户 7 条记录未受影响，数据文件全程未移动。
