# -*- coding: utf-8 -*-
"""Windows 通知中心 Toast 封装，仅使用系统自带 PowerShell 与 WinRT。"""

import base64
import json
import os
import subprocess


def show_notification(title: str, message: str) -> bool:
    """异步发送通知中心消息，调用方不会被通知展示过程阻塞。"""
    if os.name != "nt":
        return False
    payload = base64.b64encode(json.dumps({"title": str(title), "message": str(message)}, ensure_ascii=False).encode("utf-8")).decode("ascii")
    # 通过 Base64 传值并在 PowerShell 内转义 XML，避免项目名中的特殊字符导致通知失效。
    log_path = os.path.join(os.environ.get("APPDATA", ""), "FlowLauncher", "JenkinsLauncher", "plugin_debug.log")
    script = f"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime] | Out-Null
try {{
    $payload = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{payload}')) | ConvertFrom-Json
    $title = [Security.SecurityElement]::Escape([string]$payload.title)
    $message = [Security.SecurityElement]::Escape([string]$payload.message)
    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$title</text><text>$message</text></binding></visual></toast>")
    $toast = New-Object Windows.UI.Notifications.ToastNotification $xml
    # 读取当前安装的 Flow Launcher 开始菜单注册身份；它已具备通知中心投递资格。
    $flowLauncher = Get-StartApps | Where-Object {{ $_.Name -eq 'Flow Launcher' }} | Select-Object -First 1
    if (-not $flowLauncher -or -not $flowLauncher.AppID) {{ throw '未找到 Flow Launcher 的通知身份' }}
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($flowLauncher.AppID).Show($toast)
}} catch {{
    $dir = Split-Path -Parent '{log_path.replace("'", "''")}'
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    [IO.File]::AppendAllText('{log_path.replace("'", "''")}', "[notify] Toast 发送失败: $($_.Exception.Message)`r`n", [Text.Encoding]::UTF8)
}}
"""
    encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Sta", "-WindowStyle", "Hidden", "-EncodedCommand", encoded_script],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except OSError:
        return False
