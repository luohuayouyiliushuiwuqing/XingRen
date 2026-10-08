# CHANGE.md

变更记录，倒序（最新在上）。每条含三字段：**修改时间 / 用户名 / 系统名**。

维护约定：每完成一批修改即追加一条，按"修改"计（未 commit 的改动同样记录）。
字段取值：时间 `date "+%Y-%m-%d %H:%M:%S %z"`；用户 `git config user.name`；
系统 `/etc/os-release` 的 `NAME`+`VERSION` · `uname -srm` · `hostname`。

---

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
