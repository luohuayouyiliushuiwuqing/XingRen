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
