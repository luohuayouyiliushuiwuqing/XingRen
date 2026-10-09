# CHANGE.md

变更记录，倒序（最新在上）。每条含三字段：**修改时间 / 用户名 / 系统名**。

维护约定：每完成一批修改即追加一条，按"修改"计（未 commit 的改动同样记录）。
字段取值：时间 `date "+%Y-%m-%d %H:%M:%S %z"`；用户 `git config user.name`；
系统 `/etc/os-release` 的 `NAME`+`VERSION` · `uname -srm` · `hostname`。

---

## 2026-10-09 09:42:44 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: 向 `_tmp` 存储目录注入 480 条测试数据（按约定保留、不删除），用于在本机看到侧栏「更多」与大列表表现（数据操作，无代码改动）

  **背景**：上一批的侧栏「更多」在本机只有 14 个域名时触发不了（常规窗口下 7 行装得下），
  用户明确授权「允许向 `_tmp` 数据内注入测试数据，允许不删除」。

  - 脚本 `/tmp/seed_test_data.py`（一次性，未入库），走项目自己的
    `insert_quick_records`（单事务，0.01s）写入 `config.json` 指向的 `_tmp/metadata.db`
  - **30 个大站 `siteNN.test` × 12 条 = 360**（侧栏独立分组），
    **60 个小站 `miniNN.test` × 2 条 = 120**（全部落进「其他」）；
    域名后缀用 RFC 2606 保留的 `.test`，永不解析
  - 全部打标签 **`测试数据`（480）**，大站再分半打 `测试A` / `测试B`（各 180）——
    日后要清掉时按标签一条 SQL 即可
  - 注入后 `UPDATE records SET fetched=1`（480 行）：详情弹层直接显示空状态，
    **不会**对这些假域名发起真实抓取
  - 缩略图复用库里已有真实 URL → 图片代理命中本地缓存，零网络请求
  - 顺带验证域名设置链路：`site03.test` / `site07.test` 置 `need_proxy=1`（侧栏出「代理」徽标）、
    `site05.test` 置别名「测试别名站」
  - **幂等**：URL 是主键，复跑 `{'inserted': 0, 'skipped': 480}`

  验收（playwright 实测）：库内 records **855**（375 真实 + 480 测试）、domains **104**、
  `fetched=0` **0 行**、record_tags 840；侧栏 41 行、**`更多 (21)`** 正常出现、
  展开后 `收起 ▴` 可滚；徽标 `site03.test` / `site07.test` / `其他` 均出；
  标签 `测试数据 480`、`测试A 180`、`测试B 180` 可筛；看板分组
  `bilibili.com / youtube.com / 其他 / site01.test …`；打开测试记录详情
  **`.test` 网络请求 0 条**、状态点绿；页面 0 报错 0 请求失败。

## 2026-10-09 09:35:57 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: 侧栏固定不随滚轮滚动，装不下的条目收进「更多」按需展开；顺带修掉 body 无高度上限导致的整页滚动（`app.js` / `style.css`，2 文件）

  **1. 布局根因（不修这个，「更多」根本测不出溢出）**

  - 实测 `body` 只有 `min-height: 100vh`，卡片一多 body 被内容撑到 **24890px**：
    `main` / `.board-wrap` / `.sidebar` 全部等高，`.board-wrap.scrollHeight === clientHeight`
    （**不是滚动容器**），整页靠文档滚动；此前「滚轮转发给看板」实际是
    `wrap.scrollTop += deltaY` 加在一个不滚动的元素上，空转
  - `style.css`：`body { min-height: 100vh → height: 100vh }` 封顶一屏。
    改后实测 body/html = 800、main = 737、`.board-wrap` 737 vs 24890（可滚）、
    侧栏 737、文档不再滚动；滚到底最后一张卡片底边 737 ≤ 状态栏顶 761（让位 padding 仍有效）

  **2. 侧栏「更多」收缩（`app.js` + `style.css`）**

  - `state.sidebarExpanded` + `applySidebarOverflow(sb)`：按 `offsetTop` 实测切分
    （先 `position: relative` 让 `offsetTop` 以侧栏为原点，含各元素外边距），
    装不下的条目 `hidden`，末尾补一行「更多 (N)」；预留高度 = `.sidebar-item` 的
    `offsetHeight`，`.sidebar-more` 用 5px padding + 1px border 凑到同为 33px，
    不带外边距——否则会压进状态栏让位区
  - 默认 `.sidebar { overflow-y: hidden }` + **滚轮被拦截并转发给看板**（侧栏纹丝不动）；
    点「更多」→ `expanded` 类 → `overflow-y: auto`，此时滚轮**不再拦截**、
    交还原生滚动（内容已超出一屏，用户主动点开才允许滚），并直接滚到首个被收起条目
  - 末尾按钮变「收起 ▴」；`buildSidebar` 开头保留 `scrollTop`，重渲（如
    `loadProxyDomains` 回来）不丢展开态滚动位置；窗口 resize 后 150ms 重测一次，
    否则缩小窗口底部条目被裁掉却没有「更多」
  - 顺带删掉 `app.js` 里排查用的 `console.info("[sidebar-fixed] wheel handler v2 active")`

  **3. 验收（playwright + chromium 实测）**

  - 收起态滚轮：`defaultPrevented=true`，看板 scrollTop 0→300，侧栏恒为 0
  - 展开态滚轮：`defaultPrevented=false`，`overflow-y: auto`；展开后 scrollTop=48（落点），
    `buildSidebar()` 重渲后 40→40 保留
  - 360px 窗口真实数据：`更多 (2)`、hidden 2、可见行底 282 ≤ 内容底 305，
    无一行越过状态栏顶 321；「更多」行高 33 == 条目行高 33
  - 注入 88 项模拟长列表：`更多 (69)`，展开后 2969 > 737 可滚、「收起」在末尾
  - 冒烟：域名筛选 227 卡、分组折叠/展开、详情弹层、代理面板、存储面板
    （显示 `_tmp`），**0 页面错误 / 0 请求失败**；JS 语法通过

## 2026-10-09 09:20:33 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: README 对齐当前实现——补全 API 表、修正过时的代理与数据文件描述（`README.md`，1 文件）

  **1. API 表补全（9 行 → 21 行，覆盖 `server.py` 全部 17 个路径）**

  - 新增：`POST /api/records/quick`（快照批量入库）、`GET/PATCH /api/domain`、
    `GET /api/tags`、`POST/DELETE /api/tag`、`POST/DELETE /api/record/tag`、
    `GET /api/proxy-rules`、`POST/DELETE /api/proxy-rule`、`GET /api/proxy-domains`、
    `GET、POST /api/proxy-detect`
  - 表头加一句「全部接口无鉴权」，与文末风险条目呼应

  **2. 修正过时描述**

  - 代理：`默认 7892` → `默认 http://127.0.0.1:7897`（对齐 `app.js` `state.globalProxy`），
    补「打开页面自动探测 7889-7899」；补优先级
    **URL 模式规则 > 域名规则 > 全局默认**（命中即强制值，含强制直连）与「国内域名自动跳过代理」
  - 「代理框」已从工具栏移除 → 改写为「代理」面板的用法；同时补上左侧栏筛选、
    卡片两处操作位置（右上 详情/重新抓取，底部 重命名/标签/删除）、导入导出、存储面板
  - 数据文件：图片缓存写死 `cache/` → `cache/img/`（对齐 `get_cache_dir()`），
    并注明 `config.json` 也已 gitignore
  - 无鉴权条目：补 `GET /api/fs/list` 可列服务器任意目录、
    `POST /api/storage-dir` 可搬动/重建数据库两项新面，并链到 TODO.md 第 2 项

  **3. 配套**

  - 功能列表补：导入/导出（HTML 快照 5000 条秒级入库）、域名分组与代理映射、图片本地缓存
  - 目录结构补 `config.json`；`CLAUDE.md` 标注「约定不入库」

  验证：表内每条路径与 `server.py` 路由一一比对无遗漏；`默认 7897`、
  `get_cache_dir()` 返回 `DATA_DIR/cache/img`、`exportTxt`/`exportHtml` 取
  `getVisibleRecords()`、`detectProxy({fill:true})` 挂在 `init()` 等声明均回读源码确认。

## 2026-10-08 21:54:18 +0800

- **用户**: XiaoWin
- **系统**: Microsoft Windows 11 专业版 · 10.0.26200 · AMD64 · 本机
- **内容**: 侧边栏固定加固 v2 + 静态文件禁缓存 + 清理三实例叠跑（`app.js` / `server.py`）

  **「滚轮固定侧栏没生效」根因（两层）**

  1. **服务进程叠罗汉**：4000 端口上同时活着 3 个实例——21:16 启动的老进程
     一直占着端口，此后每次「重启」新实例都绑不上端口（闲置或退出），
     导致服务端改动看似「不生效」；已全部杀掉，单实例重启并用裸 TCP 验收
  2. **浏览器旧 JS**：静态文件原本无任何缓存头，旧 tab 一直跑修改前的代码

  **修复**

  - `app.js`：滚轮监听从 sidebar 挂载升级为 **document 捕获阶段**
    （`capture:true + passive:false`，先于一切默认滚动行为，含 `sb.contains`
    判定与 collapsed 排除），并打印 `console.info("[sidebar-fixed] wheel handler v2 active")`
    供 F12 验证
  - `server.py`：静态响应加 `Cache-Control: no-store`，以后改完代码刷新即最新
  - 运维教训（记入项目记忆）：每次重启后必须核对**监听进程 PID 与启动时间**，
    确认旧进程真的被替换

  验收：裸 TCP 响应头含 `Cache-Control: no-store`；下发 app.js 含 v2 捕获监听与
  deltaY 转发；`/api/records` 正常（3821 条）；进程唯一（pid 16052 @21:53:43）。

## 2026-10-08 21:45:01 +0800

- **用户**: XiaoWin
- **系统**: Microsoft Windows 11 专业版 · 10.0.26200 · AMD64 · 本机
- **内容**: 侧边栏固定——滚轮落在域名列表上时改为滚动右侧看板（`app.js`，1 文件）

  - 根因：`.sidebar` 是 `overflow-y: auto` 的独立滚动容器，光标悬停其上
    滚动时域名列表整体下滑，与「固定侧栏」预期不符
  - 修法：`init()` 给 `#sidebar` 挂 `wheel` 监听（`passive:false` + `preventDefault`），
    把 `deltaY` 转发给 `.board-wrap.scrollTop`——滚轮永远只滚看板，侧边栏不动；
    171 个域名的长列表仍可通过其滚动条拖拽访问
  - 验证：JS 语法通过；线上 `app.js` 已含该逻辑（静态文件按请求读取，刷新生效）

## 2026-10-08 21:39:44 +0800

- **用户**: XiaoWin
- **系统**: Microsoft Windows 11 专业版 · 10.0.26200 · AMD64 · 本机
- **内容**: 修复域名规则标注失效（`is True` 整数恒假）；移除排查用临时计时（2 文件）

  **Bug 2 修复（用户确认后执行）**

  - `records.list_proxy_domains()`：`domain_need.get(name) is True` → 真值判断。
    SQLite 的 `need_proxy` 取出是整数 `1`，`1 is True` 恒为 False——
    导致域名管理里手动设的「用代理」永远不会触发侧边栏徽标，只有 URL 通配符规则生效
  - 验证：受控测试 `github.com` 设 need_proxy=1 → 出现在 `/api/proxy-domains`，
    还原 NULL → 移除；测试规则已还原，用户既有规则（office.com 等实时配置）不受影响

  **Bug 1 按用户决定不修（已知问题备案）**

  - `/api/proxy-domains` 在服务进程内执行需 16~20s（同函数直连 0.05s，
    根因未定位）——徽标数据加载慢但功能正确。排查用的 `[perf]` 计时打印
    随决定一并从 `server.py` 移除，服务已重启生效

  附带确立协作约定（写入项目记忆）：**发现 bug 先询问是否修复，不得直接修改；
  调查中的临时改动需事先说明。**

## 2026-10-08 21:21:44 +0800

- **用户**: XiaoWin
- **系统**: Microsoft Windows 11 专业版 · 10.0.26200 · AMD64 · 本机
- **内容**: 侧边栏「其他」合并与代理标记；存储目录语义升级（不存在则建、已有则直用）；接口异常兜底（6 文件）

  **1. 侧边栏（`app.js` + `style.css`）**

  - 域名列表同样执行 `< MIN_GROUP(10)` 合并：零散域名收进可点选的「其他」项
    （选中后 `state.selectedDomain="__other__"`，`getVisibleRecords` 按成员集合过滤，
    看板分组对该视图照常收拢；状态栏显示「其他」）
  - **走代理标记**：新增 `GET /api/proxy-domains`（`records.list_proxy_domains()`，
    判定优先级与 `_effective_proxy` 一致：URL 模式规则 > 域名规则；候选 =
    domains 表 ∪ 全部记录根域名）。命中域名在侧边栏显示蓝色「代理」徽标 +
    名称着色；「其他」项任一成员走代理也打标（title 显示成员数）
  - 模式规则 `*.example.com` 按设计不匹配顶级域名，而侧边栏是根域名——
    探测时同时用根域名与 `www.` 子域名，任一命中即算
  - 数据刷新时机：`loadProxyDomains()` 在初始化、改域名规则、增删模式规则后调用

  **2. 存储目录语义升级（`records.py` / `index.html` / `app.js`）**

  - **目录不存在 → 自动创建**（`_db()` 连接前 `mkdir`，文件夹被删/外置盘重挂后
    自动重建直接可用；替代上一版的报错方案）
  - **目标已有 metadata.db → 直接使用**（不覆盖、不迁移旧库、旧库留原处；
    无效 SQLite 仍拒绝；返回 `db_used_existing`，界面提示「已直接使用该目录现有的数据库」）。
    替代上一版「已有库则拒绝」
  - `GET /api/storage-dir` 增加 `exists` 字段；界面存储面板目录缺失时红框警告
  - **接口兜底**：server 四个 do_* 包一层 `_safe`——任何未捕获异常回
    500 JSON `{ok:false,error}`，不再直接掐断连接（此前存储目录失效导致
    所有 DB 接口连接重置、前端无从得知原因）

  **3. 现场问题记录**

  - 上一版 config 指向已删除的 `D:\XingRenCache_` → 重启后全部 DB 接口连接被掐；
    本次用 `_safe` + 自动创建 + `exists` 警告闭环
  - 测试期间用户正在实时切换存储目录，config 出现 `D:\XingRenCache` /
    `D:\XingRenCache_` 两个相邻目录交替：**3825 条真实数据在
    `D:\XingRenCache`（无下划线，1.0MB，含 171 域名/4 规则）**，
    `XingRenCache_` 为自动重建的空库——以用户面板最终选择为准，测试未再改动配置

  验证：存储语义单测 6 组全过（自动创建、目标无库迁移、目标有库直用且旧库保留、
  垃圾库拒绝、切回默认、清理）；`list_proxy_domains` 直连实测 6 域名命中
  （*.missav* / *.x.com* 等 4 条既有规则），`*.sspai.com` 不命中根域名符合设计、
  www 探测修复后规则优先级（模式直连压过域名规则）验证通过；页面 200、
  JS/Python 语法通过；测试用规则与 github 域名设置已还原。

## 2026-10-08 20:04:49 +0800

- **用户**: XiaoWin
- **系统**: Microsoft Windows 11 专业版 · 10.0.26200 · AMD64 · 本机
- **内容**: 零散域名合并为「其他」类别——单域名少于 10 条的收拢展示（`app.js`，1 文件）

  - `render()` 分组前加合并：域名记录数 `< MIN_GROUP(10)` 的全部归入
    **「其他」**一个分组（计数为合计），≥10 的域名各保持独立分组
  - **筛选到单一域名时不收拢**（`state.selectedDomain` 有值时跳过合并），
    否则点进一个只有 3 条的小域名会把自己的记录也吞进「其他」
  - 侧边栏域名列表不动，仍逐个列出、点击可单独筛选
  - 与上一条的默认展开/分片构建、平铺分支正交：合并后若只剩 1 个类别且
    ≤20 条，照旧走无表头平铺

  验证：JS 语法通过；线上 `app.js` 已含 `MIN_GROUP` 合并逻辑（静态文件按请求读取，
  刷新生效）。

## 2026-10-08 20:00:36 +0800

- **用户**: XiaoWin
- **系统**: Microsoft Windows 11 专业版 · 10.0.26200 · AMD64 · 本机
- **内容**: 看板域名分组默认展开，不再按条数自动收起（`app.js`，1 文件）

  - 根因：分组创建时 `recs.length > 20` 即加 `collapsed`——大组一进页面就是收起态，
    要点一下分组头才看到卡片
  - 改为**默认直接展开**：建组时不再自动加 `collapsed`；分组头点击的手动
    收起/展开保留
  - 性能兜底：原先「>20 收起」是为了避免一次建几千个卡片 DOM 卡死（18:39 条目），
    改用 `appendCardsChunked()` 分片构建——每帧 100 张、`requestAnimationFrame` 递进，
    首屏立即可见、后台补完；`grid.isConnected` 防止筛选切换后向已移除节点续建

  验证：JS 语法通过；线上 `app.js` 已含 `appendCardsChunked` 且无
  `recs.length > 20` 自动收起逻辑（静态文件按请求读取，刷新即生效）。

## 2026-10-08 19:50:27 +0800

- **用户**: XiaoWin
- **系统**: Microsoft Windows 11 专业版 · 10.0.26200 · AMD64 · 本机
- **内容**: 本地数据升级为统一「存储目录」（数据库+缓存整体迁移），路径改为界面点选（8 文件）

  **1. 存储目录（替代上一条的单一数据库路径）**

  - `records.py`：config.json（仓库根，gitignore）改记 `storage_dir`（清掉旧 `db_path` 键）；
    `DATA_DIR`/`DB_PATH`/`get_cache_dir()` 全部跟随存储目录，`config.json` 本身固定在仓库根
    （它只记录「数据放哪」，必须跟着代码走）
  - `set_storage_dir(path, migrate)`：数据库用 SQLite backup 迁移（含 WAL 合并）后删除旧文件、
    `cache/` 整体 `shutil.move`——**项目目录不再残留本地私有数据**；目标已有 metadata.db 拒绝、
    传入文件/空路径拒绝、切回默认目录时配置键自动清理；立即生效无需重启
  - `server.py`：`GET/POST /api/storage-dir`（旧 `/api/db-path` 保留兼容）；
    `handle_img` 改用 `get_cache_dir()` 每次动态取，切换后新缓存写入新位置
  - **新增 `GET /api/fs/list?path=`**：目录浏览接口——空 path 返回盘符列表（C:\ D:\…），
    否则返回子目录 + parent；不存在/无权限返回可读错误

  **2. 路径改为界面点选（不再手写）**

  - `index.html`：工具栏「数据库」按钮改为「存储」；弹窗重做——当前位置展示
    （目录/数据库/缓存三行）+ 输入框 + 「浏览…」+ 目录点选器（fs-picker：
    上级导航、路径面包屑、目录列表点击进入、「选中此目录」回填输入框）
  - `app.js`：`openDbPanel` / `loadFs` / `saveDbPath` + `fsState` 导航状态；
    保存成功自动 `loadRecords()` 从新存储目录刷新
  - `style.css`：`.fs-picker` / `.fs-nav` / `.fs-item` / `.fs-actions` 样式与 `.db-modal` 宽度

  **3. 配套**

  - README：数据文件说明、特性列表、API 表同步（storage-dir / fs/list）
  - `.gitignore` 已含 `config.json`（上一批）

  验证：`set_storage_dir` 单测 7 组全过（迁出后旧库/旧缓存删除、写入只进新库、
  同目录 no-op、已占用目录/文件/空路径拒绝、迁回后配置键清理、测试数据还原）；
  重启 4000 服务后 API 往返：切走 migrated=true → 新位置缓存目录生成并成功缓存图片（200）
  → 切回还原；`fs/list` 盘符/目录/父级/错误路径四种情况正确；页面 200 含新结构；
  JS/Python 语法通过。

## 2026-10-08 19:36:43 +0800

- **用户**: XiaoWin
- **系统**: Microsoft Windows 11 专业版 · 10.0.26200 · AMD64 · 本机
- **内容**: 数据库位置可在 UI 内修改；看板改全宽网格分布（6 文件）

  **1. 数据库位置可配置**

  - `records.py`：新增仓库根 `config.json`（已 gitignore）持久化 `db_path`，
    启动时读取生效；`set_db_path()` 支持——目录或无 .db 后缀自动补 `metadata.db`、
    相对路径按仓库根解析、目标不存在时用 SQLite `backup` API 把现有库完整复制过去
    （含 WAL，原文件保留）、目标为无效 SQLite 时拒绝；`DB_PATH` 模块级切换，
    短连接模式下**立即生效无需重启**
  - `server.py`：新增 `GET /api/db-path`（读当前路径）、
    `POST /api/db-path`（`{path, migrate}` 切换并返回新路径/是否复制/记录数）
  - `index.html`：工具栏加「数据库」按钮 + 设置弹窗（当前路径展示、
    新位置输入、迁移提示）；`app.js`：`openDbPanel` / `saveDbPath`，
    切换成功后自动 `loadRecords()` 从新库刷新

  **2. 看板全宽分布（修「单独一列」）**

  - 根因：`.board` 是 grid，域名分组 `section.domain-group` 作为 grid 单元
    只占一格（约 240px 宽），组内 `.domain-grid` 被挤成单列竖排；
    平铺分支直塞 `.board` 同样受影响
  - `style.css`：`.board` 改纵向 flex（域名组各占整行），
    平铺与组内统一走 `.board-grid` / `.domain-grid` 的
    `auto-fill minmax(240px, 1fr)` 多列网格；去掉 `.card` 的
    `max-width: 320px`，卡片撑满单元格、整行均匀铺满可用宽度
  - `app.js`：平铺分支用 `div.board-grid` 包裹卡片

  验证：`set_db_path` 单测 6 项全过（目录补文件、复制迁移、写入只进新库、
  切回不覆盖原库、空路径/无效文件拒绝）；重启 4000 服务后
  `GET/POST /api/db-path` 往返正常（切走 copied=true → 切回 copied=false）；
  页面 200 含新按钮；线上静态资源为新版；JS/Python 语法通过。

## 2026-10-08 19:17:05 +0800

- **用户**: XiaoWin
- **系统**: Microsoft Windows 11 专业版 · 10.0.26200 · AMD64 · 本机（非开发机 igs-Y）
- **内容**: CHANGE.md 审计与倒序修正（仅本文件）

  审计方式：逐条把条目与 git 提交（7569e5c…adc0d14 共 17 个）及当前代码对照。

  - **一致性通过**：10:04 建立约定后的每个提交均有对应条目，无漏记、无幽灵条目；
    抽查 14 项特性声明（`insert_quick_records`、`fetched`、`_MULTI_TLDS` 前后端、
    `detect_proxy`、`/api/records/quick`、`parseBookmarks`、`thumb-actions`、
    `_is_domestic` 两端、`cache/` 忽略、`xingren-webui` 入口等）在当前代码中全部存在
  - **修正倒序违规**：`17:15:38`（卡片按钮拆分）与 `17:05:22`（数据库重构）两条
    原插在 16:37 与 16:12 之间，违反「倒序（最新在上）」约定；已按时间归位到
    `17:15:44` 与 `17:04:48` 之间，**仅移动位置，内容零改动**
  - **发现（未改动，仅记录）**：
    1. 18:39 条目末尾「服务进程 17:29:59 启动…需重启」对本机已过期——
       本机 4000 端口服务于 19:08:41 以 `xingren-webui` 重启，运行 adc0d14 新代码
    2. 12:27 条目与 TODO 所述 `CLAUDE.md` 在工作区不存在（未入库且无本地副本）；
       维护约定实际只保留在本文件头部与 TODO 头部
    3. 本机 `metadata.db`（创建于 10-07 21:49）实测 **0 条记录**，
       `/api/records` 同为 0；18:39 条目的「32 条真实记录」按三字段（系统=Ubuntu
       igs-Y）指开发机的库，本机与之不同步——如需本机有数据，
       用快照导入或从开发机导出同步

## 2026-10-08 18:39:56 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: 快照导入 + 按需补抓，解决 5000 条 HTML 导入耗时数小时的问题（4 文件）

  **为什么**：原流程每条走 `/api/fetch` 三级降级（最坏 120s/条），
  5000 条 ÷ 并发 5 ≈ 2.8~33 小时，且关浏览器即断。
  但 Raindrop 导出的 HTML 每条已自带 title / TAGS / DATA-COVER，
  只有 `details` 需要抓。

  - `records.py`：新增 `fetched` 列（`ADD COLUMN … NOT NULL DEFAULT 1`，现有行自动得 1，
    **无需重建表**）——0=快照导入未抓，1=已抓取；`insert_quick_records()` 单事务批量入库
    （写记录 + 填 domains + 建标签并关联，**完全不联网**）；`upsert_record` 置 `fetched=1`；
    `_row_to_record` 改收 `tags` 列表参数，`list_records` 用一次 JOIN 建 map —— **消除 N+1**
  - `server.py`：新增 `POST /api/records/quick`
  - `app.js`：
    - `parseBookmarks()` 取代 `parseUrls`，HTML 读出 HREF/标题/封面/标签，TXT 只有 URL
    - HTML → 快照导入分支（单次 POST，完成后 `loadRecords()` 一次刷新）
    - `openDetail` 懒加载：`!fetched` 时先显示「正在抓取详情…」，抓完再渲染（不递归，失败只提示一次）
    - 状态点三分：`.pending` 灰（未抓）/ `.ok` 绿 / `.fail` 红
    - **折叠组延迟建卡片**：`recs.length > 20` 的组先不建 DOM，展开时才补；
      分组判定放宽为 `域名数 > 1 || 条数 > 20`（否则单域名上千条会走平铺建出全部节点）
  - `style.css`：`.dot.pending { background: var(--faint) }`

  **附带修复**（验证 5000 条时暴露的既有 bug）：
  - 前端 `rootDomain` 正则只覆盖 `com|net|org|gov|edu`，**漏了 `co`** → `bar.co.uk` 被截成 `co.uk`
  - 后端 `_root_domain` **完全没有多段后缀处理** → `a.example.com.cn` 得到 `com.cn`
  - 两者还不一致，导致域名分组与域名代理规则对不上
  - 修法：两端共用同一份 `_MULTI_TLDS` 列表（26 个），8 个用例前后端输出完全一致
  - 数据修复：删除错值域名行 `co.uk`，补入 `bar.co.uk`；`google.com(need_proxy=1)`、
    `bilibili.com(别名)` 两处用户设置保留

  **性能实测**：5000 条快照入库 **0.06s**（8.5 万条/秒）；`list_records` 5032 条 0.05s。

  ⚠ **测试数据已清理**：验证时写入的 5000 条假记录、6 个测试标签、7 个示例域名
  已全部删除，数据库恢复为 **32 条真实记录**（`demo1` 标签、两处域名设置均保留）。

  ⚠ **你的服务进程是 17:29:59 启动的，跑的还是旧 `_root_domain`**，
  会继续把 `bar.co.uk` 算成 `co.uk` 插库——**需要重启服务**。

## 2026-10-08 17:33:56 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: statusbar 悬浮于页面底部（仅 style.css）

  - `.statusbar` 由普通 flex 子项改为 `position: fixed; left:0; right:0; bottom:0; z-index:10`，
    脱离文档流盖在内容上，加 `box-shadow: 0 -2px 8px` 与内容分隔
  - 脱离流后占的 39px 需补偿，否则最后一行被遮：
    `.board-wrap` `padding-bottom` 24→63px、`.sidebar` 补 `padding-bottom: 55px`

  **未采用 `position: sticky; bottom:0`**：`body` 是 `min-height:100vh` 的 flex 列、
  `main` 是 `flex:1 + overflow:hidden`，状态栏本就在视口底部且不随滚动移动，
  sticky 在此布局下无任何可见效果；fixed 才真正「悬浮」并把这 39px 还给看板。

  验证：statusbar 规则与两处让位 padding 均已下发（静态文件即时生效，刷新即可）。

## 2026-10-08 17:27:54 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: 代理端口自动探测（7889-7899 区间）——新模块 `xingren/core/proxy.py`

  - `detect_proxy(host="127.0.0.1")`：`ThreadPoolExecutor` **并行**对 7889-7899
    逐端口 `socket.create_connection`（单端口超时 0.25s，总耗时约等于单次超时），
    按端口升序返回第一个可连接的 `http://127.0.0.1:<port>`，全不通返回 None
  - `server.py`：新增 `GET`/`POST /api/proxy-detect`（每次重新探测，不缓存——
    代理可能刚启动）；启动横幅打印 `代理探测（7889-7899）：…`（仅提示，不改配置）
  - `app.js`：`detectProxy({fill, announce})`；页面加载自动探测并**采纳探测结果**
    （探测不到则保持原配置——本地探测找不到不代表没有远程代理）；
    面板全局代理行加「检测」按钮可随时重探
  - `index.html`：`#proxyDetectBtn`；补说明「启动时会自动探测…也可随时点检测」

  验证：**实测探测到本机真实代理 `http://127.0.0.1:7897`（0.02s）**；
  启动横幅正确打印；GET/POST 均返回 `{ok:true, proxy:…}`；
  绑定 7889 后返回 7889（区间内最小可连接端口）；JS 语法通过。

## 2026-10-08 17:21:37 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: 修复代理映射面板 `.proxy-row` 内三个控件的尺寸问题（style.css + index.html）

  - **根因**：`.proxy-row .input { flex: 1 }` 同时命中模式输入框与下拉框，
    只能靠 `.rule-select { … !important }` 硬压；且 `flex: 1` 缺 `min-width: 0`，
    flex 子项默认不可收缩，窄容器下会撑破行；三者高度也不一致
  - `style.css`：`.proxy-row` 改 `align-items: stretch`（三者等高齐平）；
    输入框 `flex: 1 1 0; min-width: 0`（占满剩余且可收缩）；
    下拉改 `.proxy-row .rule-select`（与上条同特异性 0,2,0，靠声明顺序覆盖，
    **去掉 `!important`**），宽 92px；按钮 `flex: 0 0 auto; white-space: nowrap`
  - `index.html`：`ruleNeedSelect` 补 `title` 提示

  验证：CSS 两条同特异性规则声明顺序正确；`!important` 无残留；
  页面与 style.css 均 200，级联后 `.rule-select` 规则生效。

## 2026-10-08 17:15:44 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: 代理映射改为「是否需要代理」布尔判定，不再存代理地址（6 文件，+399 / -43）

  - **语义**：映射只表达要不要走代理，实际地址统一由全局代理一处配置。
    - `proxy_rules.need_proxy` INTEGER：1=用代理，0=强制直连（规则本身即决策，二态）
    - `domains.need_proxy` INTEGER **可空**：NULL=无规则跟随全局，1=用代理，0=强制直连（三态）
    - 旧 `proxy TEXT` 地址列已删除
  - **迁移**（`_migrate_to_bool_proxy`）：`proxy_rules` 空地址→0；
    `domains` 空地址→**NULL 而非 0**——旧语义 `proxy=''` 是「无规则跟随全局」，
    若误迁成 0 会导致全站被强制直连
  - **`_effective_proxy` 三级解析**：URL 模式规则 > 域名规则 > 全局代理默认值；
    规则命中即强制（0 直接返回 None，不再向下兜底）
  - `records.py`：`_UNSET` 哨兵区分「本次不改」与「设为无规则」；
    `get_domain_need_proxy` 返回 `True/False/None`
  - `server.py`：`PATCH /api/domain` 只更新 payload 里出现的字段
  - `app.js` / `index.html`：规则与域名的代理地址输入框改为
    「用代理 / 直连 / 跟随全局」下拉；全局代理地址仅保留在面板顶部一处

  **修复**：本地库已被错误迁移污染（11 个域名 need_proxy=0），
  经重建 `domains` 表修正为 NULL；服务器侧未迁移过，拉取新代码后按正确逻辑执行。

  验证：JS 语法 / Python 编译；迁移后 `proxy` 列已删、`need_proxy` 已生成；
  三级优先级 5 个断言全过（含规则压过域名规则）；测试规则已清理。

## 2026-10-08 17:15:38 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: 卡片操作按钮拆分——详情/重新抓取移到缩略图右上角（2 文件，+34 / -30）

  - `app.js`：底部操作条拆为两组——`thumb-actions`（右上角：详情/重新抓取）
    和 `actions`（底部：重命名/标签/删除）；代码压缩为单行事件绑定
  - `style.css`：新增 `.thumb-actions`（`position: absolute; top:6px; right:6px`），
    半透明白底 + `backdrop-filter: blur(4px)` 毛玻璃效果，悬停时 `opacity: 1`

  验证：JS 语法通过；`thumb-actions` / `topActions` / `actions.append` 引用正确。

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

## 2026-10-08 17:04:48 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: 新增代理映射（URL 模式通配符）+ 独立设置面板；移除工具栏代理输入框（5 文件，+293 / -13）

  - `records.py`：新表 `proxy_rules(id, pattern, proxy)`，`proxy` 空串 = **强制直连**；
    `match_proxy_rule(url)` 用 `fnmatchcase` 匹配——模式含 `/` 时连路径一起匹配，
    否则只匹配主机名；规则按模式长度降序，更具体的先命中；
    `upsert_proxy_rule` / `delete_proxy_rule` / `list_proxy_rules` CRUD
  - `server.py`：`_effective_proxy` 改为三级优先级
    **URL 模式规则 > 域名代理 > 全局代理**；规则命中即为强制值（空串→直连，不再向下兜底）；
    新增 `GET /api/proxy-rules`、`POST /api/proxy-rule`、`DELETE /api/proxy-rule?id=…`
  - `app.js`：新增「代理」面板（`openProxyPanel` / `renderProxyPanel` /
    `saveGlobalProxy` / `addRule`），含三段——全局代理、URL 模式规则（增删）、
    域名代理（只读列表，点域名可跳转编辑）；**新增 `state.globalProxy`，
    移除工具栏 `proxyInput` 输入框**，`createCard` / `fetchRecord` /
    `importFromFile` 改读 `state.globalProxy`
  - `index.html`：toolbar 加「代理」按钮；移除 `#proxyInput`；加 `#proxyMask` 面板
  - `style.css`：删 `.input.proxy`；新增 `.proxy-panel` / `.proxy-section` /
    `.rule-list` / `.rule-row` / `.rule-pattern` / `.rule-proxy` / `.rule-del` 样式

  验证：8 个通配符匹配用例全过（`*.google.com` 不匹配顶级域名、
  `github.com/*` 不匹配 gist.github.com 等）；API 增删查 200；强制直连解析为 None；
  JS 语法通过；`proxyInput` 无残留；服务启动 200。

## 2026-10-08 16:50:20 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: 新增导出功能（TXT / HTML，导出当前可见记录；3 文件，+76 / -9）

  - `app.js`：抽公共函数 `getVisibleRecords()`（搜索 + 域名 + 标签过滤，
    `render()` 与导出共用）；新增 `exportTxt`（一行一个 URL）、
    `exportHtml`（Netscape Bookmark 格式，含 `ADD_DATE` 和 `TAGS` 属性）、
    `downloadFile`（Blob + `URL.createObjectURL` 触发下载）、
    `escHtml`（转义 `& < > "`，防标题里的特殊字符破坏 HTML）、`doExport`
  - `index.html`：toolbar 加 `<select id="exportSelect">`（导出 / TXT / HTML）
  - `style.css`：`.export-select` 样式

  语义：**导出当前可见记录**（侧边栏域名/标签筛选 + 搜索框过滤后的结果），
  选格式后下拉框复位，可重复导出；无可见记录时状态栏提示。

  验证：JS 语法通过；TXT 输出 3 行 URL；HTML 输出含转义
  （`&`→`&amp;`、`<`→`&lt;`）与 TAGS；**往返校验一致**（导出 URL 能被
  现有导入逻辑原样读回）。

  已知限制：导入目前只读 `HREF`，`TAGS` 属性不会写回标签（需后端 `/api/fetch`
  接受 tags 参数才能完整闭环）。

## 2026-10-08 16:37:43 +0800

- **用户**: haijie yin
- **系统**: Ubuntu 20.04.6 LTS (Focal Fossa) · Linux 5.4.0-21-generic x86_64 · igs-Y
- **内容**: `/api/img` 加服务端本地磁盘缓存（3 文件）

  - `server.py`：`handle_img` 先查 `cache/img/<sha256(src)[:32]>.bin` +
    `.ct`（内容类型侧车文件），命中直接返回不走网络；未命中才经代理拉取，
    **仅 2xx 落盘**（4xx/5xx 不缓存，避免把错误页存住）；
    抽出 `_send_img` 统一响应头（`Cache-Control: public, max-age=86400`）
  - `records.py`：暴露公共常量 `DATA_DIR`（原 `_DATA_DIR`），供服务端定位缓存目录
  - `.gitignore`：加 `cache/`，整目录删除即可强制刷新

  验证：首次请求 1.22s（走网络）→ 二次 0.02s（命中本地，快 61 倍）；
  两次响应字节一致；`git check-ignore` 覆盖 `cache/img/test.bin`。

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
