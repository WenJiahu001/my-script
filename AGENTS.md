# AGENTS.md - python-quickly 仓库 AI 编程协作规则

本项目是一个面向 Windows 效率工具与自动化脚本的 Python 多模块工具仓库，当前核心项目为 **Flow Launcher Jenkins 一键构建插件 (`jenkins_launcher_plugin`)**。

---

## 1. 核心语言与交互原则

* **全中文交互**：思考过程 (CoT)、需求分析、代码注释、提示信息与用户反馈必须全部使用简体中文。
* **深思熟虑 (CoT)**：修改代码前必须先推演调用链路与边界情况。
* **架构师视角**：若存在更优方案，主动提供方案对比（性能、维护性、复杂度），由用户决策。
* **严格依赖控制**：**严禁擅自引入任何第三方 `pip` 依赖**（如 `requests`, `pillow` 等），项目必须保持纯 Python 标准库与 Windows 原生系统调用实现。
* **数据库变更规则**：若涉及数据库字段添加，必须向用户提供标准 SQL 语句。

---

## 2. 仓库架构与模块定位

```
python-quickly/
├── jenkins_launcher_plugin/           # Flow Launcher Jenkins 一键构建插件
│   ├── plugin.json                    # Flow Launcher 插件元数据 (触发词: jk)
│   ├── main.py                       # 控制与交互层 (query 搜索、前缀匹配、右键菜单、构建分发)
│   ├── jenkins_client.py             # 数据与 API 层 (多环境管理、视图拉取、自适应参数化构建)
│   ├── config.py                     # 配置与缓存管理 (自动补全、多环境独立缓存)
│   ├── config.json.template          # 配置文件模板
│   ├── history.py                    # 历史构建统计与高频智能加权排序
│   ├── flowlauncher.py               # JSON-RPC 通信协议与调试日志封装
│   ├── install.py                    # 插件自动化安装脚本 (建立软链接)
│   └── Images/                       # 标准 GDI+ PNG 图标 (setting.png, pipeline.png 等)
├── tests/                             # 自动化单元测试目录
│   └── test_jenkins_launcher.py
└── AGENTS.md                          # 本协作规则文件
```

### 需求定位与修改映射

| 需求类型 | 优先查看与修改文件 |
| :--- | :--- |
| **搜索匹配 / 界面菜单 / 快捷指令** | `jenkins_launcher_plugin/main.py` |
| **Jenkins API 通信 / 鉴权 / 视图 / 构建触发** | `jenkins_launcher_plugin/jenkins_client.py` |
| **多环境配置字段 / 本地缓存策略 (TTL)** | `jenkins_launcher_plugin/config.py` 与 `config.json.template` |
| **置顶算法 / 常用历史频次加权** | `jenkins_launcher_plugin/history.py` 与 `main.py` |
| **Flow Launcher 协议底层 / 日志记录** | `jenkins_launcher_plugin/flowlauncher.py` |
| **安装部署 / 软链接管理** | `jenkins_launcher_plugin/install.py` |
| **扩展新工具 / 新插件** | 根目录下新建独立子目录 `xxx_tool/` 或 `xxx_plugin/` |

---

## 3. 核心设计与编码规范

### 3.1 Flow Launcher 交互规范
* **精准前缀匹配**：快捷设置指令（`config`, `refresh`, `views`, `prod`）必须严格使用 `action_key.startswith(keyword_lower)` 进行前缀匹配，**禁止随意添加 `cfg`, `sz` 等别名或全局子串匹配**。
* **首屏常驻项**：空输入（`not query_text`）时，列表最上方必须常驻展示快捷操作项：
  * `[config]` 打开配置文件
  * `[refresh]` 刷新缓存列表
  * `[views]` 查看视图列表
  * `[prod]` 切换生产环境
* **图标规范**：
  * 快捷操作项统一使用 `setting.png`（设置齿轮图标）。
  * 流水线项目统一使用 `pipeline.png`（流水线图标）。
  * 必须使用绝对路径传递 `IcoPath`，防止软链接环境下图标加载失败。
* **右键上下文菜单 (Context Menu)**：
  * 对流水线项目右键，第 1 项必须为 `📋 查看控制台构建日志 (Console Output)`，直接跳转 `.../lastBuild/console`。
  * 唤起浏览器必须使用 Windows 原生 `os.startfile(url)`，确保在子进程中 100% 成功唤起默认浏览器。

### 3.2 Jenkins API 交互规范
* **自适应构建触发**：触发构建时，必须优先尝试 `/buildWithParameters`（带参项目默认参数构建），失败时自动降级尝试 `/build`（普通项目）。
* **CSRF Crumb 防护**：POST 请求前必须自动获取并携带 `Jenkins-Crumb`；若失败需支持刷新 Crumb 重试。
* **视图分组支持**：当环境配置了 `views: [...]` 时，需通过 `/view/{quoted_view_name}/api/json` 精准拉取，并为 Job 附带 `view_name` 标签。

### 3.3 生产环境 (PROD) 安全红线
* PROD 环境项目必须带有醒目的 `🚨 [PROD 生产]` 前缀。
* PROD 环境支持 `require_confirm: true`，构建前必须使用 `ctypes.windll.user32.MessageBoxW` 弹出原生二次确认对话框，用户点击取消时严禁触发。

---

## 4. 常用命令与验证方式

```powershell
# 1. 运行全部单元测试
python -m unittest discover -s tests -p "test_*.py" -v

# 2. 本地模拟 Flow Launcher 查询
python jenkins_launcher_plugin/main.py "query_string"

# 3. 重新安装/关联插件到 Flow Launcher
python jenkins_launcher_plugin/install.py
```

---

## 5. 禁止事项清单

1. ❌ **禁止引入任何外部 pip 库**（纯标准库实现）。
2. ❌ **禁止在源码中硬编码 Token、密码等敏感信息**（统一由 `config.json` 加载，模板提供 `config.json.template`）。
3. ❌ **禁止使用破坏性的非标准 PNG**（必须使用 Windows GDI+ 标准生成的 32bpp PNG）。
4. ❌ **禁止修改代码后不跑测试**（必须运行单元测试验证通过）。

---

## 6. 任务完成前检查清单 (Checklist)

* [ ] 是否遵循纯标准库，未引入外部 pip 依赖？
* [ ] 修改是否覆盖了多环境（DEV / PROD）与视图（Views）的兼容性？
* [ ] 快捷操作是否保持严格精准前缀匹配？
* [ ] 右键菜单与浏览器唤起是否使用 `os.startfile` 稳健调用？
* [ ] 是否在 `tests/` 中更新并运行了所有单元测试（100% 通过）？
* [ ] 代码与异常提示是否全中文友好？
