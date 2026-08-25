# -*- coding: utf-8 -*-
"""Flow Launcher 插件一键安装脚本"""

import os
import shutil
import subprocess
import sys

# 兼容 Windows 控制台各种编码环境
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def safe_print(msg: str):
    """安全打印，避免 Windows GBK 控制台编码崩溃"""
    try:
        print(msg)
    except Exception:
        try:
            print(msg.encode(sys.stdout.encoding or "gbk", errors="replace").decode(sys.stdout.encoding or "gbk"))
        except Exception:
            pass


def install():
    safe_print("=" * 60)
    safe_print(" 正在安装 Jenkins 一键构建插件到 Flow Launcher...")
    safe_print("=" * 60)

    # 1. 确定目录路径
    appdata = os.environ.get("APPDATA")
    if not appdata:
        safe_print("[错误] 无法获取 APPDATA 环境变量")
        return

    plugins_dir = os.path.join(appdata, "FlowLauncher", "Plugins")
    target_dir = os.path.join(plugins_dir, "JenkinsLauncher")
    source_dir = os.path.dirname(os.path.abspath(__file__))

    safe_print(f"源文件目录: {source_dir}")
    safe_print(f"目标安装目录: {target_dir}")

    # 2. 创建 Plugins 根目录（若不存在）
    if not os.path.exists(plugins_dir):
        safe_print(f"[提示] 创建 Flow Launcher 插件目录: {plugins_dir}")
        os.makedirs(plugins_dir, exist_ok=True)

    # 3. 清理已存在的旧目录或链接
    if os.path.exists(target_dir) or os.path.islink(target_dir):
        safe_print("[提示] 检测到已存在同名插件，正在清理旧版本...")
        try:
            if os.path.islink(target_dir):
                os.unlink(target_dir)
            elif os.path.isdir(target_dir):
                shutil.rmtree(target_dir)
            else:
                os.remove(target_dir)
        except Exception:
            subprocess.run(f'rmdir "{target_dir}" /s /q', shell=True, capture_output=True)

    # 4. 优先尝试建立软链接 (mklink /D)
    link_success = False
    try:
        cmd = f'cmd /c mklink /D "{target_dir}" "{source_dir}"'
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if os.path.exists(target_dir) or os.path.islink(target_dir):
            link_success = True
            safe_print("\n[成功] 已通过【软链接】方式安装完成！")
            safe_print("       (后续在源码目录修改代码将实时生效，无需重新安装)")
    except Exception:
        link_success = False

    # 5. 软链接若未成功，则回退为完整复制
    if not link_success:
        safe_print("\n[提示] 正在使用【直接复制】方式安装...")
        try:
            shutil.copytree(source_dir, target_dir)
            # 复制安装时，插件运行目录不同；保存主项目路径以保持配置单向同步。
            pointer_file = os.path.join(target_dir, "config_source_path.txt")
            with open(pointer_file, "w", encoding="utf-8") as f:
                f.write(source_dir)
            safe_print("[成功] 插件文件已完整复制到 Flow Launcher 插件目录！")
        except Exception as e:
            safe_print(f"[失败] 复制插件文件失败: {e}")
            return

    # 6. 安装完成后的指引
    safe_print("\n" + "=" * 60)
    safe_print(" [完成] 插件安装就绪！请按以下步骤启用：")
    safe_print("=" * 60)
    safe_print(" 1. 呼出 Flow Launcher (快捷键 Alt + Space)")
    safe_print(" 2. 输入: flow reload plugins (或重启 Flow Launcher)")
    safe_print(" 3. 输入: jk config 回车，配置你的 Jenkins 地址与 API Token")
    safe_print(" 4. 输入: jk 即可开始使用！")
    safe_print("=" * 60 + "\n")


if __name__ == "__main__":
    install()
    if sys.stdin and sys.stdin.isatty():
        try:
            input("按回车键退出...")
        except Exception:
            pass
