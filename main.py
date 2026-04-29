"""
wx-data-toolkit 一键启动

python main.py          # 获取访问凭据 + 启动 Web UI
python main.py decrypt  # 获取访问凭据 + 导出全部数据库
"""
import json
import os
import platform
import sys
import subprocess

import functools
print = functools.partial(print, flush=True)

from wxdec.key_utils import strip_key_metadata


def check_wechat_running():
    """检查微信是否在运行，返回 True/False"""
    if platform.system().lower() == "darwin":
        return subprocess.run(["pgrep", "-x", "WeChat"], capture_output=True).returncode == 0
    from wxdec.find_all_keys import get_pids
    try:
        get_pids()
        return True
    except RuntimeError:
        return False


def ensure_keys(keys_file, db_dir):
    """确保凭据文件存在且匹配当前 db_dir，否则重新获取"""
    if os.path.exists(keys_file):
        try:
            with open(keys_file, encoding="utf-8") as f:
                keys = json.load(f)
        except (json.JSONDecodeError, ValueError):
            keys = {}
        # 检查凭据是否匹配当前 db_dir（防止切换账号后误复用旧 key）
        saved_dir = keys.pop("_db_dir", None)
        if saved_dir and os.path.normcase(os.path.normpath(saved_dir)) != os.path.normcase(os.path.normpath(db_dir)):
            print(f"[!] 检测到 db_dir 已切换（可能切换了微信账号），需要重新生成凭据")
            print(f"    上次: {saved_dir}")
            print(f"    本次: {db_dir}")
            keys = {}
        keys = strip_key_metadata(keys)
        if keys:
            print(f"[+] 已加载 {len(keys)} 条访问凭据")
            return

    print("[*] 凭据文件不存在，正在初始化访问凭据...")
    print()
    from wxdec.find_all_keys import main as extract_keys
    try:
        extract_keys()
    except RuntimeError as e:
        print(f"\n[!] 凭据获取失败: {e}")
        sys.exit(1)
    print()

    # 获取后再次检查
    if not os.path.exists(keys_file):
        print("[!] 凭据获取失败")
        sys.exit(1)
    try:
        with open(keys_file, encoding="utf-8") as f:
            keys = json.load(f)
    except (json.JSONDecodeError, ValueError):
        keys = {}
    if not strip_key_metadata(keys):
        print("[!] 未能获取到任何 key")
        print("    可能原因：选择了错误的微信数据目录，或微信需要重启")
        print("    请检查 config.json 中的 db_dir 是否与当前登录的微信账号匹配")
        sys.exit(1)


def _run_decode_images(cfg, argv):
    """`decode-images` 子命令:批量把 .dat 图片解码到标准格式目录树。

    与 decrypt 不同,decode-images **不需要** 微信进程在运行,也不需要数据库 key
    (只读已存在的 .dat 文件;V2 文件用 config.json 里的 image_aes_key)。
    """
    import argparse
    from wxdec.decode_image import decode_all_dats

    parser = argparse.ArgumentParser(
        prog="main.py decode-images",
        description=(
            "批量解码微信本地 .dat 图片到标准格式目录树。"
            "区别于 decode_image.py 单文件 CLI,本子命令扫描 attach_dir 下"
            "全部 .dat,镜像目录结构产出标准格式图片(jpg / png / gif / webp / hevc)。"
        ),
    )
    default_base = cfg.get("wechat_base_dir") or os.path.dirname(cfg["db_dir"])
    default_attach = os.path.join(default_base, "msg", "attach")
    default_out = cfg.get("decoded_image_dir", "decoded_images")
    parser.add_argument(
        "--attach-dir", default=None,
        help=f"微信 msg/attach 根目录,覆盖默认推断(默认: {default_attach})",
    )
    parser.add_argument(
        "--decoded-dir", default=None,
        help=f"标准格式图片输出根目录,覆盖 config.json 的 decoded_image_dir(默认: {default_out})",
    )
    parser.add_argument(
        "--aes-key", default=None,
        help="V2 AES key(16 字节 ASCII 字符串),覆盖 config.json 的 image_aes_key",
    )
    parser.add_argument(
        "--xor-key", default=None,
        help="V2 XOR key(可十进制或 0x 十六进制),覆盖 config.json 的 image_xor_key(默认: 0x88)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="忽略已存在目标重新导出(默认按 basename 跳过)",
    )
    args = parser.parse_args(argv)

    attach_dir = args.attach_dir or default_attach
    out_dir = args.decoded_dir or default_out
    aes_key = args.aes_key if args.aes_key is not None else cfg.get("image_aes_key")
    xor_key_raw = args.xor_key if args.xor_key is not None else cfg.get("image_xor_key", 0x88)
    if isinstance(xor_key_raw, str):
        xor_key = int(xor_key_raw, 0)
    else:
        xor_key = xor_key_raw

    if not os.path.isdir(attach_dir):
        print(f"[ERROR] attach 目录不存在: {attach_dir}", file=sys.stderr)
        sys.exit(1)

    if aes_key is None:
        print(
            "[NOTE] 未配置 image_aes_key,V2 格式图片将被跳过(计入 skipped_no_key);"
            "其他格式图片不受影响。详见 README 的图片导出章节。",
            file=sys.stderr,
        )

    print(f"  attach_dir = {attach_dir}")
    print(f"  out_dir    = {out_dir}")
    print(f"  aes_key    = {'已配置' if aes_key else '未配置'}")
    print(f"  xor_key    = 0x{xor_key:02x}")
    print(f"  force      = {args.force}")
    print()

    stats = decode_all_dats(
        attach_dir=attach_dir,
        out_dir=out_dir,
        aes_key=aes_key,
        xor_key=xor_key,
        force=args.force,
    )

    print()
    print("=" * 60)
    print(f"扫描 {stats['total']} 个 .dat 文件")
    print(f"  解码: {stats['decoded']}  跳过(已存在): {stats['skipped']}  "
          f"无 key 跳过: {stats['skipped_no_key']}  失败: {stats['failed']}")
    if stats["formats"]:
        fmt_summary = ", ".join(f"{ext}={n}" for ext, n in sorted(stats["formats"].items()))
        print(f"  按格式: {fmt_summary}")
    print(f"输出在: {out_dir}")

    if stats["failed"] > 0:
        sys.exit(2)


def main():
    print("=" * 60)
    print("  wx-data-toolkit")
    print("=" * 60)
    print()

    # 1. 加载配置（自动检测 db_dir）
    from wxdec.config import load_config
    cfg = load_config()

    # 早路由:decode-images 不需要微信进程在运行,也不需要数据库 key
    if len(sys.argv) > 1 and sys.argv[1] == "decode-images":
        print("[*] 批量导出图片...")
        print()
        _run_decode_images(cfg, sys.argv[2:])
        return

    # 2. 检查微信进程
    if not check_wechat_running():
        print(f"[!] 未检测到微信进程 ({cfg.get('wechat_process', 'WeChat')})")
        print("    请启动微信并登录后重新运行本命令")
        sys.exit(1)
    print("[+] 微信进程运行中")

    # 3. 获取访问凭据
    ensure_keys(cfg["keys_file"], cfg["db_dir"])

    # 4. 根据子命令执行
    cmd = sys.argv[1] if len(sys.argv) > 1 else "web"

    if cmd == "decrypt":
        print("[*] 开始导出全部数据库...")
        print()
        from wxdec.decrypt_db import main as decrypt_all
        decrypt_all(sys.argv[2:])
    elif cmd == "web":
        print("[*] 启动 Web UI...")
        print()
        from wxdec.cli.monitor_web import main as start_web
        start_web()
    else:
        print(f"[!] 未知命令: {cmd}")
        print()
        print("用法:")
        print("  python main.py                            启动实时消息监听 (Web UI)")
        print("  python main.py decrypt                    导出全部数据库到 decrypted/(不含 WAL)")
        print("  python main.py decrypt --with-wal         导出 + 合并 WAL,获得当天最新消息")
        print("  python main.py decode-images              批量导出 .dat 图片到 decoded_image_dir/")
        print("  python main.py decrypt --help             查看 decrypt 全部选项")
        print("  python main.py decode-images --help       查看 decode-images 全部选项")
        sys.exit(1)


if __name__ == "__main__":
    main()
