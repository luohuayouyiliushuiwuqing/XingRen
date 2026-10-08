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
