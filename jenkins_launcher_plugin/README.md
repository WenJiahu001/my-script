# Flow Launcher - Jenkins 多环境与视图分组一键构建插件

为 [Flow Launcher](https://www.flowlauncher.com/) 打造的轻量高效 Jenkins 一键构建插件。

---

## 🌟 核心特性

- ⚡ **即输即搜与高频智能排序**：
  - 自动记忆每个项目的触发频次与最近构建时间，最常发布的项目**自动加权置顶排在最前**（显示 `⭐[常用 X次]`）。
  - 支持在 `config.json` 中配置 `"pinned_jobs": ["qm-spds"]` 实现永久置顶（显示 `📌 [置顶]`）。
- 📋 **右键一键查看控制台日志 (Console Output)**：
  - 在任何流水线项目上点击鼠标右键，第一项即可直接打开最近一次构建的控制台实时日志（`.../lastBuild/console`），排查问题快人一步。
- 🏢 **多环境与视图分组统一管理**：
  - 支持按环境配置关心的 Jenkins Views（如 `"views": ["全民后端", "新版-全民-后端"]`）。
  - 支持 `jk views` 查看所有视图列表。
  - 支持 `jk prod`（或 `jk p`）检索生产环境，带防误触弹窗保护。
- 🚀 **一键触发构建**：按下 `Enter` 立即触发 Jenkins 默认参数构建，自适应普通项目与参数化构建项目。
- ⚙️ **醒目快捷操作**：呼出 `jk ` 在列表顶部常驻 `[config]`、`[refresh]`、`[views]` 操作项。

---

## 🛠️ 配置说明 (`config.json`)

项目目录的 `config.json` 是主配置文件，也是唯一需要编辑的文件。每次插件加载或保存配置时，都会单向同步到 `%APPDATA%\FlowLauncher\JenkinsLauncher\config.json` 作为运行镜像；用户目录中的镜像不会反向覆盖项目配置。

配置文件不再保存真实凭据。请先在当前 Windows 用户环境变量中设置：

```powershell
[Environment]::SetEnvironmentVariable('JENKINS_DEV_URL', 'https://jenkins.example.com', 'User')
[Environment]::SetEnvironmentVariable('JENKINS_DEV_USERNAME', 'your_username', 'User')
[Environment]::SetEnvironmentVariable('JENKINS_DEV_API_TOKEN', 'your_api_token', 'User')
```

重启 Flow Launcher 后生效。环境名称完全由 `environments` 的键决定：例如 `beta` 使用 `JENKINS_BETA_URL`、`JENKINS_BETA_USERNAME`、`JENKINS_BETA_API_TOKEN`。`config.json` 仅保留环境变量引用、Views 和置顶配置；输入 `jk beta` 即可检索 beta 环境，`jk all` 会搜索全部已配置环境。

在 Flow Launcher 中输入 `jk config` 可直接打开该用户配置文件。配置格式如下：

```json
{
  "default_environment": "dev",
  "environments": {
    "dev": {
      "name": "DEV 开发环境",
      "jenkins_url": "${JENKINS_DEV_URL}",
      "username": "${JENKINS_DEV_USERNAME}",
      "api_token": "${JENKINS_DEV_API_TOKEN}",
      "views": [
        "全民后端",
        "新版-全民-后端"
      ],
      "pinned_jobs": [
        "qm-spds"
      ],
      "cache_duration_seconds": 600,
      "verify_ssl": true,
      "timeout": 5,
      "require_confirm": false
    },
    "prod": {
      "name": "PROD 生产环境",
      "jenkins_url": "https://jenkins.prod.company.com",
      "username": "your_prod_username",
      "api_token": "your_prod_api_token",
      "views": [],
      "pinned_jobs": [],
      "cache_duration_seconds": 300,
      "verify_ssl": true,
      "timeout": 5,
      "require_confirm": true
    }
  }
}
```

---

## 📖 常用操作与快捷键

| 操作 | 方式 |
| :--- | :--- |
| **一键触发构建** | 选中流水线项目，直接按下 **`Enter` 回车** |
| **查看实时构建日志** | 鼠标右键目标项目 -> 点击 **【📋 查看控制台构建日志 (Console Output)】** |
| **打开项目主页** | 鼠标右键目标项目 -> 点击 **【🌐 在浏览器打开流水线首页】** |
| **刷新列表缓存** | 呼出 `jk ` 点击顶部 **`[refresh]`** 项（或输入 `refresh`） |
| **修改配置/置顶** | 呼出 `jk ` 点击顶部 **`[config]`** 项（或输入 `config`），编辑项目目录的 `config.json` |
| **切换任意环境** | 输入 `jk <环境键> <关键词>`，如 `jk beta api` |
