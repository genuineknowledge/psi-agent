"""把 Windows 侧的 haitun.ico 生成 macOS 侧 1024px PNG 图标源, 两平台共用同一份视觉。

为什么需要这个 PNG: sips 的文档格式列表 (jpeg/tiff/png/gif/jp2/pict/bmp/qtif) 不含
.ico, 打包机上不能可靠地把 ico 直接转成 png/icns。早期实现让 sips 读 ico, 失败后把
.ico 改名 .icns 兜底 —— 那不是合法 icns 容器 (魔数 00 00 01 00 vs 69 63 6e 73),
macOS 无法渲染, 应用图标静默退化成通用图标且构建照常绿。所以打包脚本的图标源是
本脚本生成的 1024px PNG: 真正的 PNG 一定读得动, 任一步失败都会让构建红。

用法:
    python scripts/gen_haitun_icon_png.py            # 生成
    python scripts/gen_haitun_icon_png.py --check    # 校验库内 PNG 是否与 ico 一致(CI 用)
"""
# ruff: noqa: T201  这是命令行脚本, stdout 就是它的输出通道。

import argparse
import io
import sys
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
ICO_SRC = REPO_ROOT / ".github" / "inno-setup" / "haitun.ico"
PNG_OUT = REPO_ROOT / ".github" / "macos" / "haitun-1024.png"
TARGET_SIZE = 1024


def _render_frame() -> Image.Image:
    """ico 最大帧 → RGBA → 边长为 TARGET_SIZE 的帧。

    ico 可能含多帧, 逐帧 seek 比较尺寸选最大那帧。遍历用 EOFError 收尾而不是
    ``n_frames``/``.ico``: 前者在 IcoImageFile 上并不存在 (实测 AttributeError),
    后者是插件专属属性, 而 Image.open() 的静态类型是基类 ImageFile, ty 不认插件
    私有成员 —— 两个都不碰, 只用基类 API。
    源只有 256px 时 LANCZOS 放大到 1024: 放大是唯一选择, macOS 的 512/1024 表示
    必须存在, 宁可在 Retina 下略软也不要有缺口。
    """
    if not ICO_SRC.is_file():
        raise FileNotFoundError(f"icon source missing: {ICO_SRC}")
    with Image.open(ICO_SRC) as ico:
        largest: tuple[int, int] = (0, 0)
        largest_index = 0
        i = 0
        while True:
            try:
                ico.seek(i)
            except EOFError:
                break
            width, height = ico.size
            if width * height > largest[0] * largest[1]:
                largest = ico.size
                largest_index = i
            i += 1
        ico.seek(largest_index)
        frame = ico.convert("RGBA")
    if frame.size != (TARGET_SIZE, TARGET_SIZE):
        frame = frame.resize((TARGET_SIZE, TARGET_SIZE), Image.Resampling.LANCZOS)
    return frame


def render_png() -> bytes:
    """生成 PNG 字节 (写文件用)。"""
    buf = io.BytesIO()
    _render_frame().save(buf, format="PNG")
    return buf.getvalue()


def _committed_pixels(path: Path) -> bytes:
    """解码库内 PNG → RGBA 原始像素。

    --check 比的是**像素**而不是 PNG 字节: deflate 压缩输出随 zlib 版本漂移,
    同样的像素在 Linux CI 与 Windows 开发机上编出的字节不同, 字节级比较会把
    干净检出误判成过期 (实测)。像素级比较只关心内容, 与压缩器无关。
    """
    with Image.open(path) as img:
        return img.convert("RGBA").tobytes()


def _force_utf8_stdout() -> None:
    """把 stdout 切成 UTF-8。本脚本输出含中文, 不切在 Windows 控制台会直接崩。

    同 scripts/gen_legal_html.py: reconfigure 在 stdout 被替换成非 TextIOWrapper
    时可能不存在 (某些捕获实现), 所以先探再调; 探不到就维持原样。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验不写入: 库内 PNG 与 ico 不一致时返回 1 (改了 ico 忘了重新生成会在 CI 里失败)",
    )
    args = parser.parse_args(argv)

    if args.check:
        current = _committed_pixels(PNG_OUT) if PNG_OUT.is_file() else b""
        if current != _render_frame().tobytes():
            print(f"过期: {PNG_OUT.relative_to(REPO_ROOT)} (请运行 python scripts/gen_haitun_icon_png.py)")
            return 1
        print("PNG 图标与 ico 源一致。")
        return 0
    generated = render_png()
    PNG_OUT.write_bytes(generated)
    print(f"已生成 {PNG_OUT.relative_to(REPO_ROOT)} ({len(generated):,} 字节, {TARGET_SIZE}x{TARGET_SIZE})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
