# TODO

未完成事项清单，按优先级排列。完成一项即勾选移入「已完成」，并按约定在
[CHANGE.md](CHANGE.md) 追加记录（时间 / 用户名 / 系统名三字段）。

## 待办

- [ ] **1. README 补部署说明：域名 + 反代 + 安全组**
      以后域名下来照着配就行。要点：
      1. 域名 A 记录指向云主机公网 IP
      2. Caddy / nginx 挂在前面终结 HTTPS（Caddy 自动签发、自动续期）
      3. 看板改绑 `xingren-webui --host 127.0.0.1`，只让反代面对公网
      4. 安全组只放行 80 / 443，关掉 4000
      ```
      浏览器 --https--> Caddy:443 --http--> 127.0.0.1:4000（本机）
      ```
      前提：**Let's Encrypt 不给裸 IP 签证书**，所以域名是上 HTTPS 的必要条件；
      在此之前只能用明文 `http://<公网IP>:4000` 访问。

- [ ] **2. 看板加简单 token 鉴权**
      当前状态是「明文 HTTP + 无鉴权 + 全网开放」，`DELETE /api/record` 任何人可删库，
      `POST /api/fetch` / `GET /api/img` 会让服务器发起任意 URL 请求（SSRF）。
      最小实现：启动参数或环境变量给一个 token，请求校验 `X-Token` 头或 `?token=`，
      不匹配返回 401；静态页加载时由 JS 注入。几行代码，能挡住无脑扫描。

- [ ] **3. 选择器试验台支持 `--host` / `--port`**
      `xingren/web/demo/server.py` 仍硬编码 `127.0.0.1:8765`，与看板的参数风格不一致。

- [ ] **4. 服务器侧遗留项（待确认是否已处理）**
      - `~/XingRen/setup.cfg` 中的 `[easy_install] index_url`——该文件在仓库历史里
        从不存在，应为服务器上自行添加，会持续刷 deprecation 警告
      - 系统 `packaging` 版本偏旧（需 `>=24.2`），曾导致 editable 安装报
        `TypeError: canonicalize_version() got an unexpected keyword argument 'strip_trailing_zero'`

- [ ] **5. 抓取代理默认值在服务器上不可用**
      前端默认 `http://127.0.0.1:7892` 指向**运行服务的机器**本机，云主机上通常没跑代理。
      现状：代码会捕获异常并回退直连重试整条降级链，不会报错，只是抓不到需要代理的站。
      可选改进：把代理地址做成可持久化配置（存库或配置文件），免得每次开页面重填。

- [ ] **8. 浏览器插件（Chrome/Edge MV3）：一键把当前页 / 标签页收进看板**
      目的：省掉「复制 URL → 切窗口 → 粘贴」，把浏览器已经知道的元数据直接送进看板。
      v1 只做两件事：**① 收藏当前页 ② 批量收藏标签页**；右键菜单、快捷键、自动收集、
      截图都往后放，鉴权就是上面第 2 项（插件上线时一并做）。

      **零服务端改动**：`POST /api/records/quick` 本就是快照入库接口——单事务、返回
      `inserted/skipped`、重复 URL 自动 skip、`tags` 不存在会建。插件递过去的
      title / favicon / og:image 和「导入 HTML 书签」完全同构 → `fetched=0`、
      详情字段打开时按需补抓，全走既有链路；批量记录缺封面也能自愈（点「详情 /
      重新抓取」会真实抓取，按合并规则补上 thumbnail / favicon / details）。

      **目录**：仓库根 `extension/`。`packages.find` 只 include `xingren*`、`.gitignore`
      也没这条 → 既不进 wheel 也不被忽略，正常提交，与 Python 包零耦合。

      **关键技术点（都是会踩的坑）**：
      - manifest：`manifest_version: 3`；`permissions: ["activeTab","scripting","tabs","storage"]`；
        `host_permissions: ["http://*/*","https://*/*"]`（看板地址可配：本机 / 内网 IP / 远程）；
        `action.default_popup` 与 `background.service_worker` 可以共存
      - **CORS 只能靠 `host_permissions`**：看板一个 `Access-Control-*` 都不发，也没有
        `do_OPTIONS`（预检直接 501）。装了 host 权限后 Chrome **整个跳过 CORS**，
        `Content-Type: application/json` 正常用即可 —— 改用 `text/plain` 是**没用的**
        （它只免预检，免不了「响应不可读」，而响应可读才是这里的真问题）
      - **缩略图 / favicon 必须是绝对 http(s) URL**：前端 `imgSrc()` 无条件包成
        `/api/img?src=`，服务端对非 http 的 `src` 一律 400，`<img onerror>` 再静默移除
        → **`data:` 图根本显示不出来**。注入函数里用 `new URL(v, location.href)` 解析成
        绝对地址，再按 `/^https?:\/\//` 过滤、非 http(s) 置空；`tab.favIconUrl` 同样过滤
        （它可能是 `undefined` / `data:` / `chrome-extension://`）
      - **读 og:image 不需要常驻内容脚本**：`chrome.scripting.executeScript({target, func})`
        在点击那一刻注入自包含函数（不能引用外部闭包，函数体会被序列化）；
        `host_permissions` 已让注入确定可用，`activeTab` 只是兜底。返回值是数组，
        取 `results[0]?.result`，拿不到就当提取失败
      - **受限页面分两类**：`tab.url` 不匹配 `^https?://`（`chrome://`、`file://`、
        商店页…）→ **禁用按钮**并提示；http(s) 但 `executeScript` 抛错（PDF 阅读器、
        应用商店）→ **降级成只有 title + favicon** 仍可收藏。批量只筛 `^https?://`，
        且**不注入脚本**（那要给每个标签页注入一次），只取 `tab.title` + 过滤后的 favIconUrl
      - **批量单次 POST、不分片**：300 条约 60~150KB，服务端一个事务 <10ms；
        但**空列表不要发**（服务端会 400「缺少 items」），前端先拦
      - 设置存 **`chrome.storage.local`**（别用 sync —— 不该把 `127.0.0.1` 同步到别的设备），
        默认 `http://127.0.0.1:4000`；**默认值在 popup 读取路径也要兜底**（存储可能被清空
        而没重装）；保存时 trim、去尾部 `/`、按 `^https?://` 校验
      - **popup.html 不能有内联 `<script>`**：MV3 默认 CSP 是 `script-src 'self'`，
        内联脚本被静默拦掉 → 表现为「装得上、点了没反应」。只能外链 `popup.js`
      - **图标要么全套要么全不写**：manifest 里引用了不存在的图标文件会直接加载失败；
        先不写 `icons`/`default_icon`，用 Chrome 默认拼图
      - 错误要分三种提示：`fetch` 网络失败（看板没开）/ `data.ok === false`（400、500）/
        成功回显 `新增 X，跳过 Y`

      **文件**：`extension/manifest.json`、`popup.html|css|js`、`background.js`
      （只做 `onInstalled` 写默认设置；顺带让 Playwright 能用 `context.serviceWorkers()`
      拿到扩展 ID 做自动化）、`extension/README.md`（`chrome://extensions` → 开发者模式 →
      加载已解压的扩展程序）。

      **验收**：① load-unpacked 无报错；② `node --check popup.js`；
      ③ 按插件的确切 payload 打一次 `/api/records/quick`，断言 `inserted/skipped`；
      ④ Playwright `--load-extension` 起浏览器驱动 popup，点收藏后看板多一条；
      ⑤ 手动矩阵：普通 http 页（有 og:image）/ PDF 页（降级）/ `chrome://`（禁用）/
      关掉看板（连接失败提示）/ 重复收藏（`skipped=1`）/ 批量含重复（新增 N 跳过 M）。
      另外 README 目录树与 CHANGE.md 各记一笔。

## 已完成

- [x] **提交 `0.0.0.0:4000` 那批改动**
       已由 `62e8a2e`（haijie yin）完成，含 `server.py` 的 `--host`/`--port`/`flush`、
       README 用法与安全说明、CHANGE.md 11:00:16 那条记录。
       （`CLAUDE.md` 未入库——按约定它不参与提交，其同步内容已在工作区。）

- [x] **7. 本机 patchright 内核版本对不上，第 2/3 级抓取器起不来**
       已完成（2026-10-09）。根因不是「忘了装」：`patchright 1.63.0` 要 chromium-1243，
       而 `patchright install chromium` 在 Ubuntu 20.04 上**直接拒绝**（
       `Patchright does not support chromium on ubuntu20.04-x64`），装不上。
       反倒是本机已有的 `chromium-1234` 正是 **playwright 1.62.0** 的内核
       （`requirements.txt` 里就钉着 `playwright==1.62.0`）。
       修：把 `patchright` 降到 **1.62.3**（与 playwright 1.62 同内核版本），
       `patchright install chromium` 即为 no-op、无需下载。
       验收：`DynamicFetcher` / `StealthyFetcher` 实测均 `status=200`，
       启动 PersistentContext 正常、`example.com` 标题取到。
       **服务需重启**——patchright 是在第一次调用抓取时才 import 的，旧进程里
       已经装了 1.63 的模块。

- [x] **6. 域名下再分组（用户自定义子分组）**
       已完成。数据库加 `group_name` 列（迁移兼容已有记录）；侧边栏改为
       域名 → 子分组两级树；卡片操作条加「分组」按钮；`upsert_record`
       保留分组名不被覆盖。7 文件，+294 / -46。
