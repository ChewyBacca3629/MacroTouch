# core/codegen.py
import os
import re
import shutil
import hashlib
from pathlib import Path
from typing import Dict, Any, List

from PIL import Image, ImageSequence

from modules.helpers import c_ident_from_filename


def _pick_transparent_key(used: set[int]) -> int:
    candidates = [
        0xF81F,  # magenta
        0x07FF,  # cyan
        0xFFE0,  # yellow
        0x07E0,  # green
        0x001F,  # blue
        0xF800,  # red
        0x0000,  # black
    ]
    for key in candidates:
        if key != 0xFFFF and key not in used:
            return key
    for key in range(0x0000, 0xFFFF):
        if key != 0xFFFF and key not in used:
            return key
    return 0xFFFE


def _rgb_close(a: tuple[int, int, int], b: tuple[int, int, int], tol: int) -> bool:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2])) <= tol


def _detect_background_color(
    pixels: list[tuple[int, int, int]],
    width: int,
    height: int,
    tol: int,
) -> tuple[int, int, int] | None:
    if not pixels or width <= 0 or height <= 0:
        return None
    corners = [
        pixels[0],
        pixels[width - 1],
        pixels[(height - 1) * width],
        pixels[height * width - 1],
    ]
    for base in corners:
        similar = [c for c in corners if _rgb_close(c, base, tol)]
        if len(similar) >= 3:
            return base
    return None


def _apply_rounded_mask(
    values: list[int],
    width: int,
    height: int,
    radius: int,
    key: int,
) -> None:
    if radius <= 0:
        return
    r = min(radius, width // 2, height // 2)
    if r <= 0:
        return
    cx = r - 0.5
    cy = r - 0.5
    r2 = (r - 0.5) * (r - 0.5)
    for y in range(r):
        for x in range(r):
            dx = cx - x
            dy = cy - y
            if (dx * dx + dy * dy) > r2:
                values[y * width + x] = key
                values[y * width + (width - 1 - x)] = key
                values[(height - 1 - y) * width + x] = key
                values[(height - 1 - y) * width + (width - 1 - x)] = key


def image_to_rgb565_array(
    image_path: str,
    width: int = 80,
    height: int = 80,
    round_radius: int | None = None,
    use_transparency: bool = True,
    bg_fill: tuple[int, int, int] | None = None,
    dither: bool = False,
    dither_mode: str = "auto",
    image_obj: Image.Image | None = None,
) -> tuple[list[str], int]:
    """Load an image, resize, and return a list of hex RGB565 values + transparent key."""
    try:
        if image_obj is not None:
            img = image_obj.convert("RGBA")
        else:
            img = Image.open(image_path).convert("RGBA")
        img = img.resize((width, height), Image.Resampling.LANCZOS)
        pixels = list(img.getdata())
        use_dither = False
        if dither:
            step = max(1, int(min(width, height) / 16))
            uniq: set[tuple[int, int, int]] = set()
            for y in range(0, height, step):
                for x in range(0, width, step):
                    r, g, b, a = pixels[y * width + x]
                    if a < 16:
                        continue
                    uniq.add((r >> 4, g >> 4, b >> 4))
                    if len(uniq) > 64:
                        use_dither = True
                        break
                if use_dither:
                    break
        mode = str(dither_mode or "auto").lower()
        # oversampled icons can explode into visible noise; clamp to safer mode
        if (width >= 200 and height >= 200) and use_transparency:
            mode = "none"
        if mode == "auto":
            if (width >= 200 or height >= 200) and not use_transparency:
                mode = "fs"
            else:
                mode = "noise" if use_dither else "none"
        bayer4 = (
            (0, 8, 2, 10),
            (12, 4, 14, 6),
            (3, 11, 1, 9),
            (15, 7, 13, 5),
        )
        def _clamp8(v: float) -> int:
            return 0 if v < 0 else (255 if v > 255 else int(v))
        rgb565_vals: list[int] = []
        used: set[int] = set()
        has_alpha = False
        if mode == "fs" and not use_transparency:
            # Floyd-Steinberg error diffusion for large backgrounds
            err_r = [0.0] * (width + 2)
            err_g = [0.0] * (width + 2)
            err_b = [0.0] * (width + 2)
            next_err_r = [0.0] * (width + 2)
            next_err_g = [0.0] * (width + 2)
            next_err_b = [0.0] * (width + 2)
            for y in range(height):
                if y & 1:
                    x_range = range(width - 1, -1, -1)
                    dir_sign = -1
                else:
                    x_range = range(width)
                    dir_sign = 1
                for x in x_range:
                    idx = y * width + x
                    r, g, b, a = pixels[idx]
                    if a < 255:
                        has_alpha = True
                    if bg_fill is not None and a < 255:
                        br, bg, bb = bg_fill
                        r = int((r * a + br * (255 - a)) / 255)
                        g = int((g * a + bg * (255 - a)) / 255)
                        b = int((b * a + bb * (255 - a)) / 255)
                    r = _clamp8(r + err_r[x + 1])
                    g = _clamp8(g + err_g[x + 1])
                    b = _clamp8(b + err_b[x + 1])
                    r5 = (r * 31 + 127) // 255
                    g6 = (g * 63 + 127) // 255
                    b5 = (b * 31 + 127) // 255
                    rgb565 = (r5 << 11) | (g6 << 5) | b5
                    rgb565_vals.append(rgb565)
                    used.add(rgb565)
                    r_q = (r5 * 255) / 31.0
                    g_q = (g6 * 255) / 63.0
                    b_q = (b5 * 255) / 31.0
                    er = r - r_q
                    eg = g - g_q
                    eb = b - b_q
                    if dir_sign > 0:
                        err_r[x + 2] += er * (7 / 16)
                        err_g[x + 2] += eg * (7 / 16)
                        err_b[x + 2] += eb * (7 / 16)
                        next_err_r[x + 0] += er * (3 / 16)
                        next_err_g[x + 0] += eg * (3 / 16)
                        next_err_b[x + 0] += eb * (3 / 16)
                        next_err_r[x + 1] += er * (5 / 16)
                        next_err_g[x + 1] += eg * (5 / 16)
                        next_err_b[x + 1] += eb * (5 / 16)
                        next_err_r[x + 2] += er * (1 / 16)
                        next_err_g[x + 2] += eg * (1 / 16)
                        next_err_b[x + 2] += eb * (1 / 16)
                    else:
                        err_r[x + 0] += er * (7 / 16)
                        err_g[x + 0] += eg * (7 / 16)
                        err_b[x + 0] += eb * (7 / 16)
                        next_err_r[x + 2] += er * (3 / 16)
                        next_err_g[x + 2] += eg * (3 / 16)
                        next_err_b[x + 2] += eb * (3 / 16)
                        next_err_r[x + 1] += er * (5 / 16)
                        next_err_g[x + 1] += eg * (5 / 16)
                        next_err_b[x + 1] += eb * (5 / 16)
                        next_err_r[x + 0] += er * (1 / 16)
                        next_err_g[x + 0] += eg * (1 / 16)
                        next_err_b[x + 0] += eb * (1 / 16)
                err_r, next_err_r = next_err_r, err_r
                err_g, next_err_g = next_err_g, err_g
                err_b, next_err_b = next_err_b, err_b
                for i in range(width + 2):
                    next_err_r[i] = 0.0
                    next_err_g[i] = 0.0
                    next_err_b[i] = 0.0
        else:
            use_noise = use_dither and (width >= 200 or height >= 200)
            for i, (r, g, b, a) in enumerate(pixels):
                if a < 255:
                    has_alpha = True
                if not use_transparency and bg_fill is not None and a < 255:
                    br, bg, bb = bg_fill
                    r = int((r * a + br * (255 - a)) / 255)
                    g = int((g * a + bg * (255 - a)) / 255)
                    b = int((b * a + bb * (255 - a)) / 255)
                if use_dither:
                    x = i % width
                    y = i // width
                    if use_noise:
                        n = x * 374761393 + y * 668265263 + 0x9E3779B9
                        n = (n ^ (n >> 13)) * 1274126177
                        n ^= (n >> 16)
                        offset = (n & 0xFF) / 255.0 - 0.5
                        strength = 6.0
                    else:
                        d = bayer4[y & 3][x & 3]
                        offset = (d - 7.5) / 16.0
                        strength = 8.0
                    r = _clamp8(r + offset * strength)
                    g = _clamp8(g + offset * strength)
                    b = _clamp8(b + offset * strength)
                r5 = (r * 31 + 127) // 255
                g6 = (g * 63 + 127) // 255
                b5 = (b * 31 + 127) // 255
                rgb565 = (r5 << 11) | (g6 << 5) | b5
                rgb565_vals.append(rgb565)
                used.add(rgb565)
        key = _pick_transparent_key(used)

        if use_transparency:
            if has_alpha:
                # transparent pixels -> use key (avoid color fringes on custom backgrounds)
                # higher cutoff removes semi-transparent halos on colored backgrounds
                for i, (r, g, b, a) in enumerate(pixels):
                    if a <= 96:
                        rgb565_vals[i] = key
            else:
                bg_color = _detect_background_color(pixels, width, height, tol=14)
                if bg_color is not None:
                    for i, (r, g, b, _a) in enumerate(pixels):
                        if _rgb_close((r, g, b), bg_color, 14):
                            rgb565_vals[i] = key

        if round_radius and round_radius > 0:
            _apply_rounded_mask(rgb565_vals, width, height, int(round_radius), key)
        elif not use_transparency:
            key = 0xFFFF
        rgb565_data = [f"0x{val:04X}" for val in rgb565_vals]
        return rgb565_data, key
    except Exception as e:
        print(f"Chyba pri konverzii obrázka {image_path}: {e}")
        return [], 0xFFFF


def gif_frames_rgba(
    image_path: str,
    max_frames: int = 8,
) -> tuple[list[Image.Image], int]:
    """Load up to max_frames GIF frames as RGBA images + avg frame duration in ms."""
    frames: list[Image.Image] = []
    durations: list[int] = []
    try:
        with Image.open(image_path) as img:
            if str(getattr(img, "format", "") or "").upper() != "GIF":
                return [], 0
            frame_total = int(getattr(img, "n_frames", 1) or 1)
            take = max(1, min(max_frames, frame_total))
            for i in range(take):
                try:
                    img.seek(i)
                except Exception:
                    break
                try:
                    frame_rgba = img.convert("RGBA")
                except Exception:
                    continue
                frames.append(frame_rgba.copy())
                try:
                    dur = int(img.info.get("duration", 100) or 100)
                except Exception:
                    dur = 100
                if dur < 20:
                    dur = 20
                if dur > 500:
                    dur = 500
                durations.append(dur)
    except Exception:
        return [], 0

    if len(frames) < 2:
        return [], 0
    if not durations:
        durations = [100]
    avg_ms = int(round(sum(durations) / max(1, len(durations))))
    avg_ms = max(40, min(250, avg_ms))
    return frames, avg_ms


def _normalize_hex_color(value: str, fallback: str) -> str:
    text = (value or "").strip()
    if not text:
        return fallback
    if not text.startswith("#"):
        text = "#" + text
    if len(text) == 4:
        text = "#" + "".join(ch * 2 for ch in text[1:])
    if not re.match(r"^#[0-9A-Fa-f]{6}$", text):
        return fallback
    return text.upper()


def _hex_to_rgb565(hex_color: str) -> int:
    text = _normalize_hex_color(hex_color, "#000000")
    r = int(text[1:3], 16)
    g = int(text[3:5], 16)
    b = int(text[5:7], 16)
    r5 = (r >> 3) & 0x1F
    g6 = (g >> 2) & 0x3F
    b5 = (b >> 3) & 0x1F
    return (r5 << 11) | (g6 << 5) | b5


def _hex_to_rgb_tuple(hex_color: str) -> tuple[int, int, int]:
    text = _normalize_hex_color(hex_color, "#000000")
    return (
        int(text[1:3], 16),
        int(text[3:5], 16),
        int(text[5:7], 16),
    )


def _c_string_literal(text: str) -> str:
    raw = text or ""
    raw = raw.replace("\\", "\\\\").replace('"', '\\"')
    return f"\"{raw}\""


def _file_sig(path: Path) -> tuple[int, int]:
    try:
        st = path.stat()
        return int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))), int(st.st_size)
    except Exception:
        return (0, 0)


def _write_text_if_changed(path: Path, content: str, encoding: str = "utf-8") -> bool:
    try:
        if path.is_file():
            try:
                if path.read_text(encoding=encoding) == content:
                    return False
            except Exception:
                pass
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding=encoding)
        return True
    except Exception:
        raise


def _dir_quick_signature(base_dir: Path) -> str:
    digest = hashlib.sha1()
    files: list[Path] = sorted(p for p in base_dir.rglob("*") if p.is_file())
    for file_path in files:
        rel = file_path.relative_to(base_dir).as_posix()
        mtime_ns, size = _file_sig(file_path)
        digest.update(rel.encode("utf-8", errors="ignore"))
        digest.update(b"\0")
        digest.update(str(mtime_ns).encode("ascii", errors="ignore"))
        digest.update(b":")
        digest.update(str(size).encode("ascii", errors="ignore"))
        digest.update(b"\n")
    return digest.hexdigest()


def _copy_support_files(export_dir: Path) -> None:
    base_dir = Path(__file__).resolve().parent
    predecls_src = base_dir / "predecls.h"
    fonts_src = base_dir / "fonts"

    if not predecls_src.is_file():
        raise FileNotFoundError(f"Missing support file: {predecls_src}")
    if not fonts_src.is_dir():
        raise FileNotFoundError(f"Missing fonts dir: {fonts_src}")

    export_dir.mkdir(parents=True, exist_ok=True)
    predecls_dst = export_dir / "predecls.h"
    if not predecls_dst.is_file() or predecls_src.read_bytes() != predecls_dst.read_bytes():
        shutil.copyfile(predecls_src, predecls_dst)

    fonts_dst = export_dir / "fonts"
    fonts_sig_path = export_dir / ".fonts-signature"
    src_sig = _dir_quick_signature(fonts_src)
    prev_sig = ""
    try:
        if fonts_sig_path.is_file():
            prev_sig = fonts_sig_path.read_text(encoding="utf-8").strip()
    except Exception:
        prev_sig = ""

    if (not fonts_dst.is_dir()) or (prev_sig != src_sig):
        if fonts_dst.exists():
            shutil.rmtree(fonts_dst, ignore_errors=True)
        shutil.copytree(fonts_src, fonts_dst)
        fonts_sig_path.write_text(src_sig, encoding="utf-8")


def generate_main_ino(
    profiles: Dict[str, Dict[str, Any]],
    export_path: str,
    display_settings: Dict[str, Any] | None = None,
) -> None:
    """Generate the Arduino .ino source for all profiles into export_path."""
    export_dir = Path(export_path).resolve().parent
    _copy_support_files(export_dir)
    # --- Zber profilov a metadát ---
    # Na ESP chceme všetky profily, ale monitor profil má špeciálnu logiku.
    all_profiles: List[tuple[str, Dict[str, Any]]] = list(profiles.items())
    PROFILES = len(all_profiles)

    if PROFILES == 0:
        raise ValueError("Nemáš žiadne profily – nie je čo generovať pre ESP.")

    # index profilu, ktorý má mode == "monitor"
    monitor_index = -1
    for idx, (pname, pconf) in enumerate(all_profiles):
        if isinstance(pconf, dict) and pconf.get("mode") == "monitor":
            monitor_index = idx
            break
        
    # index profilu, ktorý má mode == "media"
    media_index = -1
    for idx, (pname, pconf) in enumerate(all_profiles):
        if isinstance(pconf, dict) and pconf.get("mode") == "media":
            media_index = idx
            break

    # index profilu, ktorý má mode == "mixer"
    mixer_index = -1
    for idx, (pname, pconf) in enumerate(all_profiles):
        if isinstance(pconf, dict) and pconf.get("mode") == "mixer":
            mixer_index = idx
            break

    SCREEN_W, SCREEN_H = 480, 320
    ICON_OVERSAMPLE = 1.30
    ICON_MAX = 320

    display = display_settings if isinstance(display_settings, dict) else {}
    scr = display.get("screensaver") if isinstance(display.get("screensaver"), dict) else {}
    btn = display.get("buttons") if isinstance(display.get("buttons"), dict) else {}
    grid = display.get("grid") if isinstance(display.get("grid"), dict) else {}

    def _get_bool(d: dict[str, Any], key: str, default: bool) -> bool:
        return bool(d.get(key, default))

    def _get_int(d: dict[str, Any], key: str, default: int, lo: int, hi: int) -> int:
        try:
            val = int(d.get(key, default))
        except Exception:
            val = default
        return max(lo, min(hi, val))

    def _get_float(d: dict[str, Any], key: str, default: float, lo: float, hi: float) -> float:
        try:
            val = float(d.get(key, default))
        except Exception:
            val = default
        return max(lo, min(hi, val))

    scr_enabled = _get_bool(scr, "enabled", True)
    scr_idle_ms = _get_int(scr, "idle_ms", 60000, 5000, 3600_000)
    scr_time_size = _get_int(scr, "time_size", 3, 1, 8)
    scr_label_size = _get_int(scr, "label_size", 1, 1, 6)
    scr_label = str(scr.get("label", "MacroTouch"))
    scr_show_label = _get_bool(scr, "show_label", bool(scr_label))
    scr_time_font_raw = str(scr.get("time_font", "Title") or "Title").strip().lower()
    scr_time_font_map = {
        "default": 0,
        "title": 1,
        "body": 2,
        "meta": 3,
        "mono": 4,
        "digital": 4,
    }
    scr_time_font_id = scr_time_font_map.get(scr_time_font_raw, 0)

    scr_bg = _hex_to_rgb565(str(scr.get("bg_color", "#080C12")))
    scr_bg_rgb = _hex_to_rgb_tuple(str(scr.get("bg_color", "#080C12")))
    scr_time = _hex_to_rgb565(str(scr.get("time_color", "#F0F0F0")))
    scr_label_col = _hex_to_rgb565(str(scr.get("label_color", "#788296")))

    btn_bg = _hex_to_rgb565(str(btn.get("bg_color", "#000000")))
    btn_fg = _hex_to_rgb565(str(btn.get("fg_color", "#FFFFFF")))
    btn_bg_hi = _hex_to_rgb565(str(btn.get("bg_highlight", "#14181E")))
    btn_fg_hi = _hex_to_rgb565(str(btn.get("fg_highlight", "#F0F0F0")))
    btn_text_size = _get_float(btn, "text_size", 1.1, 0.6, 2.5)
    icon_transparent = _get_bool(btn, "icon_transparent", True)
    MARGIN = 2 if icon_transparent else 4
    icon_bg_rgb = _hex_to_rgb_tuple(str(btn.get("bg_color", "#000000")))
    grid_bg = _hex_to_rgb565(str(grid.get("bg_color", "#000000")))
    grid_bg_rgb = _hex_to_rgb_tuple(str(grid.get("bg_color", "#000000")))
    scr_bg_image = str(scr.get("bg_image", "") or "").strip()
    grid_bg_image = str(grid.get("bg_image", "") or "").strip()
    if scr_bg_image:
        scr_bg_image = os.path.expanduser(os.path.expandvars(scr_bg_image))
        if not os.path.isfile(scr_bg_image):
            scr_bg_image = ""
    if grid_bg_image:
        grid_bg_image = os.path.expanduser(os.path.expandvars(grid_bg_image))
        if not os.path.isfile(grid_bg_image):
            grid_bg_image = ""

    profile_meta: list[tuple[int, int, int]] = []
    size_bucket: set[int] = set()

    for name, prof in all_profiles:
        mode = prof.get("mode", "grid")

        if mode in ("monitor", "mixer"):
            # dummy grid – 1x1, bez ikon
            rows_p, cols_p, s = 1, 1, 0
        else:
            rows_p = max(1, min(4, int(prof.get("rows", 3))))
            cols_p = max(1, min(4, int(prof.get("cols", 4))))
            cell_w = SCREEN_W // cols_p
            cell_h = SCREEN_H // rows_p
            s_base = max(32, min(cell_w, cell_h) - MARGIN)
            s = int(round(s_base * ICON_OVERSAMPLE))
            if s < s_base:
                s = s_base
            if s > ICON_MAX:
                s = ICON_MAX
            s = (s // 8) * 8

        profile_meta.append((rows_p, cols_p, s))
        if s > 0:
            size_bucket.add(s)

    sizes_sorted = sorted(size_bucket)

    # --- Zoznam ikon a stabilný ident ---
    icon_order: list[str] = []
    icon_source_map: dict[str, str] = {}
    seen: set[str] = set()
    for _, prof in all_profiles:
        rows_p = max(1, min(5, int(prof.get("rows", 3))))
        cols_p = max(1, min(5, int(prof.get("cols", 4))))
        for r in range(rows_p):
            for c in range(cols_p):
                ip_raw = (prof.get(f"btn{r}{c}", {}).get("icon") or "").strip()
                if not ip_raw:
                    continue
                ip = os.path.expanduser(os.path.expandvars(ip_raw))
                base = os.path.splitext(os.path.basename(ip_raw))[0]
                if base not in seen:
                    seen.add(base)
                    icon_order.append(base)
                    if os.path.isfile(ip):
                        icon_source_map[base] = ip
                elif base not in icon_source_map and os.path.isfile(ip):
                    icon_source_map[base] = ip

    icon_ident_map: dict[str, str] = {}
    used: set[str] = set()
    for base in icon_order:
        ident = c_ident_from_filename(base)
        orig = ident
        k = 2
        while ident in used:
            ident = f"{orig}_{k}"
            k += 1
        used.add(ident)
        icon_ident_map[base] = ident

    lines: list[str] = []
    ap = lines.append

    # --- hlavičky ---
    ap('#include <Arduino.h>\n')
    ap('#include <string.h>\n')
    ap('#include <stdio.h>\n')
    ap('#include <math.h>\n')
    ap('#include <LovyanGFX.hpp>\n')
    ap('#include "predecls.h"\n')
    ap('#include "fonts/Inter_SemiBold_26.h"\n')
    ap('#include "fonts/Inter_Regular_16.h"\n')
    ap('#include "fonts/Inter_Medium_12.h"\n')
    ap('#define DETENT_STEPS 2\n\n')
    ap('// === Auto-generated MacroTouch (LGFX + inline config) ===\n\n')

    ap(r'''// ====== LGFX inline config (SPI ILI9488 + XPT2046) ======
class LGFX : public lgfx::LGFX_Device {
  lgfx::Panel_ILI9488 _panel;
  lgfx::Bus_SPI       _bus;
  lgfx::Touch_XPT2046 _touch;
public:
  LGFX() {
    { auto b = _bus.config();
      b.spi_host   = SPI3_HOST; b.spi_mode=0; b.freq_write=40000000; b.freq_read=16000000;
      b.pin_sclk=12; b.pin_mosi=11; b.pin_miso=15; b.pin_dc=7; b.dma_channel=1;
      _bus.config(b); _panel.setBus(&_bus); }
    { auto p = _panel.config();
      p.pin_cs=10; p.pin_rst=-1; p.panel_width=320; p.panel_height=480; p.memory_width=320; p.memory_height=480; p.bus_shared=true;
      _panel.config(p); }
    { auto tcfg = _touch.config();
      tcfg.spi_host=SPI2_HOST; tcfg.freq=2000000; tcfg.pin_sclk=13; tcfg.pin_mosi=6; tcfg.pin_miso=5; tcfg.pin_cs=14; tcfg.pin_int=4;
      tcfg.offset_rotation=2; tcfg.x_min=3850; tcfg.x_max=350; tcfg.y_min=350; tcfg.y_max=3850; tcfg.bus_shared=false;
      _touch.config(tcfg); _panel.setTouch(&_touch); }
    setPanel(&_panel);
  }
};
LGFX tft;
lgfx::LGFX_Sprite g_sprite(&tft);
lgfx::LGFX_Sprite g_transOld(&tft);
lgfx::LGFX_Sprite g_transNew(&tft);
bool g_spriteReady = false;
bool g_transSpritesReady = false;
lgfx::LovyanGFX* g_overrideTarget = nullptr;
''')

    ap('static constexpr auto UI_FONT = &lgfx::v1::fonts::Font2;\n\n')
    ap(r'''static lgfx::PointerWrapper g_fontTitleData(Inter_SemiBold_26_vlw, sizeof(Inter_SemiBold_26_vlw));
static lgfx::PointerWrapper g_fontBodyData(Inter_Regular_16_vlw, sizeof(Inter_Regular_16_vlw));
static lgfx::PointerWrapper g_fontMetaData(Inter_Medium_12_vlw, sizeof(Inter_Medium_12_vlw));

static lgfx::VLWfont g_fontTitle;
static lgfx::VLWfont g_fontBody;
static lgfx::VLWfont g_fontMeta;
static bool g_mediaFontsReady = false;

static inline float clampf(float v, float lo, float hi) {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}

static bool initMediaFonts() {
  bool ok = true;
  ok = g_fontTitle.loadFont(&g_fontTitleData) && ok;
  ok = g_fontBody.loadFont(&g_fontBodyData) && ok;
  ok = g_fontMeta.loadFont(&g_fontMetaData) && ok;
  g_mediaFontsReady = ok;
  return ok;
}

static inline void setMediaFontTitle(lgfx::LovyanGFX* gfx) {
  if (g_mediaFontsReady) {
    gfx->setFont(&g_fontTitle);
    gfx->setTextSize(1);
  } else {
    gfx->setFont(UI_FONT);
    gfx->setTextSize(2);
  }
}

static inline void setMediaFontBody(lgfx::LovyanGFX* gfx) {
  if (g_mediaFontsReady) {
    gfx->setFont(&g_fontBody);
    gfx->setTextSize(1);
  } else {
    gfx->setFont(UI_FONT);
    gfx->setTextSize(1);
  }
}

static inline void setMediaFontMeta(lgfx::LovyanGFX* gfx) {
  if (g_mediaFontsReady) {
    gfx->setFont(&g_fontMeta);
    gfx->setTextSize(1);
  } else {
    gfx->setFont(UI_FONT);
    gfx->setTextSize(1);
  }
}

static inline void setButtonFontForSize(lgfx::LovyanGFX* gfx, float textSize) {
  if (g_mediaFontsReady) {
    float scale = 1.0f;
    if (textSize <= 0.85f) {
      gfx->setFont(&g_fontMeta);
      scale = clampf(textSize / 0.85f, 0.6f, 1.0f);
    } else if (textSize <= 1.45f) {
      gfx->setFont(&g_fontBody);
      scale = clampf(textSize, 0.8f, 1.2f);
    } else {
      gfx->setFont(&g_fontTitle);
      scale = clampf(textSize / 1.6f, 0.9f, 1.2f);
    }
    gfx->setTextSize(scale);
  } else {
    gfx->setFont(UI_FONT);
    int scale = (textSize <= 1.1f) ? 1 : (textSize <= 1.9f ? 2 : 3);
    gfx->setTextSize(scale);
  }
}

extern const uint8_t SCREENSAVER_TIME_FONT;

static inline void setScreensaverTimeFont(lgfx::LovyanGFX* gfx) {
  switch (SCREENSAVER_TIME_FONT) {
    case 1: gfx->setFont(&g_fontTitle); break;
    case 2: gfx->setFont(&g_fontBody); break;
    case 3: gfx->setFont(&g_fontMeta); break;
    case 4: gfx->setFont(&lgfx::v1::fonts::Font7); break;
    default: gfx->setFont(UI_FONT); break;
  }
}

''')
    ap('#define SCREEN_WIDTH  480\n#define SCREEN_HEIGHT 320\n')
    ap('// Encoder + tlačidlá + potenciometer\n')
    ap('#define ENC_A   18\n#define ENC_B   17\n#define ENC_SW  39\n')
    ap('#define BTN_A   40\n#define BTN_B   41\n')

    # BTN_MAX len z GRID profilov
    max_rows = 1
    max_cols = 1
    for name, prof in all_profiles:
        if prof.get("mode", "grid") == "grid":
            rows = max(1, min(4, int(prof.get("rows", 3))))
            cols = max(1, min(4, int(prof.get("cols", 4))))
            max_rows = max(max_rows, rows)
            max_cols = max(max_cols, cols)
    BTN_MAX = max_rows * max_cols
    ap(f'#define BTN_MAX {BTN_MAX}\n\n')


    ap('struct Button{ int x,y,w,h; const char* label; const uint16_t* icon; };\n')
    ap('Button buttons[BTN_MAX];\n\n')

    ap(f'const int PROFILES = {PROFILES};\n')
    ap('const char* PROFILE_NAMES[] = { ' + ', '.join(f'"{n}"' for n, _ in all_profiles) + ' };\n')
    ap('int PROFILE_ROWS[] = { ' + ', '.join(str(r) for r, _, __ in profile_meta) + ' };\n')
    ap('int PROFILE_COLS[] = { ' + ', '.join(str(c) for _, c, __ in profile_meta) + ' };\n')
    ap('const uint16_t PROFILE_ICON_SIZE[] = { ' + ', '.join(str(s) for __, __, s in profile_meta) + ' };\n')
    ap('int current_profile = 0;\n')
    ap('uint32_t g_last_input_ms = 0;\n')
    ap('uint32_t g_last_ui_ms = 0;\n')
    ap('uint32_t g_icon_anim_last_ms = 0;\n')
    ap('bool g_allow_draw = true;\n')
    ap('bool g_screensaver_active = false;\n')
    ap('uint32_t g_screensaver_last_draw = 0;\n')
    ap('bool g_clock_valid = false;\n')
    ap('uint8_t g_clock_h = 0, g_clock_m = 0, g_clock_s = 0;\n')
    ap('uint32_t g_clock_last_ms = 0;\n')
    ap('bool g_date_valid = false;\n')
    ap('int g_date_y = 0;\n')
    ap('uint8_t g_date_m = 0, g_date_d = 0, g_date_dow = 0;\n')
    ap('bool g_temp_valid = false;\n')
    ap('float g_temp_c = -1000.0f;\n')
    ap('uint32_t g_last_serial_ms = 0;\n')
    ap('bool g_wifi_connected = false;\n')
    ap(f'const bool SCREENSAVER_ENABLED = {"true" if scr_enabled else "false"};\n')
    ap(f'const uint32_t SCREENSAVER_IDLE_MS = {scr_idle_ms};\n')
    ap(f'const uint16_t COLOR_SCR_BG = 0x{scr_bg:04X};\n')
    ap(f'const uint16_t COLOR_SCR_TIME = 0x{scr_time:04X};\n')
    ap(f'const uint16_t COLOR_SCR_LABEL = 0x{scr_label_col:04X};\n')
    ap(f'const float SCREENSAVER_TIME_SIZE = {scr_time_size:.2f}f;\n')
    ap(f'const uint8_t SCREENSAVER_TIME_FONT = {scr_time_font_id};\n')
    ap(f'const float SCREENSAVER_LABEL_SIZE = {scr_label_size:.2f}f;\n')
    ap(f'const bool SCREENSAVER_SHOW_LABEL = {"true" if scr_show_label else "false"};\n')
    ap(f'const char* SCREENSAVER_LABEL = {_c_string_literal(scr_label)};\n')
    ap(f'const uint16_t COLOR_BTN_BG = 0x{btn_bg:04X};\n')
    ap(f'const uint16_t COLOR_BTN_FG = 0x{btn_fg:04X};\n')
    ap(f'const uint16_t COLOR_BTN_BG_HI = 0x{btn_bg_hi:04X};\n')
    ap(f'const uint16_t COLOR_BTN_FG_HI = 0x{btn_fg_hi:04X};\n')
    ap(f'const bool ICONS_TRANSPARENT = {"true" if icon_transparent else "false"};\n')
    ap(f'const uint16_t COLOR_GRID_BG = 0x{grid_bg:04X};\n')
    ap('const uint16_t ICON_KEY_NONE = 0xFFFF;\n')
    ap(f'const float BTN_TEXT_SIZE = {btn_text_size:.2f}f;\n')
    ap('const int BTN_INSET = 12;\n')
    ap('const int BTN_RADIUS = 20;\n')
    ap('const uint8_t WIDGET_KIND_WEATHER = 1;\n')
    ap('const uint8_t WIDGET_KIND_METRIC = 2;\n')
    ap('const uint32_t UI_FRAME_MS = 25;\n')
    ap('struct TransitionState { bool active; int from; int to; int dir; uint32_t start_ms; uint32_t duration_ms; };\n')
    ap('TransitionState g_trans = { false, 0, 0, 0, 0, 360 };\n')
    ap('bool g_transUseSnapshots = false;\n')
    ap(f'const int MONITOR_PROFILE_INDEX = {monitor_index};\n\n')
    ap(f'const int MEDIA_PROFILE_INDEX   = {media_index};\n\n')
    ap(f'const int MIXER_PROFILE_INDEX   = {mixer_index};\n\n')

    # --- monitor state ---
    # --- monitor state ---
    ap('// --- System monitor state (from PC) ---\n')
    ap('float g_cpu = 0.0f;\n')
    ap('float g_ram = 0.0f;\n')
    ap('float g_gpu = -1.0f;\n')
    ap('float g_disk = 0.0f;\n')
    ap('float g_net = 0.0f;\n')
    ap('float g_fps = -1.0f;\n')
    ap('bool  g_monitorDirty = true;\n')
    ap('bool  g_monitorFullRedraw = true;\n\n')

    # --- media state ---
    # --- media state ---
    ap('// --- Media player state (from PC) ---\n')
    # pozor: nesmie sa volať DEFAULT, lebo Arduino.h má #define DEFAULT 1
    ap('enum class MediaSource : uint8_t { MEDIA_GENERIC, MEDIA_SPOTIFY, MEDIA_YOUTUBE, MEDIA_VLC };\n')
    ap('struct MediaState {\n')
    ap('  MediaSource source      = MediaSource::MEDIA_GENERIC;\n')
    ap('  String      track       = "—";\n')
    ap('  int         position_s  = 0;\n')
    ap('  int         duration_s  = 1;\n')
    ap('  bool        isPlaying   = false;\n')
    ap('  int         volume_pct  = -1;\n')
    ap('  bool        dirty       = true;\n')
    ap('};\n')
    ap('MediaState g_media;\n\n')
    ap('// --- Mixer state (from PC) ---\n')
    ap('struct MixerSlot {\n')
    ap('  int  volume = 0;\n')
    ap('  bool muted  = false;\n')
    ap('  bool active = false;\n')
    ap('  char name[16];\n')
    ap('};\n')
    ap('struct MixerState {\n')
    ap('  int  master = 0;\n')
    ap('  bool masterMuted = false;\n')
    ap('  int  mic = 0;\n')
    ap('  bool micMuted = false;\n')
    ap('  MixerSlot apps[2];\n')
    ap('  bool dirty = true;\n')
    ap('};\n')
    ap('MixerState g_mixer;\n')
    ap('bool g_mixerFullRedraw = true;\n\n')
    ap('// --- Weather widget state (from PC) ---\n')
    ap('struct WeatherWidgetState {\n')
    ap('  bool valid = false;\n')
    ap('  bool dirty = true;\n')
    ap('  float tempC = 0.0f;\n')
    ap('  float feelsC = 0.0f;\n')
    ap('  int humidity = -1;\n')
    ap('  float wind = 0.0f;\n')
    ap('  int code = 0;\n')
    ap('  char label[28];\n')
    ap('  char desc[28];\n')
    ap('  uint32_t animMs = 0;\n')
    ap('  uint8_t animPhase = 0;\n')
    ap('};\n')
    ap('WeatherWidgetState g_weather;\n\n')
    ap('// --- Metric widget state (from PC) ---\n')
    ap('struct MetricWidgetState {\n')
    ap('  bool dirty = true;\n')
    ap('  float cpu = -1.0f;\n')
    ap('  float ram = -1.0f;\n')
    ap('  float gpu = -1.0f;\n')
    ap('  float gpuTemp = -1.0f;\n')
    ap('  float fps = -1.0f;\n')
    ap('  float net = -1.0f;\n')
    ap('  float disk = -1.0f;\n')
    ap('  float cpuGhz = -1.0f;\n')
    ap('};\n')
    ap('MetricWidgetState g_metric;\n\n')
    ap('// --- ESP render benchmark state ---\n')
    ap('static constexpr uint8_t ESP_BENCH_MAX_TILES = 16;\n')
    ap('struct EspBenchState {\n')
    ap('  bool active = false;\n')
    ap('  bool directFast = true;\n')
    ap('  bool fullRedraw = true;\n')
    ap('  uint8_t tiles = 6;\n')
    ap('  uint16_t tileSize = 96;\n')
    ap('  uint16_t targetFps = 24;\n')
    ap('  uint32_t durationMs = 20000;\n')
    ap('  uint32_t startMs = 0;\n')
    ap('  uint32_t nextFrameMs = 0;\n')
    ap('  uint32_t lastStatMs = 0;\n')
    ap('  uint32_t lastHudMs = 0;\n')
    ap('  uint32_t frames = 0;\n')
    ap('  uint32_t expected = 0;\n')
    ap('  uint32_t dropped = 0;\n')
    ap('  uint32_t drawUsLast = 0;\n')
    ap('  uint64_t drawUsAcc = 0;\n')
    ap('  int16_t prevX[ESP_BENCH_MAX_TILES];\n')
    ap('  int16_t prevY[ESP_BENCH_MAX_TILES];\n')
    ap('  uint16_t tileCol[ESP_BENCH_MAX_TILES];\n')
    ap('  uint16_t glowCol[ESP_BENCH_MAX_TILES];\n')
    ap('  uint16_t bgCol = 0;\n')
    ap('  uint16_t hudBgCol = 0;\n')
    ap('  uint16_t hudFgCol = 0;\n')
    ap('  uint16_t pulseCol = 0;\n')
    ap('};\n')
    ap('EspBenchState g_bench;\n\n')
    ap('struct StatCard;\n\n')
    ap('static inline lgfx::LovyanGFX* drawTarget() {\n')
    ap('  if (g_overrideTarget) return g_overrideTarget;\n')
    ap('  return g_spriteReady\n')
    ap('    ? static_cast<lgfx::LovyanGFX*>(&g_sprite)\n')
    ap('    : static_cast<lgfx::LovyanGFX*>(&tft);\n')
    ap('}\n\n')
    ap('static inline void pushIfSprite() {\n')
    ap('  if (g_overrideTarget) return;\n')
    ap('  if (g_spriteReady) {\n')
    ap('    g_sprite.pushSprite(0, 0);\n')
    ap('  }\n')
    ap('}\n\n')
    ap('static inline int triWaveOffset(uint32_t tick, int amp) {\n')
    ap('  if (amp <= 0) return 0;\n')
    ap('  uint32_t a = (uint32_t)amp;\n')
    ap('  uint32_t p = tick % (a * 4U);\n')
    ap('  if (p < a) return (int)p;\n')
    ap('  if (p < a * 3U) return (int)(2U * a - p);\n')
    ap('  return (int)(p - 4U * a);\n')
    ap('}\n\n')

    ap(r'''
    // --- MEDIA COLORS (brand-ish) ---

uint16_t mediaBackgroundColor(MediaSource src) {
  switch (src) {
    case MediaSource::MEDIA_SPOTIFY: return tft.color565(30, 215, 96);  // tmavá zelená
    case MediaSource::MEDIA_YOUTUBE: return tft.color565(127, 29, 29);  // tmavá červená
    case MediaSource::MEDIA_VLC:     return tft.color565(120, 53, 15);  // tmavá oranžová
    case MediaSource::MEDIA_GENERIC:
    default:                         return tft.color565( 17, 24, 39);  // dark slate
  }
}

uint16_t mediaCardColor(MediaSource src) {
  switch (src) {
    case MediaSource::MEDIA_SPOTIFY: return tft.color565(24, 172, 77);  // card zelená
    case MediaSource::MEDIA_YOUTUBE: return tft.color565(185, 28, 28);  // card červená
    case MediaSource::MEDIA_VLC:     return tft.color565(217,119,  6);  // card oranžová
    case MediaSource::MEDIA_GENERIC:
    default:                         return tft.color565( 31, 41, 55);  // card sivomodrá
  }
}

uint16_t mediaAccentColor(MediaSource src) {
  switch (src) {
    case MediaSource::MEDIA_SPOTIFY: return tft.color565( 34,197, 94);  // “Spotify” zelená
    case MediaSource::MEDIA_YOUTUBE: return tft.color565(248,113,113);  // svetlá červená
    case MediaSource::MEDIA_VLC:     return tft.color565(252,211, 77);  // svetlá oranžová
    case MediaSource::MEDIA_GENERIC:
    default:                         return tft.color565(239,239,239);  // off-white
  }
}

uint16_t mediaHeaderColor(MediaSource src) {
  switch (src) {
    case MediaSource::MEDIA_SPOTIFY: return tft.color565(24, 172, 77);   // tmavšia zelená
    case MediaSource::MEDIA_YOUTUBE: return tft.color565(96, 22, 22);    // tmavšia červená
    case MediaSource::MEDIA_VLC:     return tft.color565(90, 40, 12);    // tmavšia oranžová
    case MediaSource::MEDIA_GENERIC:
    default:                         return tft.color565(12, 18, 30);    // dark slate
  }
}

uint16_t mediaInactiveColor(MediaSource src) {
  switch (src) {
    case MediaSource::MEDIA_SPOTIFY: return tft.color565(20, 120, 60);   // tmavšia zelená
    case MediaSource::MEDIA_YOUTUBE: return tft.color565(160, 80, 80);   // tlmená červená
    case MediaSource::MEDIA_VLC:     return tft.color565(200, 160, 60);  // tlmená oranžová
    case MediaSource::MEDIA_GENERIC:
    default:                         return tft.color565(60, 70, 80);   // tlmená sivá
  }
}
    ''')




    # --- ikony ---
    possible_exts = [".bmp", ".png", ".jpg", ".jpeg", ".ico", ".webp", ".gif"]
    icons_dir = (Path(__file__).resolve().parent.parent / "icons")
    if not icons_dir.is_dir():
        icons_dir = Path(__file__).resolve().parent.parent / "assets"
    icon_key_name: dict[tuple[str, int], str] = {}
    icon_frame_ptrs: dict[tuple[str, int], list[str]] = {}
    icon_frame_key_names: dict[tuple[str, int], list[str]] = {}
    icon_frame_interval_ms: dict[tuple[str, int], int] = {}
    for base in icon_order:
        full_path = icon_source_map.get(base)
        if full_path:
            full_path = os.path.expanduser(os.path.expandvars(full_path))
            if not os.path.isfile(full_path):
                full_path = None
        if not full_path:
            for ext in possible_exts:
                candidate = icons_dir / f"{base}{ext}"
                if candidate.is_file():
                    full_path = str(candidate)
                    break
        if not full_path:
            print(f" Ikona '{base}' sa nenašla v {icons_dir}. Preskakujem.")
            continue

        ident_base = icon_ident_map[base]
        is_gif = str(full_path).lower().endswith(".gif")
        for s in sizes_sorted:
            icon_radius = max(6, min(20, s // 5))
            frame_cap = 8 if s <= 160 else 4
            gif_frames, gif_interval = gif_frames_rgba(full_path, max_frames=frame_cap) if is_gif else ([], 0)
            if gif_frames:
                ptrs_for_size: list[str] = []
                keys_for_size: list[str] = []
                for fi, frame_img in enumerate(gif_frames):
                    var_name = f"{ident_base}_{s}" if fi == 0 else f"{ident_base}_{s}_F{fi}"
                    key_name = f"{ident_base}_{s}_KEY" if fi == 0 else f"{ident_base}_{s}_F{fi}_KEY"
                    data, key_val = image_to_rgb565_array(
                        full_path,
                        width=s,
                        height=s,
                        round_radius=icon_radius,
                        # Keep GIF corners transparent regardless of global icon transparency setting.
                        use_transparency=True,
                        bg_fill=None,
                        dither=True,
                        dither_mode="noise",
                        image_obj=frame_img,
                    )
                    ap(f'const uint16_t {key_name} = 0x{key_val:04X};\n')
                    ap(f'const uint16_t {var_name}[] PROGMEM = {{')
                    for i, val in enumerate(data):
                        if i % 16 == 0:
                            ap('\n  ')
                        ap(val + ', ')
                    ap('\n};\n\n')
                    ptrs_for_size.append(var_name)
                    keys_for_size.append(key_name)
                icon_key_name[(base, s)] = keys_for_size[0] if keys_for_size else "ICON_KEY_NONE"
                icon_frame_ptrs[(base, s)] = ptrs_for_size
                icon_frame_key_names[(base, s)] = keys_for_size if keys_for_size else ["ICON_KEY_NONE"]
                icon_frame_interval_ms[(base, s)] = max(40, min(250, int(gif_interval or 100)))
            else:
                use_trans = icon_transparent or is_gif
                data, key = image_to_rgb565_array(
                    full_path,
                    width=s,
                    height=s,
                    round_radius=icon_radius,
                    use_transparency=use_trans,
                    bg_fill=None if use_trans else icon_bg_rgb,
                    dither=True,
                    dither_mode="noise",
                )
                key_name = f"{ident_base}_{s}_KEY"
                icon_key_name[(base, s)] = key_name
                ap(f'const uint16_t {key_name} = 0x{key:04X};\n')
                var_name = f"{ident_base}_{s}"
                ap(f'const uint16_t {var_name}[] PROGMEM = {{')
                for i, val in enumerate(data):
                    if i % 16 == 0:
                        ap('\n  ')
                    ap(val + ', ')
                ap('\n};\n\n')
                icon_frame_ptrs[(base, s)] = [var_name]
                icon_frame_key_names[(base, s)] = [key_name]
                icon_frame_interval_ms[(base, s)] = 120

    icon_anim_max_frames = 1
    for ptrs in icon_frame_ptrs.values():
        if len(ptrs) > icon_anim_max_frames:
            icon_anim_max_frames = len(ptrs)

    # --- pomocná funkcia pre pack buniek ---
    def _profile_rows_cols(profile_dict: Dict[str, Any]) -> tuple[int, int]:
        if profile_dict.get("mode", "grid") == "monitor":
            return 1, 1
        rows_p = max(1, min(4, int(profile_dict.get("rows", 3))))
        cols_p = max(1, min(4, int(profile_dict.get("cols", 4))))
        return rows_p, cols_p

    def _pack_cells(profile_dict: Dict[str, Any], key: str) -> list[str]:
        """Extract a flattened list of button fields for all cells."""
        rows_p, cols_p = _profile_rows_cols(profile_dict)

        out: list[str] = []
        for r in range(rows_p):
            for c in range(cols_p):
                btn = profile_dict.get(f"btn{r}{c}", {})
                val = str(btn.get(key, "") or "")
                val = val.replace("\\", "\\\\").replace('"', '\\"')
                out.append(f'"{val}"')
        if len(out) < BTN_MAX:
            out.extend(['""'] * (BTN_MAX - len(out)))
        return out

    def _pack_color_cells(profile_dict: Dict[str, Any], key: str, default_color: int) -> list[str]:
        rows_p, cols_p = _profile_rows_cols(profile_dict)
        out: list[str] = []
        for r in range(rows_p):
            for c in range(cols_p):
                btn = profile_dict.get(f"btn{r}{c}", {})
                style = btn.get("style", {}) if isinstance(btn, dict) else {}
                raw = style.get(key)
                color = _hex_to_rgb565(str(raw)) if raw else default_color
                out.append(f"0x{color:04X}")
        if len(out) < BTN_MAX:
            out.extend([f"0x{default_color:04X}"] * (BTN_MAX - len(out)))
        return out

    def _pack_text_size_cells(profile_dict: Dict[str, Any], default_size: float) -> list[str]:
        rows_p, cols_p = _profile_rows_cols(profile_dict)
        out: list[str] = []
        for r in range(rows_p):
            for c in range(cols_p):
                btn = profile_dict.get(f"btn{r}{c}", {})
                style = btn.get("style", {}) if isinstance(btn, dict) else {}
                raw = style.get("text_size", default_size)
                try:
                    size = float(raw)
                except Exception:
                    size = float(default_size)
                size = max(0.6, min(2.5, size))
                out.append(f"{size:.2f}f")
        if len(out) < BTN_MAX:
            out.extend([f"{float(default_size):.2f}f"] * (BTN_MAX - len(out)))
        return out

    def _normalize_action_key(raw: Any) -> str:
        text = str(raw or "").strip()
        aliases = {
            "Weather Widget": "WeatherWidget",
            "Metric Widget": "MetricWidget",
        }
        return aliases.get(text, text)

    def _resolve_widget_layout(profile_dict: Dict[str, Any]) -> list[dict[str, int]]:
        rows_p, cols_p = _profile_rows_cols(profile_dict)
        owners: list[list[str | None]] = [[None for _ in range(cols_p)] for _ in range(rows_p)]
        anchors: list[dict[str, int]] = []

        def _sanitize_span(r: int, c: int, btn: Dict[str, Any]) -> tuple[int, int]:
            try:
                span_rows = int(btn.get("span_rows", 1) or 1)
            except Exception:
                span_rows = 1
            try:
                span_cols = int(btn.get("span_cols", 1) or 1)
            except Exception:
                span_cols = 1
            span_rows = max(1, min(2, span_rows, max(1, rows_p - r)))
            span_cols = max(1, min(2, span_cols, max(1, cols_p - c)))
            return span_rows, span_cols

        def _is_free(rr: int, cc: int, sr: int, sc: int) -> bool:
            if rr < 0 or cc < 0 or sr <= 0 or sc <= 0:
                return False
            if rr + sr > rows_p or cc + sc > cols_p:
                return False
            for y in range(rr, rr + sr):
                for x in range(cc, cc + sc):
                    if owners[y][x] is not None:
                        return False
            return True

        for r in range(rows_p):
            for c in range(cols_p):
                if owners[r][c] is not None:
                    continue
                anchor_name = f"btn{r}{c}"
                raw_btn = profile_dict.get(anchor_name, {})
                btn = raw_btn if isinstance(raw_btn, dict) else {}
                span_rows, span_cols = _sanitize_span(r, c, btn)

                candidates: list[tuple[int, int]] = [(span_rows, span_cols)]
                if span_cols > 1:
                    candidates.append((span_rows, 1))
                if span_rows > 1:
                    candidates.append((1, span_cols))
                if (1, 1) not in candidates:
                    candidates.append((1, 1))

                chosen_rows, chosen_cols = 1, 1
                for cand_rows, cand_cols in candidates:
                    if _is_free(r, c, cand_rows, cand_cols):
                        chosen_rows, chosen_cols = cand_rows, cand_cols
                        break

                for rr in range(r, r + chosen_rows):
                    for cc in range(c, c + chosen_cols):
                        owners[rr][cc] = anchor_name

                anchors.append(
                    {
                        "row": r,
                        "col": c,
                        "span_rows": chosen_rows,
                        "span_cols": chosen_cols,
                    }
                )

        return anchors

    def _pack_span_owner_cells(profile_dict: Dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
        rows_p, cols_p = _profile_rows_cols(profile_dict)
        span_rows = [1] * BTN_MAX
        span_cols = [1] * BTN_MAX
        owners = [-1] * BTN_MAX

        anchors = _resolve_widget_layout(profile_dict)
        for entry in anchors:
            r = int(entry.get("row", 0))
            c = int(entry.get("col", 0))
            sr = int(entry.get("span_rows", 1))
            sc = int(entry.get("span_cols", 1))
            anchor_idx = r * cols_p + c
            if 0 <= anchor_idx < BTN_MAX:
                span_rows[anchor_idx] = max(1, min(2, sr))
                span_cols[anchor_idx] = max(1, min(2, sc))
            for rr in range(r, min(rows_p, r + sr)):
                for cc in range(c, min(cols_p, c + sc)):
                    idx = rr * cols_p + cc
                    if 0 <= idx < BTN_MAX:
                        owners[idx] = anchor_idx

        cell_count = rows_p * cols_p
        for idx in range(min(cell_count, BTN_MAX)):
            if owners[idx] < 0:
                owners[idx] = idx

        return (
            [str(v) for v in span_rows],
            [str(v) for v in span_cols],
            [str(v) for v in owners],
        )

    def _pack_widget_kind_cells(profile_dict: Dict[str, Any]) -> tuple[list[str], bool, bool]:
        rows_p, cols_p = _profile_rows_cols(profile_dict)
        kinds = [0] * BTN_MAX
        has_weather = False
        has_metric = False

        anchors = _resolve_widget_layout(profile_dict)
        for entry in anchors:
            r = int(entry.get("row", 0))
            c = int(entry.get("col", 0))
            idx = r * cols_p + c
            if idx < 0 or idx >= BTN_MAX:
                continue
            btn = profile_dict.get(f"btn{r}{c}", {})
            if not isinstance(btn, dict):
                continue
            action = _normalize_action_key(btn.get("action", ""))
            if action == "WeatherWidget":
                kinds[idx] = 1
                has_weather = True
            elif action == "MetricWidget":
                kinds[idx] = 2
                has_metric = True

        return [str(v) for v in kinds], has_weather, has_metric


    # --- BTN_LABELS / ACTIONS / PATHS ---
    ap('const char* BTN_LABELS[PROFILES][BTN_MAX] = {\n')
    for _, prof in all_profiles:
        ap("  { " + ", ".join(_pack_cells(prof, "name")) + " },\n")
    ap("};\n\n")

    ap('const char* BTN_ACTIONS[PROFILES][BTN_MAX] = {\n')
    for _, prof in all_profiles:
        ap("  { " + ", ".join(_pack_cells(prof, "action")) + " },\n")
    ap("};\n\n")

    ap('const char* BTN_PATHS[PROFILES][BTN_MAX] = {\n')
    for _, prof in all_profiles:
        ap("  { " + ", ".join(_pack_cells(prof, "path")) + " },\n")
    ap("};\n\n")

    span_owner_cache = [_pack_span_owner_cells(prof) for _, prof in all_profiles]

    ap('const uint8_t BTN_SPAN_ROWS[PROFILES][BTN_MAX] = {\n')
    for sr, _, __ in span_owner_cache:
        ap("  { " + ", ".join(sr) + " },\n")
    ap("};\n\n")

    ap('const uint8_t BTN_SPAN_COLS[PROFILES][BTN_MAX] = {\n')
    for _, sc, __ in span_owner_cache:
        ap("  { " + ", ".join(sc) + " },\n")
    ap("};\n\n")

    ap('const int16_t BTN_OWNER[PROFILES][BTN_MAX] = {\n')
    for _, __, owner in span_owner_cache:
        ap("  { " + ", ".join(owner) + " },\n")
    ap("};\n\n")

    widget_kind_cache = [_pack_widget_kind_cells(prof) for _, prof in all_profiles]
    ap('const uint8_t BTN_WIDGET_KIND[PROFILES][BTN_MAX] = {\n')
    for kinds, _, __ in widget_kind_cache:
        ap("  { " + ", ".join(kinds) + " },\n")
    ap("};\n\n")

    ap('const bool PROFILE_HAS_WEATHER_WIDGET[PROFILES] = {\n')
    ap("  " + ", ".join("true" if has_w else "false" for _, has_w, __ in widget_kind_cache) + "\n")
    ap("};\n\n")

    ap('const bool PROFILE_HAS_METRIC_WIDGET[PROFILES] = {\n')
    ap("  " + ", ".join("true" if has_m else "false" for _, __, has_m in widget_kind_cache) + "\n")
    ap("};\n\n")

    ap('const uint16_t BTN_BG[PROFILES][BTN_MAX] = {\n')
    for _, prof in all_profiles:
        ap("  { " + ", ".join(_pack_color_cells(prof, "bg_color", btn_bg)) + " },\n")
    ap("};\n\n")

    ap('const uint16_t BTN_FG[PROFILES][BTN_MAX] = {\n')
    for _, prof in all_profiles:
        ap("  { " + ", ".join(_pack_color_cells(prof, "fg_color", btn_fg)) + " },\n")
    ap("};\n\n")

    ap('const float BTN_TEXT_SIZE_PER[PROFILES][BTN_MAX] = {\n')
    for _, prof in all_profiles:
        ap("  { " + ", ".join(_pack_text_size_cells(prof, btn_text_size)) + " },\n")
    ap("};\n\n")

    # --- POINTERS na ikony ---
    ap(f'#define ICON_ANIM_MAX_FRAMES {icon_anim_max_frames}\n\n')

    profile_has_anim_icons: list[bool] = []
    profile_min_anim_intervals: list[int] = []

    ap(f'const uint16_t* PROFILE_ICONS[PROFILES][BTN_MAX] = {{\n')
    for p_idx, (prof_name, prof) in enumerate(all_profiles):
        rows_p, cols_p, size_p = profile_meta[p_idx]
        icon_ptrs = ["nullptr"] * BTN_MAX
        icon_keys = ["ICON_KEY_NONE"] * BTN_MAX
        has_anim = False
        min_anim_interval = 0

        def _idx(r: int, c: int, cols: int) -> int:
            """Index helper for flattening 2D button coords."""
            return r * cols + c

        for r in range(rows_p):
            for c in range(cols_p):
                btn = prof.get(f"btn{r}{c}", {})
                ip = (btn.get("icon") or "").strip()
                if not ip:
                    continue
                base = os.path.splitext(os.path.basename(ip))[0]
                ident = icon_ident_map.get(base)
                frame_ptr_list = icon_frame_ptrs.get((base, size_p), [])
                key_name = icon_key_name.get((base, size_p), "ICON_KEY_NONE")
                if ident and frame_ptr_list:
                    idx = _idx(r, c, cols_p)
                    icon_ptrs[idx] = frame_ptr_list[0]
                    icon_keys[idx] = key_name
                    if len(frame_ptr_list) > 1:
                        has_anim = True
                        iv = max(40, min(250, int(icon_frame_interval_ms.get((base, size_p), 100))))
                        if min_anim_interval == 0 or iv < min_anim_interval:
                            min_anim_interval = iv
        ap("  { " + ", ".join(icon_ptrs) + " },\n")
        profile_has_anim_icons.append(has_anim)
        profile_min_anim_intervals.append(min_anim_interval if has_anim else 0)
    ap("};\n\n")

    ap(f'const uint16_t PROFILE_ICON_KEYS[PROFILES][BTN_MAX] = {{\n')
    for p_idx, (prof_name, prof) in enumerate(all_profiles):
        rows_p, cols_p, size_p = profile_meta[p_idx]
        icon_keys = ["ICON_KEY_NONE"] * BTN_MAX
        for r in range(rows_p):
            for c in range(cols_p):
                btn = prof.get(f"btn{r}{c}", {})
                ip = (btn.get("icon") or "").strip()
                if not ip:
                    continue
                base = os.path.splitext(os.path.basename(ip))[0]
                key_name = icon_key_name.get((base, size_p), "ICON_KEY_NONE")
                idx = r * cols_p + c
                if 0 <= idx < BTN_MAX:
                    icon_keys[idx] = key_name
        ap("  { " + ", ".join(icon_keys) + " },\n")
    ap("};\n\n")

    ap('const uint8_t PROFILE_ICON_FRAME_COUNTS[PROFILES][BTN_MAX] = {\n')
    for p_idx, (_prof_name, prof) in enumerate(all_profiles):
        rows_p, cols_p, size_p = profile_meta[p_idx]
        counts = ["1"] * BTN_MAX
        for r in range(rows_p):
            for c in range(cols_p):
                btn = prof.get(f"btn{r}{c}", {})
                ip = (btn.get("icon") or "").strip()
                if not ip:
                    continue
                base = os.path.splitext(os.path.basename(ip))[0]
                ptrs = icon_frame_ptrs.get((base, size_p), [])
                idx = r * cols_p + c
                if 0 <= idx < BTN_MAX and ptrs:
                    counts[idx] = str(max(1, min(255, len(ptrs))))
        ap("  { " + ", ".join(counts) + " },\n")
    ap("};\n\n")

    ap('const uint16_t PROFILE_ICON_FRAME_INTERVAL[PROFILES][BTN_MAX] = {\n')
    for p_idx, (_prof_name, prof) in enumerate(all_profiles):
        rows_p, cols_p, size_p = profile_meta[p_idx]
        intervals = ["120"] * BTN_MAX
        for r in range(rows_p):
            for c in range(cols_p):
                btn = prof.get(f"btn{r}{c}", {})
                ip = (btn.get("icon") or "").strip()
                if not ip:
                    continue
                base = os.path.splitext(os.path.basename(ip))[0]
                ptrs = icon_frame_ptrs.get((base, size_p), [])
                iv = max(40, min(250, int(icon_frame_interval_ms.get((base, size_p), 120))))
                idx = r * cols_p + c
                if 0 <= idx < BTN_MAX and ptrs:
                    intervals[idx] = str(iv)
        ap("  { " + ", ".join(intervals) + " },\n")
    ap("};\n\n")

    ap('const uint16_t* PROFILE_ICON_FRAMES[PROFILES][BTN_MAX][ICON_ANIM_MAX_FRAMES] = {\n')
    for p_idx, (_prof_name, prof) in enumerate(all_profiles):
        rows_p, cols_p, size_p = profile_meta[p_idx]
        ap("  {\n")
        frame_rows: list[list[str]] = [["nullptr"] * icon_anim_max_frames for _ in range(BTN_MAX)]
        for r in range(rows_p):
            for c in range(cols_p):
                btn = prof.get(f"btn{r}{c}", {})
                ip = (btn.get("icon") or "").strip()
                if not ip:
                    continue
                base = os.path.splitext(os.path.basename(ip))[0]
                ptrs = icon_frame_ptrs.get((base, size_p), [])
                idx = r * cols_p + c
                if idx < 0 or idx >= BTN_MAX or not ptrs:
                    continue
                for fi, ptr_name in enumerate(ptrs[:icon_anim_max_frames]):
                    frame_rows[idx][fi] = ptr_name
        for idx in range(BTN_MAX):
            ap("    { " + ", ".join(frame_rows[idx]) + " },\n")
        ap("  },\n")
    ap("};\n\n")

    ap('const uint16_t PROFILE_ICON_FRAME_KEYS[PROFILES][BTN_MAX][ICON_ANIM_MAX_FRAMES] = {\n')
    for p_idx, (_prof_name, prof) in enumerate(all_profiles):
        rows_p, cols_p, size_p = profile_meta[p_idx]
        ap("  {\n")
        key_rows: list[list[str]] = [["ICON_KEY_NONE"] * icon_anim_max_frames for _ in range(BTN_MAX)]
        for r in range(rows_p):
            for c in range(cols_p):
                btn = prof.get(f"btn{r}{c}", {})
                ip = (btn.get("icon") or "").strip()
                if not ip:
                    continue
                base = os.path.splitext(os.path.basename(ip))[0]
                keys = icon_frame_key_names.get((base, size_p), [])
                idx = r * cols_p + c
                if idx < 0 or idx >= BTN_MAX or not keys:
                    continue
                for fi, key_name in enumerate(keys[:icon_anim_max_frames]):
                    key_rows[idx][fi] = key_name
        for idx in range(BTN_MAX):
            ap("    { " + ", ".join(key_rows[idx]) + " },\n")
        ap("  },\n")
    ap("};\n\n")

    ap('const bool PROFILE_HAS_ANIM_ICONS[PROFILES] = {\n')
    ap("  " + ", ".join("true" if v else "false" for v in profile_has_anim_icons) + "\n")
    ap("};\n\n")

    ap('const uint16_t PROFILE_ICON_MIN_INTERVAL[PROFILES] = {\n')
    ap("  " + ", ".join(str(max(0, int(v))) for v in profile_min_anim_intervals) + "\n")
    ap("};\n\n")

    def _emit_bg_image(prefix: str, path: str, fill_rgb: tuple[int, int, int]) -> None:
        if path and os.path.isfile(path):
            data, _key = image_to_rgb565_array(
                path,
                width=max(1, SCREEN_W),
                height=max(1, SCREEN_H),
                round_radius=0,
                use_transparency=False,
                bg_fill=fill_rgb,
                dither=True,
                dither_mode="noise",
            )
            if data:
                ap(f'const uint16_t {prefix}_DATA[] PROGMEM = {{')
                for i, val in enumerate(data):
                    if i % 16 == 0:
                        ap('\n  ')
                    ap(val + ', ')
                ap('\n};\n')
                ap(f'const uint16_t* {prefix} = {prefix}_DATA;\n')
                ap(f'const bool {prefix}_HAS_IMG = true;\n\n')
                return
        ap(f'const uint16_t* {prefix} = nullptr;\n')
        ap(f'const bool {prefix}_HAS_IMG = false;\n\n')

    _emit_bg_image("GRID_BG", grid_bg_image, grid_bg_rgb)
    _emit_bg_image("SCR_BG", scr_bg_image, scr_bg_rgb)

    # --- C++ funkcie ---
    ap(r'''
void drawBgImage(lgfx::LovyanGFX* gfx, const uint16_t* img, int xOffset){
  if (!img) return;
  gfx->pushImage(xOffset, 0, SCREEN_WIDTH, SCREEN_HEIGHT, img);
}

void drawGridBackground(lgfx::LovyanGFX* gfx, int xOffset){
  if (GRID_BG_HAS_IMG && GRID_BG) {
    drawBgImage(gfx, GRID_BG, xOffset);
  } else {
    gfx->fillRect(xOffset, 0, SCREEN_WIDTH, SCREEN_HEIGHT, COLOR_GRID_BG);
  }
}

void drawButtonAtEx(
  lgfx::LovyanGFX* gfx,
  int x,
  int y,
  int w,
  int h,
  const char* label,
  const uint16_t* icon,
  uint16_t iconSize,
  float textSize,
  uint16_t bg,
  uint16_t fg,
  uint16_t iconKey,
  float iconScale = 1.0f,
  int contentOffsetY = 0
){
  if (!GRID_BG_HAS_IMG) {
    gfx->fillRect(x, y, w, h, COLOR_GRID_BG);
  }
  if (iconScale < 0.65f) iconScale = 0.65f;
  if (iconScale > 1.35f) iconScale = 1.35f;
  bool hasLabel = (label && strlen(label) > 0);
  bool iconOnly = (icon && !hasLabel);
  bool drawPlate = !(ICONS_TRANSPARENT && iconOnly);
  int inset = iconOnly ? 5 : BTN_INSET;
  int innerW = max(1, w - inset * 2);
  int innerH = max(1, h - inset * 2 - contentOffsetY);
  int innerX = x + inset;
  int innerY = y + inset + contentOffsetY;
  int radius = min(BTN_RADIUS, min(innerW, innerH) / 3);
  if (drawPlate) {
    gfx->fillRoundRect(innerX, innerY, innerW, innerH, radius, bg);
  }

  gfx->setTextColor(fg, bg);
  setButtonFontForSize(gfx, textSize);
  gfx->setTextDatum(textdatum_t::middle_center);
  gfx->setTextPadding(0);
  int textY = innerY + innerH / 2;

  if (icon && iconSize > 0) {
    int gap = 6;
    int drawSize = iconSize;
    gfx->setClipRect(innerX, innerY, innerW, innerH);
    if (hasLabel) {
      int freeAboveText = max(1, (textY - innerY) - gap);
      int target = min(innerW, freeAboveText);
      target = (int)((float)target * iconScale);
      if (target < 1) target = 1;
      int iconY = innerY + (freeAboveText - target) / 2;
      int iconX = innerX + (innerW - target) / 2;
      float zoom = (float)target / (float)max(1, drawSize);
      if (fabsf(zoom - 1.0f) > 0.01f) {
        float cx = iconX + target * 0.5f;
        float cy = iconY + target * 0.5f;
        if (iconKey == ICON_KEY_NONE) {
          gfx->pushImageRotateZoom(cx, cy, drawSize * 0.5f, drawSize * 0.5f, 0.0f, zoom, zoom, drawSize, drawSize, icon);
        } else {
          gfx->pushImageRotateZoom(cx, cy, drawSize * 0.5f, drawSize * 0.5f, 0.0f, zoom, zoom, drawSize, drawSize, icon, iconKey);
        }
      } else {
        if (iconKey == ICON_KEY_NONE) {
          gfx->pushImage(iconX, iconY, drawSize, drawSize, icon);
        } else {
          gfx->pushImage(iconX, iconY, drawSize, drawSize, icon, iconKey);
        }
      }
    } else {
      float zoomX = ((float)innerW / (float)max(1, drawSize)) * iconScale;
      float zoomY = ((float)innerH / (float)max(1, drawSize)) * iconScale;
      float cx = innerX + innerW * 0.5f;
      float cy = innerY + innerH * 0.5f;
      if (fabsf(zoomX - 1.0f) > 0.01f || fabsf(zoomY - 1.0f) > 0.01f) {
        if (iconKey == ICON_KEY_NONE) {
          gfx->pushImageRotateZoom(cx, cy, drawSize * 0.5f, drawSize * 0.5f, 0.0f, zoomX, zoomY, drawSize, drawSize, icon);
        } else {
          gfx->pushImageRotateZoom(cx, cy, drawSize * 0.5f, drawSize * 0.5f, 0.0f, zoomX, zoomY, drawSize, drawSize, icon, iconKey);
        }
      } else {
        int iconX = innerX + (innerW - drawSize) / 2;
        int iconYc = innerY + (innerH - drawSize) / 2;
        if (iconKey == ICON_KEY_NONE) {
          gfx->pushImage(iconX, iconYc, drawSize, drawSize, icon);
        } else {
          gfx->pushImage(iconX, iconYc, drawSize, drawSize, icon, iconKey);
        }
      }
    }
    gfx->clearClipRect();
  }

  if (label && strlen(label) > 0){
    gfx->drawString(label, innerX + innerW/2, textY);
  }
}

void drawButtonAt(lgfx::LovyanGFX* gfx, int x, int y, int w, int h, const char* label, const uint16_t* icon, uint16_t iconSize, float textSize){
  drawButtonAtEx(gfx, x, y, w, h, label, icon, iconSize, textSize, COLOR_BTN_BG, COLOR_BTN_FG, ICON_KEY_NONE);
}

bool profileHasWeatherWidget(int profileIdx) {
  if (profileIdx < 0 || profileIdx >= PROFILES) return false;
  return PROFILE_HAS_WEATHER_WIDGET[profileIdx];
}

bool profileHasMetricWidget(int profileIdx) {
  if (profileIdx < 0 || profileIdx >= PROFILES) return false;
  return PROFILE_HAS_METRIC_WIDGET[profileIdx];
}

uint8_t metricKeyFromPath(const char* path) {
  char key[24];
  size_t j = 0;
  key[0] = '\0';

  if (path && *path) {
    const char* src = path;
    if (*src == '{') {
      const char* k = strstr(path, "\"key\"");
      if (!k) k = strstr(path, "\"metric\"");
      if (k) {
        const char* colon = strchr(k, ':');
        if (colon) {
          src = colon + 1;
          while (*src == ' ' || *src == '"' || *src == '\'') src++;
        }
      }
    }

    while (*src && *src != '|' && j + 1 < sizeof(key)) {
      char ch = *src++;
      if (ch >= 'a' && ch <= 'z') ch = (char)(ch - 'a' + 'A');
      if ((ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch == '_') {
        key[j++] = ch;
      } else if (ch == '-' || ch == ' ' || ch == '.') {
        key[j++] = '_';
      } else if (ch == '"' || ch == '\'') {
        break;
      }
    }
  }

  key[j] = '\0';
  if (j == 0) {
    strncpy(key, "CPU", sizeof(key) - 1);
    key[sizeof(key) - 1] = '\0';
  }

  if (strcmp(key, "CPU") == 0 || strcmp(key, "CPU_PERCENT") == 0) return 0;
  if (strcmp(key, "RAM") == 0 || strcmp(key, "RAM_PERCENT") == 0) return 1;
  if (strcmp(key, "GPU") == 0 || strcmp(key, "GPU_PERCENT") == 0) return 2;
  if (strcmp(key, "GPU_TEMP") == 0 || strcmp(key, "GPUTEMP") == 0 || strcmp(key, "TEMP") == 0 || strcmp(key, "GPU_TEMPERATURE") == 0) return 3;
  if (strcmp(key, "FPS") == 0) return 4;
  if (strcmp(key, "NET") == 0 || strcmp(key, "NETWORK") == 0 || strcmp(key, "NET_MB_S") == 0) return 5;
  if (strcmp(key, "DISK") == 0 || strcmp(key, "DISK_MB_S") == 0 || strcmp(key, "IO") == 0) return 6;
  if (strcmp(key, "CPU_GHZ") == 0 || strcmp(key, "CPUGHZ") == 0) return 7;
  return 0;
}

float metricValueByKey(uint8_t key) {
  switch (key) {
    case 0: return g_metric.cpu;
    case 1: return g_metric.ram;
    case 2: return g_metric.gpu;
    case 3: return g_metric.gpuTemp;
    case 4: return g_metric.fps;
    case 5: return g_metric.net;
    case 6: return g_metric.disk;
    case 7: return g_metric.cpuGhz;
    default: return -1.0f;
  }
}

const char* metricLabelByKey(uint8_t key) {
  switch (key) {
    case 0: return "CPU load";
    case 1: return "RAM usage";
    case 2: return "GPU load";
    case 3: return "GPU temp";
    case 4: return "FPS";
    case 5: return "Network";
    case 6: return "Disk I/O";
    case 7: return "CPU freq";
    default: return "Metric";
  }
}

const char* metricBadgeByKey(uint8_t key) {
  switch (key) {
    case 0: return "CPU";
    case 1: return "RAM";
    case 2: return "GPU";
    case 3: return "TMP";
    case 4: return "FPS";
    case 5: return "NET";
    case 6: return "IO";
    case 7: return "GHz";
    default: return "M";
  }
}

const char* metricMetaByKey(uint8_t key) {
  switch (key) {
    case 0:
    case 1:
    case 2: return "Usage";
    case 3: return "Thermal";
    case 4: return "Frames/s";
    case 5: return "Throughput";
    case 6: return "Read+Write";
    case 7: return "Frequency";
    default: return "Metric";
  }
}

uint16_t metricTopColor(uint8_t key) {
  switch (key) {
    case 0: return tft.color565(31, 41, 55);
    case 1: return tft.color565(39, 52, 73);
    case 2: return tft.color565(31, 58, 44);
    case 3: return tft.color565(58, 31, 42);
    case 4: return tft.color565(58, 45, 23);
    case 5: return tft.color565(30, 58, 74);
    case 6: return tft.color565(41, 41, 58);
    case 7: return tft.color565(34, 48, 42);
    default: return tft.color565(31, 41, 55);
  }
}

uint16_t metricBottomColor(uint8_t key) {
  switch (key) {
    case 0: return tft.color565(51, 65, 85);
    case 1: return tft.color565(52, 74, 102);
    case 2: return tft.color565(43, 90, 67);
    case 3: return tft.color565(90, 48, 64);
    case 4: return tft.color565(91, 67, 29);
    case 5: return tft.color565(46, 91, 113);
    case 6: return tft.color565(61, 61, 89);
    case 7: return tft.color565(47, 68, 58);
    default: return tft.color565(51, 65, 85);
  }
}

uint16_t metricAccentColor(uint8_t key) {
  switch (key) {
    case 0: return tft.color565(96, 165, 250);
    case 1: return tft.color565(34, 211, 238);
    case 2: return tft.color565(52, 211, 153);
    case 3: return tft.color565(251, 113, 133);
    case 4: return tft.color565(251, 191, 36);
    case 5: return tft.color565(56, 189, 248);
    case 6: return tft.color565(167, 139, 250);
    case 7: return tft.color565(74, 222, 128);
    default: return tft.color565(148, 163, 184);
  }
}

void formatMetricValue(uint8_t key, float value, char* out, size_t outSize) {
  if (!out || outSize == 0) return;
  if (!(value >= 0.0f)) {
    snprintf(out, outSize, "--");
    return;
  }
  switch (key) {
    case 0:
    case 1:
    case 2:
      snprintf(out, outSize, "%.0f%%", value);
      return;
    case 3:
      snprintf(out, outSize, "%.0fC", value);
      return;
    case 4:
      snprintf(out, outSize, "%.0f", value);
      return;
    case 5:
    case 6:
      if (value < 1.0f) {
        snprintf(out, outSize, "%.0fKB/s", value * 1024.0f);
      } else {
        snprintf(out, outSize, "%.1fMB/s", value);
      }
      return;
    case 7:
      snprintf(out, outSize, "%.2fGHz", value);
      return;
    default:
      snprintf(out, outSize, "%.1f", value);
      return;
  }
}

static constexpr uint8_t WIDGET_MODE_MICRO = 0;
static constexpr uint8_t WIDGET_MODE_NARROW = 1;
static constexpr uint8_t WIDGET_MODE_ROW = 2;
static constexpr uint8_t WIDGET_MODE_COMPACT = 3;
static constexpr uint8_t WIDGET_MODE_FULL = 4;

uint8_t metricLayoutModeForSize(int w, int h) {
  int area = max(1, w) * max(1, h);
  bool vertical = h >= (int)(w * 1.20f);
  bool shortH = h < 94;
  bool tinySquare = (w <= 152 && h <= 152 && area <= 22000);

  if (w <= 118 || h <= 62 || area <= 15000 || tinySquare) return WIDGET_MODE_MICRO;
  if (vertical && w < 156) return WIDGET_MODE_NARROW;
  if (shortH) return WIDGET_MODE_ROW;
  if (w < 186 || area < 19000) return WIDGET_MODE_COMPACT;
  return WIDGET_MODE_FULL;
}

uint8_t weatherLayoutModeForSize(int w, int h) {
  int area = max(1, w) * max(1, h);
  bool vertical = h >= (int)(w * 1.22f);
  bool shortH = h < 96;
  bool tinySquare = (w <= 152 && h <= 152 && area <= 22000);

  if (w <= 118 || h <= 62 || area <= 15000 || tinySquare) return WIDGET_MODE_MICRO;
  if (vertical && w < 156) return WIDGET_MODE_NARROW;
  if (shortH) return WIDGET_MODE_ROW;
  if (w < 190 || area < 19000) return WIDGET_MODE_COMPACT;
  return WIDGET_MODE_FULL;
}

void clipText(const char* src, char* dst, size_t dstSize, size_t maxChars) {
  if (!dst || dstSize == 0) return;
  const char* srcPtr = src;
  char localCopy[96];
  if (srcPtr == dst) {
    size_t n = 0;
    while (srcPtr[n] && n + 1 < sizeof(localCopy)) {
      n++;
    }
    memcpy(localCopy, srcPtr, n);
    localCopy[n] = '\0';
    srcPtr = localCopy;
  }
  dst[0] = '\0';
  if (!srcPtr || !*srcPtr) return;

  size_t limit = maxChars;
  if (limit + 1 > dstSize) limit = dstSize - 1;
  size_t i = 0;
  for (; i < limit && srcPtr[i]; ++i) {
    dst[i] = srcPtr[i];
  }
  dst[i] = '\0';
  if (srcPtr[i] && i >= 4) {
    dst[i - 3] = '.';
    dst[i - 2] = '.';
    dst[i - 1] = '.';
  }
}

void drawMetricWidgetAt(lgfx::LovyanGFX* gfx, int x, int y, int w, int h, bool pressed, const char* metricPath) {
  if (!GRID_BG_HAS_IMG) {
    gfx->fillRect(x, y, w, h, COLOR_GRID_BG);
  }
  int inset = max(6, BTN_INSET - 3);
  if (pressed) inset += 2;
  int innerX = x + inset;
  int innerY = y + inset + (pressed ? 1 : 0);
  int innerW = max(1, w - inset * 2);
  int innerH = max(1, h - inset * 2 - (pressed ? 1 : 0));
  int radius = min(BTN_RADIUS, min(innerW, innerH) / 3);

  uint8_t key = metricKeyFromPath(metricPath);
  float value = metricValueByKey(key);
  uint16_t top = metricTopColor(key);
  uint16_t bot = metricBottomColor(key);
  uint16_t accent = metricAccentColor(key);
  uint8_t mode = metricLayoutModeForSize(innerW, innerH);

  gfx->fillRoundRect(innerX, innerY, innerW, innerH, radius, top);
  if (innerW > 8 && innerH > 8) {
    gfx->fillGradientRect(innerX + 2, innerY + 2, innerW - 4, innerH - 4, top, bot, lgfx::v1::VLINEAR);
  }
  gfx->drawRoundRect(innerX, innerY, innerW, innerH, radius, accent);

  gfx->setClipRect(innerX + 2, innerY + 2, max(1, innerW - 4), max(1, innerH - 4));

  int textLeft = innerX + 10;
  if (mode == WIDGET_MODE_ROW && innerW >= 136) {
    int badgeS = max(18, min(28, innerH - 16));
    int badgeX = innerX + 8;
    int badgeY = innerY + (innerH - badgeS) / 2;
    gfx->fillRoundRect(badgeX, badgeY, badgeS, badgeS, 7, accent);
    gfx->setTextDatum(textdatum_t::middle_center);
    gfx->setTextColor(tft.color565(15, 23, 42), accent);
    setMediaFontMeta(gfx);
    gfx->drawString(metricBadgeByKey(key), badgeX + badgeS / 2, badgeY + badgeS / 2);
    textLeft = badgeX + badgeS + 8;
  } else if ((mode == WIDGET_MODE_COMPACT || mode == WIDGET_MODE_FULL) && innerW >= 124) {
    int badgeW = max(26, min(44, innerW / 4));
    int badgeH = max(18, min(24, innerH / 3));
    int badgeX = innerX + 10;
    int badgeY = innerY + 10;
    gfx->fillRoundRect(badgeX, badgeY, badgeW, badgeH, 8, accent);
    gfx->setTextDatum(textdatum_t::middle_center);
    gfx->setTextColor(tft.color565(15, 23, 42), accent);
    setMediaFontMeta(gfx);
    gfx->drawString(metricBadgeByKey(key), badgeX + badgeW / 2, badgeY + badgeH / 2);
    textLeft = badgeX + badgeW + 8;
  }

  int textW = max(8, innerW - (textLeft - innerX) - 10);
  int tx = textLeft + textW / 2;

  char valueBuf[20];
  char labelBuf[24];
  char metaBuf[20];
  formatMetricValue(key, value, valueBuf, sizeof(valueBuf));
  clipText(metricLabelByKey(key), labelBuf, sizeof(labelBuf), (mode == WIDGET_MODE_MICRO) ? 9 : 18);
  clipText(metricMetaByKey(key), metaBuf, sizeof(metaBuf), (mode == WIDGET_MODE_ROW) ? 11 : 16);

  gfx->setTextDatum(textdatum_t::middle_center);
  gfx->setTextPadding(0);
  if (mode == WIDGET_MODE_MICRO) {
    int cx = innerX + innerW / 2;
    gfx->setTextColor(tft.color565(248, 250, 252), top);
    setMediaFontTitle(gfx);
    gfx->drawString(valueBuf, cx, innerY + innerH / 2 - 4);
    gfx->setTextColor(tft.color565(203, 213, 225), top);
    setMediaFontMeta(gfx);
    gfx->drawString(metricBadgeByKey(key), cx, innerY + innerH - 11);
  } else if (mode == WIDGET_MODE_NARROW) {
    int cx = innerX + innerW / 2;
    gfx->setTextColor(tft.color565(241, 245, 249), top);
    setMediaFontMeta(gfx);
    gfx->drawString(labelBuf, cx, innerY + 12);
    gfx->setTextColor(tft.color565(248, 250, 252), top);
    setMediaFontTitle(gfx);
    gfx->drawString(valueBuf, cx, innerY + innerH / 2);
    gfx->setTextColor(tft.color565(203, 213, 225), top);
    setMediaFontMeta(gfx);
    gfx->drawString(metaBuf, cx, innerY + innerH - 12);
  } else if (mode == WIDGET_MODE_ROW) {
    bool rowTight = innerH < 90;
    int labelY = innerY + innerH / 2 - (rowTight ? 9 : 10);
    int valueY = innerY + innerH / 2 + (rowTight ? 9 : 9);
    size_t rowChars = (size_t)max(8, min(14, textW / 8));
    clipText(labelBuf, labelBuf, sizeof(labelBuf), rowChars);
    if (rowTight && key == 3) {
      clipText("GPU TMP", labelBuf, sizeof(labelBuf), rowChars);
    }
    gfx->setTextColor(tft.color565(241, 245, 249), top);
    if (rowTight) {
      setMediaFontMeta(gfx);
    } else {
      setMediaFontBody(gfx);
    }
    gfx->drawString(labelBuf, tx, labelY);
    gfx->setTextColor(tft.color565(248, 250, 252), top);
    if (rowTight || key == 3) {
      setMediaFontBody(gfx);
    } else {
      setMediaFontTitle(gfx);
    }
    gfx->drawString(valueBuf, tx, valueY);
  } else {
    gfx->setTextColor(tft.color565(241, 245, 249), top);
    setMediaFontBody(gfx);
    gfx->drawString(labelBuf, tx, innerY + ((mode == WIDGET_MODE_COMPACT) ? 13 : 14));

    gfx->setTextColor(tft.color565(248, 250, 252), top);
    setMediaFontTitle(gfx);
    gfx->drawString(valueBuf, tx, innerY + innerH / 2);

    gfx->setTextColor(tft.color565(203, 213, 225), top);
    setMediaFontMeta(gfx);
    gfx->drawString(metaBuf, tx, innerY + innerH - ((mode == WIDGET_MODE_COMPACT) ? 12 : 14));
  }
  gfx->clearClipRect();
}

int weatherCategoryFromCode(int code) {
  if (code == 0) return 0;                    // clear
  if (code == 1 || code == 2 || code == 3) return 1;   // cloudy
  if (code == 45 || code == 48) return 5;     // fog
  if ((code >= 51 && code <= 67) || (code >= 80 && code <= 82)) return 2; // rain
  if ((code >= 71 && code <= 77) || code == 85 || code == 86) return 3;   // snow
  if (code >= 95) return 4;                   // storm
  return 1;
}

const char* weatherDescForCode(int code) {
  switch (code) {
    case 0: return "Clear";
    case 1: return "Mostly clear";
    case 2: return "Partly cloudy";
    case 3: return "Cloudy";
    case 45: return "Fog";
    case 48: return "Rime fog";
    case 51: return "Light drizzle";
    case 53: return "Drizzle";
    case 55: return "Heavy drizzle";
    case 61: return "Light rain";
    case 63: return "Rain";
    case 65: return "Heavy rain";
    case 71: return "Light snow";
    case 73: return "Snow";
    case 75: return "Heavy snow";
    case 80: return "Rain showers";
    case 81: return "Heavy showers";
    case 82: return "Violent showers";
    case 95: return "Thunderstorm";
    case 96: return "Thunder + hail";
    case 99: return "Severe storm";
    default: return "Weather";
  }
}

uint16_t weatherBgColorTop(int category) {
  switch (category) {
    case 0: return tft.color565(14, 116, 223);
    case 1: return tft.color565(55, 84, 120);
    case 2: return tft.color565(17, 55, 96);
    case 3: return tft.color565(48, 86, 124);
    case 4: return tft.color565(66, 33, 99);
    case 5: return tft.color565(50, 62, 84);
    default: return tft.color565(34, 66, 104);
  }
}

uint16_t weatherBgColorBottom(int category) {
  switch (category) {
    case 0: return tft.color565(37, 99, 235);
    case 1: return tft.color565(71, 85, 105);
    case 2: return tft.color565(30, 64, 114);
    case 3: return tft.color565(59, 130, 246);
    case 4: return tft.color565(88, 28, 135);
    case 5: return tft.color565(71, 85, 105);
    default: return tft.color565(30, 58, 95);
  }
}

void drawWeatherIcon(lgfx::LovyanGFX* gfx, int cx, int cy, int size, int category, uint8_t phase, uint16_t colorFg) {
  int s = max(18, size);
  int r = max(6, s / 7);
  uint16_t cloudCol = tft.color565(236, 244, 255);
  uint16_t accent = tft.color565(125, 211, 252);

  auto drawCloud = [&](int ox, int oy, uint16_t col) {
    int cw = max(22, s - 10);
    int ch = max(14, s / 2);
    int x = cx - cw / 2 + ox;
    int y = cy - ch / 2 + oy;
    gfx->fillRoundRect(x, y + ch / 3, cw, (ch * 2) / 3, max(4, ch / 3), col);
    gfx->fillCircle(x + cw / 4, y + ch / 2, max(4, ch / 3), col);
    gfx->fillCircle(x + cw / 2, y + ch / 3, max(5, ch / 2), col);
    gfx->fillCircle(x + (cw * 3) / 4, y + ch / 2, max(4, ch / 3), col);
  };

  if (category == 0) {
    int ray = max(10, s / 2 + (int)phase);
    gfx->fillCircle(cx, cy, r + 3, tft.color565(253, 224, 71));
    const int8_t vx[8] = {1, 1, 0, -1, -1, -1, 0, 1};
    const int8_t vy[8] = {0, 1, 1, 1, 0, -1, -1, -1};
    for (int i = 0; i < 8; i++) {
      int x0 = cx + vx[i] * (r + 5);
      int y0 = cy + vy[i] * (r + 5);
      int x1 = cx + vx[i] * ray;
      int y1 = cy + vy[i] * ray;
      gfx->drawLine(x0, y0, x1, y1, tft.color565(254, 240, 138));
    }
    return;
  }

  if (category == 1 || category == 5) {
    drawCloud((int)phase - 1, 0, cloudCol);
    if (category == 5) {
      gfx->drawFastHLine(cx - s / 3, cy + s / 4, (s * 2) / 3, tft.color565(186, 230, 253));
    }
    return;
  }

  if (category == 2) {
    drawCloud(0, -2, cloudCol);
    int dropY = cy + s / 3 + (int)(phase % 3);
    for (int i = -1; i <= 1; i++) {
      int dx = i * (s / 5);
      gfx->drawLine(cx + dx, dropY, cx + dx - 2, dropY + 8, accent);
      gfx->drawLine(cx + dx + 1, dropY, cx + dx - 1, dropY + 8, accent);
    }
    return;
  }

  if (category == 3) {
    drawCloud(0, -2, cloudCol);
    int flakeY = cy + s / 3 + (int)(phase % 2);
    for (int i = -1; i <= 1; i++) {
      int fx = cx + i * (s / 5);
      gfx->drawLine(fx - 3, flakeY, fx + 3, flakeY, colorFg);
      gfx->drawLine(fx, flakeY - 3, fx, flakeY + 3, colorFg);
    }
    return;
  }

  drawCloud(0, -3, cloudCol);
  int boltX = cx + (int)(phase % 2) - 2;
  int boltY = cy + s / 6;
  uint16_t bolt = tft.color565(253, 224, 71);
  gfx->fillTriangle(boltX, boltY, boltX + 8, boltY, boltX + 2, boltY + 12, bolt);
  gfx->fillTriangle(boltX + 2, boltY + 10, boltX + 10, boltY + 10, boltX + 4, boltY + 20, bolt);
}

void drawWeatherWidgetAt(lgfx::LovyanGFX* gfx, int x, int y, int w, int h, bool pressed) {
  if (!GRID_BG_HAS_IMG) {
    gfx->fillRect(x, y, w, h, COLOR_GRID_BG);
  }
  int inset = max(6, BTN_INSET - 3);
  if (pressed) inset += 2;
  int innerX = x + inset;
  int innerY = y + inset + (pressed ? 1 : 0);
  int innerW = max(1, w - inset * 2);
  int innerH = max(1, h - inset * 2 - (pressed ? 1 : 0));
  int radius = min(BTN_RADIUS, min(innerW, innerH) / 3);

  int cat = weatherCategoryFromCode(g_weather.code);
  uint16_t top = weatherBgColorTop(cat);
  uint16_t bot = weatherBgColorBottom(cat);
  uint8_t mode = weatherLayoutModeForSize(innerW, innerH);

  gfx->fillRoundRect(innerX, innerY, innerW, innerH, radius, top);
  if (innerW > 8 && innerH > 8) {
    gfx->fillGradientRect(innerX + 2, innerY + 2, innerW - 4, innerH - 4, top, bot, lgfx::v1::VLINEAR);
  }
  gfx->drawRoundRect(innerX, innerY, innerW, innerH, radius, tft.color565(219, 234, 254));

  char tempBuf[16];
  if (g_weather.valid) {
    snprintf(tempBuf, sizeof(tempBuf), "%.0fC", g_weather.tempC);
  } else {
    snprintf(tempBuf, sizeof(tempBuf), "--");
  }

  const char* rawDesc = g_weather.desc[0] ? g_weather.desc : weatherDescForCode(g_weather.code);
  char descBuf[40];
  clipText(rawDesc, descBuf, sizeof(descBuf), 22);

  char labelBuf[28];
  if (g_weather.label[0]) {
    clipText(g_weather.label, labelBuf, sizeof(labelBuf), 20);
  } else {
    strncpy(labelBuf, "Weather", sizeof(labelBuf) - 1);
    labelBuf[sizeof(labelBuf) - 1] = '\0';
  }

  char metaBuf[48];
  if (g_weather.valid) {
    if (g_weather.humidity >= 0) {
      snprintf(metaBuf, sizeof(metaBuf), "Feels %.0fC H %d%% W %.1f", g_weather.feelsC, g_weather.humidity, g_weather.wind);
    } else {
      snprintf(metaBuf, sizeof(metaBuf), "Feels %.0fC W %.1f", g_weather.feelsC, g_weather.wind);
    }
  } else {
    snprintf(metaBuf, sizeof(metaBuf), "Waiting for update");
  }

  gfx->setClipRect(innerX + 2, innerY + 2, max(1, innerW - 4), max(1, innerH - 4));
  gfx->setTextDatum(textdatum_t::middle_center);
  gfx->setTextPadding(0);

  if (mode == WIDGET_MODE_MICRO) {
    char microDesc[24];
    if (g_weather.humidity >= 0) {
      snprintf(microDesc, sizeof(microDesc), "%s %d%%", descBuf, g_weather.humidity);
    } else {
      snprintf(microDesc, sizeof(microDesc), "%s", descBuf);
    }
    clipText(microDesc, microDesc, sizeof(microDesc), 12);

    int tx = innerX + innerW / 2;
    gfx->setTextColor(tft.color565(248, 250, 252), top);
    setMediaFontTitle(gfx);
    gfx->drawString(tempBuf, tx, innerY + innerH / 2 - 4);
    gfx->setTextColor(tft.color565(226, 232, 240), top);
    setMediaFontMeta(gfx);
    gfx->drawString(microDesc, tx, innerY + innerH - 11);
    gfx->clearClipRect();
    return;
  }

  if (mode == WIDGET_MODE_NARROW) {
    int tx = innerX + innerW / 2;
    if (innerW >= 108 && innerH >= 128) {
      int iconSize = min(34, max(20, innerW - 66));
      int iconCx = tx;
      int iconCy = innerY + iconSize / 2 + 8;
      drawWeatherIcon(gfx, iconCx, iconCy, iconSize, cat, g_weather.animPhase, tft.color565(241, 245, 249));
    }

    char line1[36];
    snprintf(line1, sizeof(line1), "%s %s", tempBuf, descBuf);
    clipText(line1, line1, sizeof(line1), 17);
    clipText(metaBuf, metaBuf, sizeof(metaBuf), 18);

    gfx->setTextColor(tft.color565(239, 246, 255), top);
    setMediaFontMeta(gfx);
    gfx->drawString(labelBuf, tx, innerY + 11);
    gfx->setTextColor(tft.color565(248, 250, 252), top);
    setMediaFontBody(gfx);
    gfx->drawString(line1, tx, innerY + innerH / 2 - 10);
    gfx->setTextColor(tft.color565(191, 219, 254), top);
    setMediaFontMeta(gfx);
    gfx->drawString(metaBuf, tx, innerY + innerH - 12);
    gfx->clearClipRect();
    return;
  }

  int iconAreaW = 0;
  if (mode == WIDGET_MODE_ROW) {
    if (innerW >= 148) {
      iconAreaW = max(28, min(42, innerH - 16));
    }
  } else {
    iconAreaW = max(56, (innerW * 38) / 100);
    if (iconAreaW > innerW - 60) iconAreaW = max(40, innerW - 60);
  }

  if (iconAreaW > 0) {
    int iconCx = innerX + iconAreaW / 2;
    int iconCy = innerY + innerH / 2 - 2;
    int iconSize = min(max(22, iconAreaW - 12), max(22, innerH - 16));
    drawWeatherIcon(gfx, iconCx, iconCy, iconSize, cat, g_weather.animPhase, tft.color565(241, 245, 249));
  }

  int textLeft = innerX + ((iconAreaW > 0) ? (iconAreaW + 8) : 10);
  int textW = max(8, innerW - (textLeft - innerX) - 10);
  int charSlots = max(8, min(28, textW / 7));
  int tx = textLeft + textW / 2;

  if (mode == WIDGET_MODE_ROW) {
    char line1[40];
    char line2[40];
    snprintf(line1, sizeof(line1), "%s %s", tempBuf, descBuf);
    clipText(line1, line1, sizeof(line1), (size_t)charSlots);
    if (g_weather.humidity >= 0) {
      snprintf(line2, sizeof(line2), "H %d%%  W %.1f", g_weather.humidity, g_weather.wind);
    } else {
      snprintf(line2, sizeof(line2), "%.18s", metaBuf);
    }
    clipText(line2, line2, sizeof(line2), (size_t)charSlots);

    gfx->setTextColor(tft.color565(248, 250, 252), top);
    setMediaFontBody(gfx);
    gfx->drawString(line1, tx, innerY + innerH / 2 - 9);
    gfx->setTextColor(tft.color565(191, 219, 254), top);
    setMediaFontMeta(gfx);
    gfx->drawString(line2, tx, innerY + innerH / 2 + 10);
    gfx->clearClipRect();
    return;
  }

  int tempY = innerY + innerH / 2 - 22;
  int descY = tempY + 24;
  int metaY = descY + 17;
  if (mode == WIDGET_MODE_COMPACT) {
    tempY = innerY + innerH / 2 - 16;
    descY = tempY + 20;
    metaY = descY + 16;
  }

  clipText(descBuf, descBuf, sizeof(descBuf), (size_t)charSlots);
  clipText(metaBuf, metaBuf, sizeof(metaBuf), (size_t)max(10, charSlots + 2));

  gfx->setTextColor(tft.color565(248, 250, 252), top);
  setMediaFontTitle(gfx);
  gfx->drawString(tempBuf, tx, tempY);

  gfx->setTextColor(tft.color565(226, 232, 240), top);
  setMediaFontBody(gfx);
  gfx->drawString(descBuf, tx, descY);

  gfx->setTextColor(tft.color565(191, 219, 254), top);
  setMediaFontMeta(gfx);
  gfx->drawString(metaBuf, tx, metaY);

  if (labelBuf[0]) {
    gfx->setTextColor(tft.color565(239, 246, 255), top);
    setMediaFontMeta(gfx);
    gfx->drawString(labelBuf, tx, innerY + 12);
  }
  gfx->clearClipRect();
}

int anchorIndexForCell(int profileIdx, int idx) {
  if (profileIdx < 0 || profileIdx >= PROFILES) return idx;
  if (idx < 0 || idx >= BTN_MAX) return idx;
  int16_t owner = BTN_OWNER[profileIdx][idx];
  if (owner < 0 || owner >= BTN_MAX) return idx;
  return (int)owner;
}

int gridIndexFromXY(int profileIdx, int x, int y) {
  int rows = PROFILE_ROWS[profileIdx];
  int cols = PROFILE_COLS[profileIdx];
  if (x < 0 || y < 0 || x >= SCREEN_WIDTH || y >= SCREEN_HEIGHT) return -1;
  int btn_w = SCREEN_WIDTH / cols;
  int btn_h = SCREEN_HEIGHT / rows;
  int c = x / btn_w;
  int r = y / btn_h;
  if (r < 0 || r >= rows || c < 0 || c >= cols) return -1;
  int idx = r * cols + c;
  return anchorIndexForCell(profileIdx, idx);
}

uint8_t profileIconFrameIndexForCell(int profileIdx, int idx) {
  if (profileIdx < 0 || profileIdx >= PROFILES) return 0;
  if (idx < 0 || idx >= BTN_MAX) return 0;
  uint8_t count = PROFILE_ICON_FRAME_COUNTS[profileIdx][idx];
  if (count <= 1) return 0;
  uint16_t iv = PROFILE_ICON_FRAME_INTERVAL[profileIdx][idx];
  if (iv < 40) iv = 40;
  uint8_t frame = (uint8_t)(((uint32_t)(millis() / iv)) % (uint32_t)count);
  if (frame >= ICON_ANIM_MAX_FRAMES) frame = 0;
  return frame;
}

const uint16_t* profileIconForCell(int profileIdx, int idx, uint8_t frameIdx) {
  if (profileIdx < 0 || profileIdx >= PROFILES) return nullptr;
  if (idx < 0 || idx >= BTN_MAX) return nullptr;
  uint8_t count = PROFILE_ICON_FRAME_COUNTS[profileIdx][idx];
  if (count <= 1) return PROFILE_ICONS[profileIdx][idx];
  uint8_t fi = frameIdx;
  if (fi >= ICON_ANIM_MAX_FRAMES) fi = 0;
  const uint16_t* ptr = PROFILE_ICON_FRAMES[profileIdx][idx][fi];
  return ptr ? ptr : PROFILE_ICONS[profileIdx][idx];
}

uint16_t profileIconKeyForCell(int profileIdx, int idx, uint8_t frameIdx) {
  if (profileIdx < 0 || profileIdx >= PROFILES) return ICON_KEY_NONE;
  if (idx < 0 || idx >= BTN_MAX) return ICON_KEY_NONE;
  uint8_t count = PROFILE_ICON_FRAME_COUNTS[profileIdx][idx];
  if (count <= 1) return PROFILE_ICON_KEYS[profileIdx][idx];
  uint8_t fi = frameIdx;
  if (fi >= ICON_ANIM_MAX_FRAMES) fi = 0;
  uint16_t key = PROFILE_ICON_FRAME_KEYS[profileIdx][idx][fi];
  return key;
}

void drawGridButtonByIndex(int profileIdx, int idx, bool highlight) {
  if (idx < 0) return;
  int rows = PROFILE_ROWS[profileIdx];
  int cols = PROFILE_COLS[profileIdx];
  int btn_w = SCREEN_WIDTH / cols;
  int btn_h = SCREEN_HEIGHT / rows;
  int anchor = anchorIndexForCell(profileIdx, idx);
  if (anchor < 0) return;
  int r = anchor / cols;
  int c = anchor % cols;
  if (r < 0 || r >= rows || c < 0 || c >= cols) return;
  int spanR = max(1, (int)BTN_SPAN_ROWS[profileIdx][anchor]);
  int spanC = max(1, (int)BTN_SPAN_COLS[profileIdx][anchor]);
  if (r + spanR > rows) spanR = rows - r;
  if (c + spanC > cols) spanC = cols - c;
  if (spanR < 1) spanR = 1;
  if (spanC < 1) spanC = 1;
  int x = c * btn_w;
  int y = r * btn_h;
  int w = btn_w * spanC;
  int h = btn_h * spanR;
  uint16_t iconSize = PROFILE_ICON_SIZE[profileIdx];
  uint8_t iconFrame = profileIconFrameIndexForCell(profileIdx, anchor);
  const char* label = BTN_LABELS[profileIdx][anchor];
  const uint16_t* icon = profileIconForCell(profileIdx, anchor, iconFrame);
  float textSize = BTN_TEXT_SIZE_PER[profileIdx][anchor];
  uint16_t bg = highlight ? COLOR_BTN_BG_HI : COLOR_BTN_BG;
  uint16_t fg = highlight ? COLOR_BTN_FG_HI : COLOR_BTN_FG;
  uint16_t iconKey = profileIconKeyForCell(profileIdx, anchor, iconFrame);
  uint8_t widgetKind = BTN_WIDGET_KIND[profileIdx][anchor];
  if (widgetKind == WIDGET_KIND_WEATHER) {
    drawWeatherWidgetAt(drawTarget(), x, y, w, h, highlight);
  } else if (widgetKind == WIDGET_KIND_METRIC) {
    drawMetricWidgetAt(drawTarget(), x, y, w, h, highlight, BTN_PATHS[profileIdx][anchor]);
  } else {
    float iconScale = highlight ? 0.90f : 1.0f;
    int yOffset = highlight ? 1 : 0;
    drawButtonAtEx(drawTarget(), x, y, w, h, label, icon, iconSize, textSize, bg, fg, iconKey, iconScale, yOffset);
  }
  pushIfSprite();
}

void drawProfileAt(lgfx::LovyanGFX* gfx, int profileIdx, int xOffset, bool updateButtons){
  int rows = PROFILE_ROWS[profileIdx];
  int cols = PROFILE_COLS[profileIdx];
  int btn_w = SCREEN_WIDTH / cols;
  int btn_h = SCREEN_HEIGHT / rows;
  uint16_t iconSize = PROFILE_ICON_SIZE[profileIdx];

  drawGridBackground(gfx, xOffset);

  int idx = 0;
  for (int r = 0; r < rows; r++) {
    for (int c = 0; c < cols; c++, idx++) {
      int owner = anchorIndexForCell(profileIdx, idx);
      if (owner != idx) continue;
      int spanR = max(1, (int)BTN_SPAN_ROWS[profileIdx][idx]);
      int spanC = max(1, (int)BTN_SPAN_COLS[profileIdx][idx]);
      if (r + spanR > rows) spanR = rows - r;
      if (c + spanC > cols) spanC = cols - c;
      if (spanR < 1) spanR = 1;
      if (spanC < 1) spanC = 1;
      int x = c * btn_w + xOffset;
      int y = r * btn_h;
      int w = btn_w * spanC;
      int h = btn_h * spanR;
      if (x + w <= 0 || x >= SCREEN_WIDTH) continue;
      uint8_t iconFrame = profileIconFrameIndexForCell(profileIdx, idx);
      const char* label = BTN_LABELS[profileIdx][idx];
      const uint16_t* icon = profileIconForCell(profileIdx, idx, iconFrame);
      float textSize = BTN_TEXT_SIZE_PER[profileIdx][idx];
      uint16_t bg = BTN_BG[profileIdx][idx];
      uint16_t fg = BTN_FG[profileIdx][idx];
      uint16_t iconKey = profileIconKeyForCell(profileIdx, idx, iconFrame);
      uint8_t widgetKind = BTN_WIDGET_KIND[profileIdx][idx];
      if (updateButtons) {
        for (int rr = 0; rr < spanR; rr++) {
          for (int cc = 0; cc < spanC; cc++) {
            int cell_idx = (r + rr) * cols + (c + cc);
            if (cell_idx < 0 || cell_idx >= BTN_MAX) continue;
            Button &b = buttons[cell_idx];
            b.x = x;
            b.y = y;
            b.w = w;
            b.h = h;
            b.label = label;
            b.icon  = icon;
          }
        }
      }
      if (widgetKind == WIDGET_KIND_WEATHER) {
        drawWeatherWidgetAt(gfx, x, y, w, h, false);
      } else if (widgetKind == WIDGET_KIND_METRIC) {
        drawMetricWidgetAt(gfx, x, y, w, h, false, BTN_PATHS[profileIdx][idx]);
      } else {
        drawButtonAtEx(gfx, x, y, w, h, label, icon, iconSize, textSize, bg, fg, iconKey);
      }
    }
  }
}

void redrawCurrentProfile() {
  lgfx::LovyanGFX* gfx = drawTarget();
  drawProfileAt(gfx, current_profile, 0, true);
  pushIfSprite();
}

bool isSpecialProfile(int idx) {
  return (idx == MONITOR_PROFILE_INDEX || idx == MEDIA_PROFILE_INDEX || idx == MIXER_PROFILE_INDEX);
}

void redrawForProfile(int idx) {
  if (MONITOR_PROFILE_INDEX >= 0 && idx == MONITOR_PROFILE_INDEX) {
    g_monitorDirty = true;
    g_monitorFullRedraw = true;
  } else if (MEDIA_PROFILE_INDEX >= 0 && idx == MEDIA_PROFILE_INDEX) {
    g_media.dirty = true;
  } else if (MIXER_PROFILE_INDEX >= 0 && idx == MIXER_PROFILE_INDEX) {
    g_mixer.dirty = true;
    g_mixerFullRedraw = true;
  } else {
    redrawCurrentProfile();
  }
}

void noteUserActivity() {
  g_last_input_ms = millis();
  if (g_screensaver_active) {
    g_screensaver_active = false;
    g_screensaver_last_draw = 0;
    redrawForProfile(current_profile);
  }
}

void updateClock(uint32_t now) {
  if (!g_clock_valid) return;
  if (g_clock_last_ms == 0) g_clock_last_ms = now;
  while (now - g_clock_last_ms >= 1000) {
    g_clock_last_ms += 1000;
    g_clock_s++;
    if (g_clock_s >= 60) {
      g_clock_s = 0;
      g_clock_m++;
      if (g_clock_m >= 60) {
        g_clock_m = 0;
        g_clock_h = (g_clock_h + 1) % 24;
      }
    }
  }
}

uint16_t blend565(lgfx::LovyanGFX* gfx, uint16_t c0, uint16_t c1, float t);

void drawStatusDot(lgfx::LovyanGFX* gfx, int x, int y, bool on, uint16_t onCol, uint16_t offCol) {
  if (on) {
    gfx->fillCircle(x, y, 4, onCol);
  } else {
    gfx->drawCircle(x, y, 4, offCol);
  }
}

extern const char* DOW_NAMES[];
extern const char* MON_NAMES[];

void drawScreensaver() {
  lgfx::LovyanGFX* gfx = drawTarget();
  uint32_t now = millis();

  if (SCR_BG_HAS_IMG && SCR_BG) {
    drawBgImage(gfx, SCR_BG, 0);
  } else {
    // --- ambient gradient with slow hue shift ---
    uint16_t baseTop = gfx->color565(0x0A, 0x0F, 0x1C);
    uint16_t baseBot = gfx->color565(0x02, 0x05, 0x0A);
    uint16_t shiftTop = gfx->color565(0x08, 0x14, 0x28);
    uint16_t shiftBot = gfx->color565(0x02, 0x08, 0x14);
    float phase = fmodf((float)now / 600000.0f, 1.0f); // ~10 min cycle
    float hue = 0.5f - 0.5f * cosf(phase * 2.0f * 3.14159265f);
    float tint = hue * 0.35f;
    uint16_t top = blend565(gfx, baseTop, shiftTop, tint);
    uint16_t bot = blend565(gfx, baseBot, shiftBot, tint);
    gfx->fillGradientRect(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT, top, bot, lgfx::v1::VLINEAR);

    // --- subtle noise overlay ---
    uint32_t rng = now * 1664525u + 1013904223u;
    uint16_t noiseHi = blend565(gfx, top, gfx->color565(255, 255, 255), 0.03f);
    uint16_t noiseLo = blend565(gfx, bot, gfx->color565(0, 0, 0), 0.06f);
    for (int i = 0; i < 140; i++) {
      rng = rng * 1664525u + 1013904223u;
      int x = (int)(rng % SCREEN_WIDTH);
      rng = rng * 1664525u + 1013904223u;
      int y = (int)((rng >> 16) % SCREEN_HEIGHT);
      uint16_t col = (rng & 0x80000000u) ? noiseHi : noiseLo;
      gfx->drawPixel(x, y, col);
    }
  }

  // --- time ---
  char buf[6];
  if (g_clock_valid) {
    buf[0] = '0' + (g_clock_h / 10);
    buf[1] = '0' + (g_clock_h % 10);
    buf[2] = ':';
    buf[3] = '0' + (g_clock_m / 10);
    buf[4] = '0' + (g_clock_m % 10);
    buf[5] = '\0';
  } else {
    strncpy(buf, "--:--", sizeof(buf));
  }

  float pulse = sinf((float)(now % 2000) * (2.0f * 3.14159265f / 2000.0f));
  float pulseAmt = 0.03f * pulse;
  uint16_t timeBase = COLOR_SCR_TIME;
  uint16_t timeCol = (pulseAmt >= 0.0f)
      ? blend565(gfx, timeBase, gfx->color565(255, 255, 255), pulseAmt)
      : blend565(gfx, timeBase, gfx->color565(0, 0, 0), -pulseAmt);

  int cx = SCREEN_WIDTH / 2;
  int timeY = (SCREEN_HEIGHT / 2) - 40;
  gfx->setTextDatum(textdatum_t::middle_center);
  setScreensaverTimeFont(gfx);
  gfx->setTextSize(SCREENSAVER_TIME_SIZE);
  gfx->setTextPadding(0);
  gfx->setTextColor(timeCol);
  gfx->drawString(buf, cx, timeY);

  // --- date ---
  int dateY = timeY + 56;
  setMediaFontBody(gfx);
  gfx->setTextSize(1.0f);
  gfx->setTextColor(COLOR_SCR_LABEL);
  gfx->setTextPadding(0);
  if (g_date_valid && g_date_m >= 1 && g_date_m <= 12) {
    char dateBuf[32];
    const char* dow = DOW_NAMES[g_date_dow % 7];
    const char* mon = MON_NAMES[g_date_m - 1];
    snprintf(dateBuf, sizeof(dateBuf), "%s \xc2\xb7 %s %d", dow, mon, (int)g_date_d);
    gfx->drawString(dateBuf, cx, dateY);
  } else if (SCREENSAVER_SHOW_LABEL && strlen(SCREENSAVER_LABEL) > 0) {
    gfx->drawString(SCREENSAVER_LABEL, cx, dateY);
  }

  // --- status bar ---
  int statusY = SCREEN_HEIGHT - 26;
  setMediaFontMeta(gfx);
  gfx->setTextSize(1.0f);
  uint16_t statusText = COLOR_SCR_LABEL;
  uint16_t statusOn = gfx->color565(0x86, 0xB7, 0xFF);
  uint16_t statusOff = gfx->color565(0x4C, 0x5C, 0x74);
  gfx->setTextDatum(textdatum_t::middle_left);
  gfx->setTextColor(statusText);
  gfx->setTextPadding(0);

  int x = 26;
  bool pcOn = (g_last_serial_ms != 0) && ((now - g_last_serial_ms) < 90000);
  drawStatusDot(gfx, x, statusY, pcOn, statusOn, statusOff);
  gfx->drawString("PC", x + 10, statusY);

  char tempBuf[16];
  if (g_temp_valid) {
    snprintf(tempBuf, sizeof(tempBuf), "%.1f\xC2\xB0C", g_temp_c);
  } else {
    strncpy(tempBuf, "--.-\xC2\xB0C", sizeof(tempBuf));
  }
  gfx->setTextDatum(textdatum_t::middle_right);
  gfx->drawString(tempBuf, SCREEN_WIDTH - 26, statusY);

  pushIfSprite();
}

void updateScreensaver(uint32_t now) {
  if (!g_screensaver_active) return;
  if (now - g_screensaver_last_draw < 250) return;
  g_screensaver_last_draw = now;
  drawScreensaver();
}

extern volatile int32_t _enc_edges;
bool checkScreensaverWake() {
  uint16_t tx, ty;
  bool pressed = tft.getTouch(&tx, &ty);
  if (pressed) {
    noteUserActivity();
    return true;
  }
  int32_t edges = 0;
  noInterrupts();
  edges = _enc_edges;
  interrupts();
  if (edges != 0) {
    noteUserActivity();
    return true;
  }
  if (digitalRead(ENC_SW) == LOW || digitalRead(BTN_A) == LOW || digitalRead(BTN_B) == LOW) {
    noteUserActivity();
    return true;
  }
  return false;
}

void drawMonitorScreen(bool fullRedraw);
void drawMediaIfNeeded(bool forceFull);
void drawMixerScreen();

void renderProfileSnapshot(int profileIdx, lgfx::LGFX_Sprite* spr) {
  if (!spr) return;
  g_overrideTarget = static_cast<lgfx::LovyanGFX*>(spr);
  spr->fillScreen(TFT_BLACK);
  if (MONITOR_PROFILE_INDEX >= 0 && profileIdx == MONITOR_PROFILE_INDEX) {
    drawMonitorScreen(true);
  } else if (MEDIA_PROFILE_INDEX >= 0 && profileIdx == MEDIA_PROFILE_INDEX) {
    bool prevAllow = g_allow_draw;
    g_allow_draw = true;
    drawMediaIfNeeded(true);
    g_allow_draw = prevAllow;
  } else if (MIXER_PROFILE_INDEX >= 0 && profileIdx == MIXER_PROFILE_INDEX) {
    drawMixerScreen();
  } else {
    drawProfileAt(static_cast<lgfx::LovyanGFX*>(spr), profileIdx, 0, false);
  }
  g_overrideTarget = nullptr;
}

void startProfileTransition(int from, int to, int dir) {
  g_trans.active = true;
  g_trans.from = from;
  g_trans.to = to;
  g_trans.dir = dir;
  g_trans.start_ms = millis();
  g_transUseSnapshots = false;
  if (g_transSpritesReady) {
    renderProfileSnapshot(from, &g_transOld);
    renderProfileSnapshot(to, &g_transNew);
    g_transUseSnapshots = true;
  }
}

bool updateProfileTransition(uint32_t now) {
  if (!g_trans.active) return false;
  uint32_t elapsed = now - g_trans.start_ms;
  float t = (g_trans.duration_ms > 0) ? (float)elapsed / (float)g_trans.duration_ms : 1.0f;
  if (t > 1.0f) t = 1.0f;
  // easeInOutCubic for smoother motion
  float te = 0.0f;
  if (t < 0.5f) {
    te = 4.0f * t * t * t;
  } else {
    float u = -2.0f * t + 2.0f;
    te = 1.0f - (u * u * u) / 2.0f;
  }
  int offset = (int)(te * SCREEN_WIDTH);
  int dir = (g_trans.dir == 0) ? -1 : g_trans.dir;
  int oldX = (dir < 0) ? -offset : offset;
  int newX = (dir < 0) ? SCREEN_WIDTH - offset : -SCREEN_WIDTH + offset;

  if (g_transUseSnapshots && g_transSpritesReady) {
    if (g_spriteReady) {
      g_sprite.fillScreen(TFT_BLACK);
      g_transOld.pushSprite(&g_sprite, oldX, 0);
      g_transNew.pushSprite(&g_sprite, newX, 0);
      g_sprite.pushSprite(0, 0);
    } else {
      tft.fillRect(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT, TFT_BLACK);
      g_transOld.pushSprite(oldX, 0);
      g_transNew.pushSprite(newX, 0);
    }
  } else {
    lgfx::LovyanGFX* gfx = drawTarget();
    gfx->fillRect(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT, TFT_BLACK);
    drawProfileAt(gfx, g_trans.from, oldX, false);
    drawProfileAt(gfx, g_trans.to, newX, false);
    pushIfSprite();
  }

  if (t >= 1.0f) {
    g_trans.active = false;
    g_transUseSnapshots = false;
    current_profile = g_trans.to;
    redrawForProfile(current_profile);
  }
  return true;
}

void switchProfileByIndex(int idx, bool notifyHost, int dir) {
  if (idx < 0 || idx >= PROFILES) return;
  if (current_profile == idx && !g_trans.active) return;
  g_trans.active = false;
  Serial.print("LOADED:"); Serial.println(PROFILE_NAMES[idx]);
  if (notifyHost) {
    Serial.print("SwitchProfile:"); Serial.println(PROFILE_NAMES[idx]);
  }

  (void)dir;

  current_profile = idx;
  redrawForProfile(idx);
}

void switchProfileByName(const char* name) {
  for (int i = 0; i < PROFILES; i++) {
    if (strcmp(PROFILE_NAMES[i], name) == 0) {
      switchProfileByIndex(i, false, 0);
      return;
    }
  }
}

void switchProfileRelative(int delta) {
  if (PROFILES <= 1) return;
  int idx = current_profile + delta;
  if (idx < 0) idx = PROFILES - 1;
  if (idx >= PROFILES) idx = 0;
  int dir = (delta > 0) ? -1 : 1;
  switchProfileByIndex(idx, true, dir);
}

void reportBenchResult(const char* stateTag) {
  uint32_t now = millis();
  uint32_t elapsed = (now >= g_bench.startMs) ? (now - g_bench.startMs) : 0;
  if (elapsed == 0) elapsed = 1;
  float elapsedSec = (float)elapsed / 1000.0f;
  float fps = elapsedSec > 0.0f ? ((float)g_bench.frames / elapsedSec) : 0.0f;
  float avgUs = (g_bench.frames > 0) ? ((float)g_bench.drawUsAcc / (float)g_bench.frames) : 0.0f;
  Serial.printf(
    "BENCH:RESULT;STATE=%s;EL=%lu;FR=%lu;EXP=%lu;DROP=%lu;FPS=%.2f;AUS=%.1f;LUS=%lu;N=%u;SIZE=%u;TFPS=%u;FAST=%u\n",
    stateTag ? stateTag : "DONE",
    (unsigned long)elapsed,
    (unsigned long)g_bench.frames,
    (unsigned long)g_bench.expected,
    (unsigned long)g_bench.dropped,
    fps,
    avgUs,
    (unsigned long)g_bench.drawUsLast,
    (unsigned int)g_bench.tiles,
    (unsigned int)g_bench.tileSize,
    (unsigned int)g_bench.targetFps,
    (unsigned int)(g_bench.directFast ? 1 : 0)
  );
}

void stopEspRenderBench(bool userStop) {
  if (!g_bench.active) return;
  g_bench.active = false;
  reportBenchResult(userStop ? "STOPPED" : "DONE");
  redrawForProfile(current_profile);
}

void startEspRenderBench(int tiles, int tileSize, int fps, int durSec, bool directFast) {
  if (tiles < 1) tiles = 1;
  if (tiles > (int)ESP_BENCH_MAX_TILES) tiles = (int)ESP_BENCH_MAX_TILES;
  if (tileSize < 40) tileSize = 40;
  if (tileSize > 220) tileSize = 220;
  if (fps < 1) fps = 1;
  if (fps > 60) fps = 60;
  if (durSec < 2) durSec = 2;
  if (durSec > 300) durSec = 300;

  g_bench.active = true;
  g_bench.directFast = directFast;
  g_bench.fullRedraw = true;
  g_bench.tiles = (uint8_t)tiles;
  g_bench.tileSize = (uint16_t)tileSize;
  g_bench.targetFps = (uint16_t)fps;
  g_bench.durationMs = (uint32_t)durSec * 1000UL;
  g_bench.startMs = millis();
  g_bench.nextFrameMs = g_bench.startMs;
  g_bench.lastStatMs = g_bench.startMs;
  g_bench.lastHudMs = g_bench.startMs;
  g_bench.frames = 0;
  g_bench.expected = 0;
  g_bench.dropped = 0;
  g_bench.drawUsLast = 0;
  g_bench.drawUsAcc = 0;
  g_bench.bgCol = tft.color565(6, 10, 18);
  g_bench.hudBgCol = tft.color565(8, 14, 24);
  g_bench.hudFgCol = tft.color565(226, 232, 240);
  g_bench.pulseCol = tft.color565(245, 245, 255);
  for (int i = 0; i < (int)ESP_BENCH_MAX_TILES; i++) {
    g_bench.prevX[i] = -32768;
    g_bench.prevY[i] = -32768;
    uint8_t rch = (uint8_t)(90 + (i * 37) % 120);
    uint8_t gch = (uint8_t)(80 + (i * 53) % 120);
    uint8_t bch = (uint8_t)(120 + (i * 29) % 110);
    g_bench.tileCol[i] = tft.color565(rch, gch, bch);
    g_bench.glowCol[i] = tft.color565(min(255, (int)rch + 40), min(255, (int)gch + 40), min(255, (int)bch + 40));
  }

  g_screensaver_active = false;
  noteUserActivity();

  Serial.printf(
    "BENCH:STARTED;N=%u;SIZE=%u;FPS=%u;DUR=%lu;FAST=%u\n",
    (unsigned int)g_bench.tiles,
    (unsigned int)g_bench.tileSize,
    (unsigned int)g_bench.targetFps,
    (unsigned long)(g_bench.durationMs / 1000UL),
    (unsigned int)(g_bench.directFast ? 1 : 0)
  );
}

void parseBenchPacket(const char* payload) {
  if (!payload || !*payload) return;
  if (strncmp(payload, "STOP", 4) == 0) {
    stopEspRenderBench(true);
    return;
  }
  if (strncmp(payload, "PING", 4) == 0) {
    Serial.println("BENCH:PONG");
    return;
  }

  int tiles = g_bench.tiles > 0 ? (int)g_bench.tiles : 6;
  int tileSize = g_bench.tileSize > 0 ? (int)g_bench.tileSize : 96;
  int fps = g_bench.targetFps > 0 ? (int)g_bench.targetFps : 24;
  int durSec = (int)(g_bench.durationMs > 0 ? (g_bench.durationMs / 1000UL) : 20UL);
  int fastMode = g_bench.directFast ? 1 : 0;
  bool start = false;

  char buf[200];
  strncpy(buf, payload, sizeof(buf) - 1);
  buf[sizeof(buf) - 1] = '\0';
  char* token = strtok(buf, ";");
  while (token) {
    if (strcmp(token, "START") == 0) {
      start = true;
    } else if (strncmp(token, "N=", 2) == 0) {
      tiles = atoi(token + 2);
    } else if (strncmp(token, "SIZE=", 5) == 0) {
      tileSize = atoi(token + 5);
    } else if (strncmp(token, "FPS=", 4) == 0) {
      fps = atoi(token + 4);
    } else if (strncmp(token, "DUR=", 4) == 0) {
      durSec = atoi(token + 4);
    } else if (strncmp(token, "FAST=", 5) == 0) {
      fastMode = atoi(token + 5);
    }
    token = strtok(NULL, ";");
  }
  if (start) {
    startEspRenderBench(tiles, tileSize, fps, durSec, fastMode != 0);
  }
}

void drawEspRenderBenchFrame(uint32_t now) {
  if (!g_bench.active) return;
  uint32_t elapsed = (now >= g_bench.startMs) ? (now - g_bench.startMs) : 0;
  if (elapsed >= g_bench.durationMs) {
    stopEspRenderBench(false);
    return;
  }

  uint32_t frameInterval = (g_bench.targetFps > 0) ? (1000UL / g_bench.targetFps) : 41UL;
  if (frameInterval < 10UL) frameInterval = 10UL;
  if (now < g_bench.nextFrameMs) return;

  uint32_t lag = now - g_bench.nextFrameMs;
  if (lag >= frameInterval) {
    uint32_t missed = lag / frameInterval;
    g_bench.dropped += missed;
    g_bench.expected += missed;
    g_bench.nextFrameMs += missed * frameInterval;
  }
  g_bench.expected += 1;
  g_bench.nextFrameMs += frameInterval;

  uint32_t t0 = micros();
  lgfx::LovyanGFX* gfx = g_bench.directFast
    ? static_cast<lgfx::LovyanGFX*>(&tft)
    : drawTarget();
  if (g_bench.fullRedraw) {
    gfx->fillScreen(g_bench.bgCol);
    g_bench.fullRedraw = false;
    for (int i = 0; i < (int)ESP_BENCH_MAX_TILES; i++) {
      g_bench.prevX[i] = -32768;
      g_bench.prevY[i] = -32768;
    }
  }

  int n = max(1, (int)g_bench.tiles);
  int cols = 1;
  while (cols * cols < n) cols++;
  int rows = (n + cols - 1) / cols;
  int pad = 10;
  int gap = 7;
  int areaW = SCREEN_WIDTH - pad * 2;
  int areaH = SCREEN_HEIGHT - pad * 2 - 48;
  int cellW = max(10, (areaW - gap * (cols - 1)) / cols);
  int cellH = max(10, (areaH - gap * (rows - 1)) / rows);
  int side = min((int)g_bench.tileSize, min(cellW, cellH));
  if (side < 16) side = min(cellW, cellH);
  uint32_t tickX = elapsed / 12U;
  uint32_t tickY = elapsed / 17U;
  if (g_bench.directFast) tft.startWrite();

  for (int i = 0; i < n; i++) {
    int r = i / cols;
    int c = i % cols;
    int cellX = pad + c * (cellW + gap);
    int cellY = pad + r * (cellH + gap);
    int ampX = max(1, (cellW - side) / 2);
    int ampY = max(1, (cellH - side) / 2);
    int offX = triWaveOffset(tickX + (uint32_t)(i * 11), ampX);
    int offY = triWaveOffset(tickY + (uint32_t)(i * 17), ampY);
    int x = cellX + (cellW - side) / 2 + offX;
    int y = cellY + (cellH - side) / 2 + offY;

    int oldX = g_bench.prevX[i];
    int oldY = g_bench.prevY[i];
    uint16_t tileCol = g_bench.tileCol[i];
    if (oldX <= -20000 || oldY <= -20000) {
      gfx->fillRect(x, y, side, side, tileCol);
    } else {
      int ox = oldX;
      int oy = oldY;
      int nx = x;
      int ny = y;
      int ex0 = max(ox, nx);
      int ey0 = max(oy, ny);
      int ex1 = min(ox + side, nx + side);
      int ey1 = min(oy + side, ny + side);
      int ew = ex1 - ex0;
      int eh = ey1 - ey0;
      if (ew <= 0 || eh <= 0) {
        gfx->fillRect(ox, oy, side, side, g_bench.bgCol);
        gfx->fillRect(nx, ny, side, side, tileCol);
      } else {
        int topOldH = ey0 - oy;
        int botOldH = oy + side - ey1;
        int leftOldW = ex0 - ox;
        int rightOldW = ox + side - ex1;
        if (topOldH > 0) gfx->fillRect(ox, oy, side, topOldH, g_bench.bgCol);
        if (botOldH > 0) gfx->fillRect(ox, ey1, side, botOldH, g_bench.bgCol);
        if (leftOldW > 0 && eh > 0) gfx->fillRect(ox, ey0, leftOldW, eh, g_bench.bgCol);
        if (rightOldW > 0 && eh > 0) gfx->fillRect(ex1, ey0, rightOldW, eh, g_bench.bgCol);

        int topNewH = ey0 - ny;
        int botNewH = ny + side - ey1;
        int leftNewW = ex0 - nx;
        int rightNewW = nx + side - ex1;
        if (topNewH > 0) gfx->fillRect(nx, ny, side, topNewH, tileCol);
        if (botNewH > 0) gfx->fillRect(nx, ey1, side, botNewH, tileCol);
        if (leftNewW > 0 && eh > 0) gfx->fillRect(nx, ey0, leftNewW, eh, tileCol);
        if (rightNewW > 0 && eh > 0) gfx->fillRect(ex1, ey0, rightNewW, eh, tileCol);
      }
    }
    g_bench.prevX[i] = (int16_t)x;
    g_bench.prevY[i] = (int16_t)y;
  }

  if ((now - g_bench.lastHudMs >= 1000UL) || (g_bench.frames == 0)) {
    char line1[96];
    char line2[96];
    float elapsedSec = max(0.001f, (float)elapsed / 1000.0f);
    float curFps = (float)g_bench.frames / elapsedSec;
    snprintf(line1, sizeof(line1), "ESP Render Bench N=%u SIZE=%u TFPS=%u",
             (unsigned int)g_bench.tiles, (unsigned int)g_bench.tileSize, (unsigned int)g_bench.targetFps);
    snprintf(line2, sizeof(line2), "FPS %.1f FR %lu/%lu DROP %lu",
             curFps,
             (unsigned long)g_bench.frames,
             (unsigned long)g_bench.expected,
             (unsigned long)g_bench.dropped);
    int hudY = SCREEN_HEIGHT - 34;
    gfx->fillRect(0, hudY, SCREEN_WIDTH, 34, g_bench.hudBgCol);
    gfx->setTextDatum(textdatum_t::top_left);
    gfx->setFont(UI_FONT);
    gfx->setTextSize(1);
    gfx->setTextColor(g_bench.hudFgCol, g_bench.hudBgCol);
    gfx->drawString(line1, 6, hudY + 2);
    gfx->drawString(line2, 6, hudY + 18);
    g_bench.lastHudMs = now;
  }
  if (g_bench.directFast) {
    tft.endWrite();
  } else {
    pushIfSprite();
  }

  uint32_t drawUs = micros() - t0;
  g_bench.drawUsLast = drawUs;
  g_bench.drawUsAcc += (uint64_t)drawUs;
  g_bench.frames += 1;

  if (now - g_bench.lastStatMs >= 1000UL) {
    float elapsedSec = max(0.001f, (float)elapsed / 1000.0f);
    float curFps = (float)g_bench.frames / elapsedSec;
    float avgUs = (g_bench.frames > 0) ? ((float)g_bench.drawUsAcc / (float)g_bench.frames) : 0.0f;
    Serial.printf(
      "BENCH:STAT;EL=%lu;FR=%lu;EXP=%lu;DROP=%lu;FPS=%.2f;LUS=%lu;AUS=%.1f;FAST=%u\n",
      (unsigned long)elapsed,
      (unsigned long)g_bench.frames,
      (unsigned long)g_bench.expected,
      (unsigned long)g_bench.dropped,
      curFps,
      (unsigned long)g_bench.drawUsLast,
      avgUs,
      (unsigned int)(g_bench.directFast ? 1 : 0)
    );
    g_bench.lastStatMs = now;
  }
}



// --- MONITOR PARSING ---

String _rxbuf;
void parseMonitorPacket(const char* payload) {
  float cpu = 0, ram = 0, gpu = -1, disk = 0, net = 0, fps = -1;

  char buf[160];
  strncpy(buf, payload, sizeof(buf)-1);
  buf[sizeof(buf)-1] = '\0';

  char* token = strtok(buf, ";");
  while (token) {
    if      (strncmp(token, "CPU=", 4) == 0) sscanf(token+4, "%f", &cpu);
    else if (strncmp(token, "RAM=", 4) == 0) sscanf(token+4, "%f", &ram);
    else if (strncmp(token, "GPU=", 4) == 0) sscanf(token+4, "%f", &gpu);
    else if (strncmp(token, "DISK=",5) == 0) sscanf(token+5, "%f", &disk);
    else if (strncmp(token, "NET=", 4) == 0) sscanf(token+4, "%f", &net);
    else if (strncmp(token, "FPS=", 4) == 0) sscanf(token+4, "%f", &fps);
    token = strtok(NULL, ";");
  }

  g_cpu = cpu;
  g_ram = ram;
  g_gpu = gpu;
  g_disk = disk;
  g_net = net;
  g_fps = fps;

  g_monitorDirty = true;
}

// --- MIXER PARSING ---

static inline void _clearMixerSlot(MixerSlot &slot) {
  slot.volume = 0;
  slot.muted = false;
  slot.active = false;
  slot.name[0] = '\0';
}

static inline void _copyMixerName(MixerSlot &slot, const char* src) {
  if (!src) { slot.name[0] = '\0'; return; }
  int j = 0;
  for (int i = 0; src[i] && j < 15; i++) {
    char ch = src[i];
    if (ch == '_') ch = ' ';
    if (ch < 32 || ch > 126) continue;
    slot.name[j++] = ch;
  }
  slot.name[j] = '\0';
}

void parseMixerPacket(const char* payload) {
  char buf[180];
  strncpy(buf, payload, sizeof(buf)-1);
  buf[sizeof(buf)-1] = '\0';

  char* token = strtok(buf, ";");
  while (token) {
    if (strncmp(token, "MASTER=", 7) == 0) {
      g_mixer.master = atoi(token + 7);
    } else if (strncmp(token, "MM=", 3) == 0) {
      g_mixer.masterMuted = (atoi(token + 3) != 0);
    } else if (strncmp(token, "MIC=", 4) == 0) {
      g_mixer.mic = atoi(token + 4);
    } else if (strncmp(token, "MICM=", 5) == 0) {
      g_mixer.micMuted = (atoi(token + 5) != 0);
    } else if (strncmp(token, "APP1=", 5) == 0) {
      char* val = token + 5;
      if (!val || !*val) {
        _clearMixerSlot(g_mixer.apps[0]);
      } else {
        char* p1 = strchr(val, ',');
        if (p1) {
          *p1 = '\0';
          char* p2 = strchr(p1 + 1, ',');
          int vol = atoi(p1 + 1);
          int mut = p2 ? atoi(p2 + 1) : 0;
          _copyMixerName(g_mixer.apps[0], val);
          g_mixer.apps[0].volume = vol;
          g_mixer.apps[0].muted = (mut != 0);
          g_mixer.apps[0].active = true;
        }
      }
    } else if (strncmp(token, "APP2=", 5) == 0) {
      char* val = token + 5;
      if (!val || !*val) {
        _clearMixerSlot(g_mixer.apps[1]);
      } else {
        char* p1 = strchr(val, ',');
        if (p1) {
          *p1 = '\0';
          char* p2 = strchr(p1 + 1, ',');
          int vol = atoi(p1 + 1);
          int mut = p2 ? atoi(p2 + 1) : 0;
          _copyMixerName(g_mixer.apps[1], val);
          g_mixer.apps[1].volume = vol;
          g_mixer.apps[1].muted = (mut != 0);
          g_mixer.apps[1].active = true;
        }
      }
    }
    token = strtok(NULL, ";");
  }
  g_mixer.dirty = true;
}

static inline void _copyWidgetText(char* dst, size_t dstSize, const char* src) {
  if (!dst || dstSize == 0) return;
  if (!src) {
    dst[0] = '\0';
    return;
  }
  size_t j = 0;
  for (size_t i = 0; src[i] != '\0' && j + 1 < dstSize; i++) {
    char ch = src[i];
    if (ch == '_' || ch == '|') ch = ' ';
    if (ch < 32 || ch > 126) continue;
    if (ch == ';' || ch == '=') continue;
    dst[j++] = ch;
  }
  dst[j] = '\0';
}

void parseWidgetPacket(const char* payload) {
  if (!payload || !*payload) return;

  char buf[220];
  strncpy(buf, payload, sizeof(buf) - 1);
  buf[sizeof(buf) - 1] = '\0';

  bool isWeather = false;
  bool isMetric = false;
  float temp = g_weather.tempC;
  float feels = g_weather.feelsC;
  int humidity = g_weather.humidity;
  float wind = g_weather.wind;
  int code = g_weather.code;
  float metricCpu = g_metric.cpu;
  float metricRam = g_metric.ram;
  float metricGpu = g_metric.gpu;
  float metricGpuTemp = g_metric.gpuTemp;
  float metricFps = g_metric.fps;
  float metricNet = g_metric.net;
  float metricDisk = g_metric.disk;
  float metricCpuGhz = g_metric.cpuGhz;
  char label[sizeof(g_weather.label)];
  char desc[sizeof(g_weather.desc)];
  strncpy(label, g_weather.label, sizeof(label) - 1);
  label[sizeof(label) - 1] = '\0';
  strncpy(desc, g_weather.desc, sizeof(desc) - 1);
  desc[sizeof(desc) - 1] = '\0';

  char* token = strtok(buf, ";");
  while (token) {
    if (strncmp(token, "TYPE=", 5) == 0) {
      const char* tp = token + 5;
      if (strcmp(tp, "WEATHER") == 0 || strcmp(tp, "weather") == 0) {
        isWeather = true;
      } else if (strcmp(tp, "METRIC") == 0 || strcmp(tp, "metric") == 0) {
        isMetric = true;
      }
    } else if (strncmp(token, "T=", 2) == 0) {
      sscanf(token + 2, "%f", &temp);
    } else if (strncmp(token, "TEMP=", 5) == 0) {
      sscanf(token + 5, "%f", &temp);
    } else if (strncmp(token, "F=", 2) == 0) {
      sscanf(token + 2, "%f", &feels);
    } else if (strncmp(token, "FEELS=", 6) == 0) {
      sscanf(token + 6, "%f", &feels);
    } else if (strncmp(token, "H=", 2) == 0) {
      humidity = atoi(token + 2);
    } else if (strncmp(token, "HUM=", 4) == 0) {
      humidity = atoi(token + 4);
    } else if (strncmp(token, "W=", 2) == 0) {
      sscanf(token + 2, "%f", &wind);
    } else if (strncmp(token, "WIND=", 5) == 0) {
      sscanf(token + 5, "%f", &wind);
    } else if (strncmp(token, "C=", 2) == 0) {
      code = atoi(token + 2);
    } else if (strncmp(token, "CODE=", 5) == 0) {
      code = atoi(token + 5);
    } else if (strncmp(token, "L=", 2) == 0) {
      _copyWidgetText(label, sizeof(label), token + 2);
    } else if (strncmp(token, "LBL=", 4) == 0) {
      _copyWidgetText(label, sizeof(label), token + 4);
    } else if (strncmp(token, "D=", 2) == 0) {
      _copyWidgetText(desc, sizeof(desc), token + 2);
    } else if (strncmp(token, "DESC=", 5) == 0) {
      _copyWidgetText(desc, sizeof(desc), token + 5);
    } else if (strncmp(token, "CPU=", 4) == 0) {
      sscanf(token + 4, "%f", &metricCpu);
    } else if (strncmp(token, "RAM=", 4) == 0) {
      sscanf(token + 4, "%f", &metricRam);
    } else if (strncmp(token, "GPU=", 4) == 0) {
      sscanf(token + 4, "%f", &metricGpu);
    } else if (strncmp(token, "GPUT=", 5) == 0) {
      sscanf(token + 5, "%f", &metricGpuTemp);
    } else if (strncmp(token, "GPU_TEMP=", 9) == 0) {
      sscanf(token + 9, "%f", &metricGpuTemp);
    } else if (strncmp(token, "FPS=", 4) == 0) {
      sscanf(token + 4, "%f", &metricFps);
    } else if (strncmp(token, "NET=", 4) == 0) {
      sscanf(token + 4, "%f", &metricNet);
    } else if (strncmp(token, "DISK=", 5) == 0) {
      sscanf(token + 5, "%f", &metricDisk);
    } else if (strncmp(token, "CPUGHZ=", 7) == 0) {
      sscanf(token + 7, "%f", &metricCpuGhz);
    } else if (strncmp(token, "CPU_GHZ=", 8) == 0) {
      sscanf(token + 8, "%f", &metricCpuGhz);
    }
    token = strtok(NULL, ";");
  }

  if (isWeather) {
    g_weather.valid = true;
    g_weather.tempC = temp;
    g_weather.feelsC = feels;
    g_weather.humidity = humidity;
    g_weather.wind = wind;
    g_weather.code = code;
    strncpy(g_weather.label, label, sizeof(g_weather.label) - 1);
    g_weather.label[sizeof(g_weather.label) - 1] = '\0';
    strncpy(g_weather.desc, desc, sizeof(g_weather.desc) - 1);
    g_weather.desc[sizeof(g_weather.desc) - 1] = '\0';
    g_weather.dirty = true;
  }

  if (isMetric) {
    g_metric.cpu = metricCpu;
    g_metric.ram = metricRam;
    g_metric.gpu = metricGpu;
    g_metric.gpuTemp = metricGpuTemp;
    g_metric.fps = metricFps;
    g_metric.net = metricNet;
    g_metric.disk = metricDisk;
    g_metric.cpuGhz = metricCpuGhz;
    g_metric.dirty = true;
  }
}


void handleMediaLine(const char* s) {
  if (strncmp(s, "SRC:", 4) == 0) {
    String val = String(s + 4);
    val.trim();
    val.toUpperCase();
    if (val == "SPOTIFY")      g_media.source = MediaSource::MEDIA_SPOTIFY;
    else if (val == "YOUTUBE") g_media.source = MediaSource::MEDIA_YOUTUBE;
    else if (val == "VLC")     g_media.source = MediaSource::MEDIA_VLC;
    else                       g_media.source = MediaSource::MEDIA_GENERIC;
    g_media.dirty = true;
    return;
  }

  if (strncmp(s, "TRK:", 4) == 0) {
    String val = String(s + 4);
    val.trim();
    if (val.length() == 0) val = "—";
    g_media.track = val;
    g_media.dirty = true;
    return;
  }

  if (strncmp(s, "POS:", 4) == 0) {
    String val = String(s + 4);
    val.trim();
    int slashIdx = val.indexOf('/');
    if (slashIdx > 0) {
      int pos = val.substring(0, slashIdx).toInt();
      int dur = val.substring(slashIdx + 1).toInt();
      if (dur <= 0) dur = 1;
      g_media.position_s = max(0, pos);
      g_media.duration_s = dur;
    }
    return;
  }

  if (strncmp(s, "STATE:", 6) == 0) {
    String val = String(s + 6);
    val.trim();
    val.toUpperCase();
    g_media.isPlaying = (val == "PLAYING");
    g_media.dirty = true;
    return;
  }

  if (strncmp(s, "VOL:", 4) == 0) {
    String val = String(s + 4);
    val.trim();
    int v = val.toInt();
    if (v < 0 || v > 100) g_media.volume_pct = -1;
    else                  g_media.volume_pct = v;
    g_media.dirty = true;
    return;
  }
}

// --- DATE HELPERS ---
const char* DOW_NAMES[] = { "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday" };
const char* MON_NAMES[] = { "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec" };

int calcDow(int y, int m, int d) {
  if (m < 3) { m += 12; y -= 1; }
  int K = y % 100;
  int J = y / 100;
  int h = (d + (13 * (m + 1)) / 5 + K + (K / 4) + (J / 4) + (5 * J)) % 7;
  int dow = (h + 6) % 7; // 0 = Sunday
  if (dow < 0) dow = 0;
  if (dow > 6) dow = 6;
  return dow;
}

// --- HOST COMMAND ROUTER ---

void handleHostCommand(const char* s) {
  g_last_serial_ms = millis();
  if (strncmp(s, "TIME:", 5) == 0) {
    int hh = -1, mm = -1, ss = 0;
    int cnt = sscanf(s + 5, "%d:%d:%d", &hh, &mm, &ss);
    if (cnt >= 2 && hh >= 0 && hh < 24 && mm >= 0 && mm < 60) {
      if (ss < 0 || ss > 59) ss = 0;
      g_clock_h = (uint8_t)hh;
      g_clock_m = (uint8_t)mm;
      g_clock_s = (uint8_t)ss;
      g_clock_valid = true;
      g_clock_last_ms = millis();
    }
    return;
  }
  if (strncmp(s, "DATE:", 5) == 0) {
    int yy = 0, mm = 0, dd = 0;
    if (sscanf(s + 5, "%d-%d-%d", &yy, &mm, &dd) == 3) {
      if (yy > 1970 && mm >= 1 && mm <= 12 && dd >= 1 && dd <= 31) {
        g_date_y = yy;
        g_date_m = (uint8_t)mm;
        g_date_d = (uint8_t)dd;
        g_date_dow = (uint8_t)calcDow(yy, mm, dd);
        g_date_valid = true;
      }
    }
    return;
  }
  if (strncmp(s, "TEMP:", 5) == 0) {
    float tc = 0.0f;
    if (sscanf(s + 5, "%f", &tc) == 1) {
      g_temp_c = tc;
      g_temp_valid = true;
    }
    return;
  }
  if (strncmp(s, "WIFI:", 5) == 0) {
    int v = atoi(s + 5);
    g_wifi_connected = (v != 0);
    return;
  }
  if (strncmp(s, "WAKE", 4) == 0) {
    noteUserActivity();
    return;
  }
  if (strncmp(s, "BENCH:", 6) == 0) {
    parseBenchPacket(s + 6);
    return;
  }
  if (strncmp(s, "SwitchProfile:", 14) == 0) {
    const char* name = s + 14;
    while (*name == ' ') name++;
    if (*name) switchProfileByName(name);
  } else if (strncmp(s, "WIDGET:", 7) == 0) {
    parseWidgetPacket(s + 7);
  } else if (strncmp(s, "MON:", 4) == 0) {
    parseMonitorPacket(s + 4);
  } else if (strncmp(s, "MIX:", 4) == 0) {
    parseMixerPacket(s + 4);
  } else {
    // forward na media UI modul (SRC/TRK/POS/STATE/VOL)
    handleMediaLine(s);
  }
}


void pumpSerialRx() {
  while (Serial.available()) {
    char ch = (char)Serial.read();
    if (ch == '\r' || ch == '\n') {
      if (_rxbuf.length()) { handleHostCommand(_rxbuf.c_str()); _rxbuf = ""; }
    } else {
      if (_rxbuf.length() < 220) _rxbuf += ch;
    }
  }
}

void sendActionForIndex(int idx) {
  if (idx < 0) return;
  int actionIdx = anchorIndexForCell(current_profile, idx);
  if (actionIdx < 0 || actionIdx >= BTN_MAX) actionIdx = idx;
  const char* action = BTN_ACTIONS[current_profile][actionIdx];
  const char* path   = BTN_PATHS  [current_profile][actionIdx];
  if (!action || strlen(action)==0) return;
  if (strcmp(action, "WeatherWidget") == 0 || strcmp(action, "Weather Widget") == 0) return;
  if (strcmp(action, "MetricWidget") == 0 || strcmp(action, "Metric Widget") == 0) return;

  if (path && strlen(path) > 0) {
    Serial.print(action); Serial.print(":"); Serial.println(path);
  } else {
    Serial.println(action);
  }
}

int swipeThresholdForProfile(int profileIdx) {
  bool special = (profileIdx == MONITOR_PROFILE_INDEX || profileIdx == MEDIA_PROFILE_INDEX || profileIdx == MIXER_PROFILE_INDEX);
  int v = special ? (SCREEN_WIDTH / 10) : (SCREEN_WIDTH / 8);
  if (v < 24) v = 24;
  if (v > 120) v = 120;
  return v;
}

int swipeThresholdPx() {
  return swipeThresholdForProfile(current_profile);
}

int tapSlopForProfile(int profileIdx) {
  bool special = (profileIdx == MONITOR_PROFILE_INDEX || profileIdx == MEDIA_PROFILE_INDEX || profileIdx == MIXER_PROFILE_INDEX);
  int v = special ? (SCREEN_WIDTH / 34) : (SCREEN_WIDTH / 38);
  if (v < 12) v = 12;
  if (v > 24) v = 24;
  return v;
}

int tapSlopPx() {
  return tapSlopForProfile(current_profile);
}

int swipeEdgePx() {
  int v = SCREEN_WIDTH / 12;
  if (v < 18) v = 18;
  if (v > 40) v = 40;
  return v;
}

bool isHorizontalSwipe(int dx, int dy) {
  int adx = abs(dx);
  int ady = abs(dy);
  return (adx >= swipeThresholdPx() && adx > (ady * 4) / 3);
}

bool readTouchPoint(uint16_t& tx, uint16_t& ty) {
  uint16_t x1 = 0, y1 = 0;
  if (!tft.getTouch(&x1, &y1)) {
    return false;
  }
  uint16_t x2 = 0, y2 = 0;
  if (!tft.getTouch(&x2, &y2)) {
    tx = x1;
    ty = y1;
    return true;
  }

  int dx = abs((int)x2 - (int)x1);
  int dy = abs((int)y2 - (int)y1);
  const int jitter = max(10, tapSlopPx() / 2);
  if (dx <= jitter && dy <= jitter) {
    tx = (uint16_t)(((int)x1 + (int)x2) / 2);
    ty = (uint16_t)(((int)y1 + (int)y2) / 2);
  } else {
    tx = x2;
    ty = y2;
  }
  return true;
}

bool handleEdgeSwipe(bool pressed, uint16_t tx, uint16_t ty) {
  static bool active = false;
  static bool handled = false;
  static uint16_t startX = 0;
  static uint16_t startY = 0;
  static uint16_t lastX = 0;
  static uint16_t lastY = 0;

  if (pressed) {
    if (!active) {
      noteUserActivity();
      int edge = swipeEdgePx();
      if (tx > edge && tx < (SCREEN_WIDTH - 1 - edge)) {
        return false;
      }
      active = true;
      handled = false;
      startX = tx;
      startY = ty;
    }
    lastX = tx;
    lastY = ty;
    if (!handled) {
      int dx = (int)lastX - (int)startX;
      int dy = (int)lastY - (int)startY;
      if (isHorizontalSwipe(dx, dy)) {
        if (dx < 0) {
          switchProfileRelative(1);
        } else {
          switchProfileRelative(-1);
        }
        handled = true;
      }
    }
    return true;
  }

  if (active) {
    if (!handled) {
      int dx = (int)lastX - (int)startX;
      int dy = (int)lastY - (int)startY;
      if (isHorizontalSwipe(dx, dy)) {
        if (dx < 0) {
          switchProfileRelative(1);
        } else {
          switchProfileRelative(-1);
        }
        handled = true;
      }
    }
    bool wasHandled = handled;
    active = false;
    handled = false;
    return wasHandled;
  }
  return false;
}

bool handleSwipeAny(bool pressed, uint16_t tx, uint16_t ty) {
  static bool active = false;
  static bool handled = false;
  static uint16_t startX = 0;
  static uint16_t startY = 0;
  static uint16_t lastX = 0;
  static uint16_t lastY = 0;

  if (pressed) {
    if (!active) {
      noteUserActivity();
      active = true;
      handled = false;
      startX = tx;
      startY = ty;
    }
    lastX = tx;
    lastY = ty;
    if (!handled) {
      int dx = (int)lastX - (int)startX;
      int dy = (int)lastY - (int)startY;
      if (isHorizontalSwipe(dx, dy)) {
        if (dx < 0) {
          switchProfileRelative(1);
        } else {
          switchProfileRelative(-1);
        }
        handled = true;
      }
    }
    return true;
  }

  if (active) {
    if (!handled) {
      int dx = (int)lastX - (int)startX;
      int dy = (int)lastY - (int)startY;
      if (isHorizontalSwipe(dx, dy)) {
        if (dx < 0) {
          switchProfileRelative(1);
        } else {
          switchProfileRelative(-1);
        }
        handled = true;
      }
    }
    bool wasHandled = handled;
    active = false;
    handled = false;
    return wasHandled;
  }
  return false;
}

void handleEdgeSwipeOnly() {
  uint16_t tx, ty;
  bool pressed = readTouchPoint(tx, ty);
  handleEdgeSwipe(pressed, tx, ty);
}

void handleSwipeAnyOnly() {
  uint16_t tx, ty;
  bool pressed = readTouchPoint(tx, ty);
  handleSwipeAny(pressed, tx, ty);
}

void handleTouch() {
  if (g_trans.active || g_screensaver_active) return;
  static bool touching = false;
  static bool swipeHandled = false;
  static bool moved = false;
  static int pressedIdx = -1;
  static uint32_t pressStartMs = 0;
  static uint16_t startX = 0;
  static uint16_t startY = 0;
  static uint16_t lastX = 0;
  static uint16_t lastY = 0;
  static uint32_t lastSendMs = 0;
  const uint32_t COOLDOWN_MS = 80;

  uint16_t tx, ty;
  bool pressed = readTouchPoint(tx, ty);

  if (pressed) {
    if (!touching) {
      noteUserActivity();
      touching = true;
      swipeHandled = false;
      moved = false;
      pressedIdx = -1;
      pressStartMs = millis();
      startX = tx;
      startY = ty;
      pressedIdx = gridIndexFromXY(current_profile, tx, ty);
      if (pressedIdx >= 0) {
        drawGridButtonByIndex(current_profile, pressedIdx, true);
      }
    }
    lastX = tx;
    lastY = ty;

    if (!swipeHandled) {
      int dx = (int)lastX - (int)startX;
      int dy = (int)lastY - (int)startY;
      int dragSlop = max(8, tapSlopPx());
      if (!moved && (abs(dx) > dragSlop || abs(dy) > dragSlop)) {
        moved = true;
      }
      if (isHorizontalSwipe(dx, dy)) {
        if (dx < 0) {
          switchProfileRelative(1);
        } else {
          switchProfileRelative(-1);
        }
        swipeHandled = true;
      }
    }
    return;
  }

  if (!touching) return;
  touching = false;

  if (pressedIdx >= 0) {
    drawGridButtonByIndex(current_profile, pressedIdx, false);
  }

  if (!swipeHandled) {
    int dx = (int)lastX - (int)startX;
    int dy = (int)lastY - (int)startY;
    if (isHorizontalSwipe(dx, dy)) {
      if (dx < 0) {
        switchProfileRelative(1);
      } else {
        switchProfileRelative(-1);
      }
      swipeHandled = true;
    }
  }

  int dx = (int)lastX - (int)startX;
  int dy = (int)lastY - (int)startY;
  int dragSlop = max(8, tapSlopPx());
  if (!moved && (abs(dx) > dragSlop || abs(dy) > dragSlop)) {
    moved = true;
  }
  if (swipeHandled || moved) {
    swipeHandled = false;
    moved = false;
    pressedIdx = -1;
    return;
  }

  uint32_t now = millis();
  if (now - lastSendMs < COOLDOWN_MS) return;
  if (now - pressStartMs < 15) return;

  int idx = gridIndexFromXY(current_profile, lastX, lastY);
  if (idx < 0) idx = pressedIdx;
  if (pressedIdx >= 0 && idx >= 0 && idx != pressedIdx && !moved) {
    idx = pressedIdx;
  }
  if (idx < 0) return;
  sendActionForIndex(idx);
  lastSendMs = now;
  pressedIdx = -1;
}

// --- MONITOR UI ---

struct StatCard {
  const char* title;
  float value_pct;  
  String primary;
  String secondary;
};

uint16_t colorForPct(float pct) {
  if (pct < 0) return TFT_DARKGREY;
  if (pct < 60) return TFT_GREEN;
  if (pct < 85) return TFT_YELLOW;
  return TFT_RED;
}

void drawCard(lgfx::LovyanGFX* gfx, int x, int y, int w, int h, const StatCard& card) {
  uint16_t bg = gfx->color565(18, 22, 30);
  uint16_t border = gfx->color565(60, 70, 90);

  gfx->fillRoundRect(x, y, w, h, 8, bg);
  gfx->drawRoundRect(x, y, w, h, 8, border);

  int pad = 8;

  gfx->setTextDatum(textdatum_t::top_left);
  gfx->setTextColor(gfx->color565(148, 163, 184), bg);
  setMediaFontMeta(gfx);
  gfx->drawString(card.title, x + pad, y + pad);

  gfx->setTextColor(gfx->color565(248, 250, 252), bg);
  setMediaFontTitle(gfx);
  gfx->setTextSize(0.95f);
  gfx->drawString(card.primary, x + pad, y + pad + 14);

  gfx->setTextColor(gfx->color565(148, 163, 184), bg);
  setMediaFontMeta(gfx);
  gfx->drawString(card.secondary, x + pad, y + pad + 36);

  int barH = 8;
  int barY = y + h - pad - barH;
  int barX = x + pad;
  int barW = w - 2 * pad;

  uint16_t barBg = gfx->color565(15, 23, 42);
  gfx->fillRoundRect(barX, barY, barW, barH, 4, barBg);

  if (card.value_pct >= 0) {
    float pct = card.value_pct;
    if (pct < 0) pct = 0;
    if (pct > 100) pct = 100;
    int filled = (int)(barW * (pct / 100.0f));
    uint16_t barColor = colorForPct(pct);
    gfx->fillRoundRect(barX, barY, filled, barH, 4, barColor);
  }
}
       
void drawMonitorScreen(bool fullRedraw) {
  lgfx::LovyanGFX* gfx = drawTarget();
  int W = gfx->width();
  int H = gfx->height();

  uint16_t bgScreen = gfx->color565(15, 23, 42);  // alebo TFT_BLACK, podľa toho čo chceš
  if (fullRedraw) {
    gfx->fillScreen(bgScreen);
  }

  int margin = 8;
  int gap = 6;

  int cardW = (W - 2 * margin - gap) / 2;
  int cardH = (H - 2 * margin - 2 * gap) / 3;

  char buf1[32], buf2[64];

  // CPU
  snprintf(buf1, sizeof(buf1), "%2.0f %%", g_cpu);
  snprintf(buf2, sizeof(buf2), "");
  StatCard cpu = { "CPU", g_cpu, buf1, buf2 };
  drawCard(gfx, margin, margin, cardW, cardH, cpu);

  // RAM
  snprintf(buf1, sizeof(buf1), "%2.0f %%", g_ram);
  StatCard ram = { "RAM", g_ram, buf1, "" };
  drawCard(gfx, margin + cardW + gap, margin, cardW, cardH, ram);

  // GPU
  float gpu_pct = (g_gpu < 0) ? -1.0f : g_gpu;
  snprintf(buf1, sizeof(buf1), (g_gpu < 0) ? "N/A" : "%2.0f %%", gpu_pct);
  StatCard gpu = { "GPU", gpu_pct, buf1, "" };
  drawCard(gfx, margin, margin + cardH + gap, cardW, cardH, gpu);

  // DISK
  float disk_pct = (g_disk / 50.0f) * 100.0f;
  snprintf(buf1, sizeof(buf1), "%.1f MB/s", g_disk);
  StatCard disk = { "DISK", disk_pct, buf1, "" };
  drawCard(gfx, margin + cardW + gap, margin + cardH + gap, cardW, cardH, disk);

  // NET
  float net_pct = (g_net / 10.0f) * 100.0f;
  snprintf(buf1, sizeof(buf1), "%.1f MB/s", g_net);
  StatCard net = { "NET", net_pct, buf1, "" };
  drawCard(gfx, margin, margin + 2 * (cardH + gap), cardW, cardH, net);

  // FPS
  snprintf(buf1, sizeof(buf1), (g_fps < 0) ? "N/A" : "%.0f FPS", g_fps);
  StatCard fps = { "FPS", -1.0f, buf1, "" };
  drawCard(gfx, margin + cardW + gap, margin + 2 * (cardH + gap), cardW, cardH, fps);
  pushIfSprite();
}
       
// Orezanie textu tak, aby sa vošiel do maxWidth, pridá "..."
String ellipsizeToWidth(lgfx::LovyanGFX* gfx, const String& src, int maxWidth) {
  if (gfx->textWidth(src) <= maxWidth) return src;

  String base = src;
  String suffix = "...";

  while (base.length() > 1 && gfx->textWidth(base + suffix) > maxWidth) {
    base.remove(base.length() - 1);
  }
  return base + suffix;
}

uint16_t blend565(lgfx::LovyanGFX* gfx, uint16_t c0, uint16_t c1, float t) {
  if (t <= 0.0f) return c0;
  if (t >= 1.0f) return c1;
  uint8_t r0 = ((c0 >> 11) & 0x1F) * 255 / 31;
  uint8_t g0 = ((c0 >>  5) & 0x3F) * 255 / 63;
  uint8_t b0 = ( c0        & 0x1F) * 255 / 31;
  uint8_t r1 = ((c1 >> 11) & 0x1F) * 255 / 31;
  uint8_t g1 = ((c1 >>  5) & 0x3F) * 255 / 63;
  uint8_t b1 = ( c1        & 0x1F) * 255 / 31;
  uint8_t r = (uint8_t)(r0 + (r1 - r0) * t);
  uint8_t g = (uint8_t)(g0 + (g1 - g0) * t);
  uint8_t b = (uint8_t)(b0 + (b1 - b0) * t);
  return gfx->color565(r, g, b);
}

uint16_t mixLight(lgfx::LovyanGFX* gfx, uint16_t c, float t) {
  return blend565(gfx, c, gfx->color565(255, 255, 255), t);
}

uint16_t mixDark(lgfx::LovyanGFX* gfx, uint16_t c, float t) {
  return blend565(gfx, c, gfx->color565(0, 0, 0), t);
}

void drawDashedRect(lgfx::LovyanGFX* gfx, int x, int y, int w, int h, int dash, int gap, uint16_t col) {
  for (int i = x; i < x + w; i += dash + gap) {
    int len = min(dash, x + w - i);
    gfx->drawFastHLine(i, y, len, col);
    gfx->drawFastHLine(i, y + h - 1, len, col);
  }
  for (int j = y; j < y + h; j += dash + gap) {
    int len = min(dash, y + h - j);
    gfx->drawFastVLine(x, j, len, col);
    gfx->drawFastVLine(x + w - 1, j, len, col);
  }
}

void drawPlusIcon(lgfx::LovyanGFX* gfx, int cx, int cy, int size, uint16_t col) {
  int half = size / 2;
  gfx->fillRect(cx - half, cy - 1, size, 2, col);
  gfx->fillRect(cx - 1, cy - half, 2, size, col);
}

void drawSpeakerIcon(lgfx::LovyanGFX* gfx, int cx, int cy, uint16_t col) {
  gfx->fillRect(cx - 9, cy - 4, 4, 8, col);
  gfx->fillTriangle(cx - 5, cy - 6, cx + 4, cy, cx - 5, cy + 6, col);
}
       
// jednoduché Spotify logo – tmavý kruh + 3 zelené vlny
void drawSpotifyGlyph(lgfx::LovyanGFX* gfx, int cx, int cy) {
  uint16_t circleBg  = gfx->color565(0, 0, 0);        // čierne pozadie bubbliny
  uint16_t waveColor = gfx->color565(30, 215, 96);    // Spotify zelená (#1DB954 približne)

  // základný kruh
  gfx->fillSmoothCircle(cx, cy, 16, circleBg);

  // tri oblúky
  gfx->drawArc(cx, cy, 11, 10, 210, 330, waveColor);
  gfx->drawArc(cx, cy,  9,  8, 210, 330, waveColor);
  gfx->drawArc(cx, cy,  7,  6, 210, 330, waveColor);
}


// --- MEDIA UI (fullscreen, Spotify-like buttons) ---
void splitTitleArtist(const String& src, String& title, String& artist) {
  title = src;
  artist = "";
  int idx = src.indexOf(" - ");
  if (idx < 0) idx = src.indexOf(" – ");
  if (idx > 0) {
    title = src.substring(0, idx);
    artist = src.substring(idx + 3);
  }
  title.trim();
  artist.trim();
}

bool wrapTwoLines(lgfx::LovyanGFX* gfx, const String& text, int maxW, String& line1, String& line2) {
  line1 = "";
  line2 = "";
  int len = text.length();
  int i = 0;
  int line = 0;
  while (i < len) {
    while (i < len && text[i] == ' ') i++;
    int j = i;
    while (j < len && text[j] != ' ') j++;
    String word = text.substring(i, j);
    if (word.length() == 0) break;
    if (gfx->textWidth(word) > maxW) return true;
    String* cur = (line == 0) ? &line1 : &line2;
    String candidate = cur->length() ? (*cur + " " + word) : word;
    if (gfx->textWidth(candidate) <= maxW) {
      *cur = candidate;
    } else {
      if (line == 0) {
        line = 1;
        cur = &line2;
        if (gfx->textWidth(word) <= maxW) {
          *cur = word;
        } else {
          return true;
        }
      } else {
        return true;
      }
    }
    i = j + 1;
  }
  return false;
}

void drawMediaIfNeeded(bool forceFull = false) {
  if (!g_allow_draw && !forceFull) return;
  static MediaSource lastSource   = MediaSource::MEDIA_GENERIC;
  static String      lastTrack    = "";
  static bool        lastPlaying  = false;
  static int         lastPosRaw   = -1;
  static int         lastDur      = -1;
  static float       lastDrawPos  = -1.0f;
  static uint32_t    lastAnimMs   = 0;
  static uint32_t    lastPosMs    = 0;
  static int         marqueeOffset = 0;
  static int         marqueeTextW  = 0;
  static uint32_t    marqueeLastMs = 0;
  static float       playBlend    = 0.0f;
  static bool        firstDraw    = true;

  lgfx::LovyanGFX* gfx = drawTarget();
  uint32_t nowMs = millis();

  int W = gfx->width();
  int H = gfx->height();

  int marginX   = 20;
  int headerH   = (H * 11) / 100;
  int infoTop   = headerH + 12;

  int btnSize   = 54;
  int controlsY = H - btnSize - 10;
  int centerX   = W / 2;
  int prevX     = centerX - btnSize - 46;
  int playX     = centerX - (btnSize / 2);
  int nextX     = centerX + 46;

  int barH      = 8;
  int barY      = controlsY - 22;
  int barX      = marginX;
  int barW      = W - 2 * marginX;

  bool marqueeActive = false;
  int titleMaxW = W - 2 * marginX;
  String title, artist;
  splitTitleArtist(g_media.track, title, artist);
  if (title.length() == 0) title = "—";
  if (!g_media.isPlaying && (title == "—" || title.length() == 0)) {
    title = "Nic nehra";
    artist = "";
  }

  setMediaFontTitle(gfx);
  String line1, line2;
  bool overflow = wrapTwoLines(gfx, title, titleMaxW, line1, line2);
  if (overflow && g_spriteReady) {
    marqueeActive = true;
    line1 = title;
    line2 = "";
  } else if (overflow) {
    line1 = ellipsizeToWidth(gfx, title, titleMaxW);
    line2 = "";
  }

  uint16_t accentHi   = mediaAccentColor(g_media.source);
  uint16_t accentLo   = mediaInactiveColor(g_media.source);
  bool playing = g_media.isPlaying;
  bool playDirty = (playing != lastPlaying);
  float targetBlend = playing ? 1.0f : 0.0f;
  if (firstDraw) {
    playBlend = targetBlend;
  }
  float blendDiff = targetBlend - playBlend;
  if (blendDiff < 0.0f) blendDiff = -blendDiff;
  bool blendDirty = blendDiff > 0.01f;

  bool animate = marqueeActive || playing || blendDirty;
  bool animTick = animate && (nowMs - lastAnimMs >= 50);
  if (animTick) lastAnimMs = nowMs;
  if ((animTick || playDirty) && blendDirty) {
    playBlend += (targetBlend - playBlend) * 0.22f;
    if (playBlend < 0.0f) playBlend = 0.0f;
    if (playBlend > 1.0f) playBlend = 1.0f;
  }

  uint16_t bgPlaying   = mediaBackgroundColor(g_media.source);
  uint16_t headerPlay  = mediaHeaderColor(g_media.source);
  uint16_t bgPaused    = blend565(gfx, bgPlaying, gfx->color565(0, 0, 0), 0.12f);
  uint16_t headerPause = blend565(gfx, headerPlay, gfx->color565(0, 0, 0), 0.12f);
  uint16_t bgScreen    = blend565(gfx, bgPaused, bgPlaying, playBlend);
  uint16_t headerBg    = blend565(gfx, headerPause, headerPlay, playBlend);

  uint16_t headerText = gfx->color565(240, 255, 245);
  uint16_t subText    = gfx->color565(210, 230, 220);
  uint16_t iconCol    = blend565(gfx, gfx->color565(200, 210, 220), gfx->color565(255, 255, 255), playBlend);
  uint16_t barFill    = blend565(gfx, accentLo, accentHi, playBlend);
  uint16_t barBg      = accentLo;

  bool layoutDirty =
      firstDraw ||
      (g_media.source != lastSource) ||
      (g_media.track  != lastTrack);

  if (g_media.position_s != lastPosRaw) {
    lastPosRaw = g_media.position_s;
    lastPosMs = nowMs;
  }

  float drawPos = (float)lastPosRaw;
  if (playing) {
    drawPos = (float)lastPosRaw + (float)(nowMs - lastPosMs) / 1000.0f;
  }
  if (drawPos < 0.0f) drawPos = 0.0f;
  float dur = (float)max(1, g_media.duration_s);
  if (drawPos > dur) drawPos = dur;

  float diff = drawPos - lastDrawPos;
  if (diff < 0.0f) diff = -diff;
  bool progressDirty = layoutDirty || animTick || (dur != lastDur) || (diff > 0.05f);
  bool shouldDraw = forceFull || g_media.dirty || layoutDirty || animTick || playDirty || progressDirty;

  if (!shouldDraw) {
    return;
  }

  gfx->setTextWrap(false);
  gfx->setTextDatum(textdatum_t::top_left);
  gfx->setFont(UI_FONT);

  // background + header
  gfx->fillScreen(bgScreen);
  gfx->fillRect(0, 0, W, headerH, headerBg);

  // header: text
  setMediaFontMeta(gfx);
  gfx->setTextColor(headerText, headerBg);
  gfx->setCursor(marginX, (headerH - gfx->fontHeight()) / 2);
  gfx->print("Now Playing");

  // track title (max 2 lines)
  int titleY = infoTop;
  setMediaFontTitle(gfx);
  int titleLineH = gfx->fontHeight();
  if (!marqueeActive) {
    gfx->setTextColor(TFT_WHITE, bgScreen);
    if (line1.length()) {
      gfx->setCursor(marginX, titleY);
      gfx->print(line1);
    }
    if (line2.length()) {
      gfx->setCursor(marginX, titleY + titleLineH + 2);
      gfx->print(line2);
    }
  } else {
    int clipH = titleLineH + 2;
    gfx->fillRect(marginX, titleY, titleMaxW, clipH, bgScreen);
    gfx->setTextColor(TFT_WHITE, bgScreen);
    int scrollGap = 30;
    if (layoutDirty) {
      marqueeOffset = 0;
      marqueeLastMs = nowMs;
      marqueeTextW = gfx->textWidth(title);
    }
    if (marqueeTextW > 0) {
      uint32_t delta = nowMs - marqueeLastMs;
      int step = (int)((delta * 24) / 1000);
      if (step > 0) {
        marqueeOffset = (marqueeOffset + step) % (marqueeTextW + scrollGap);
        marqueeLastMs = nowMs;
      }
    }
    int cycle = marqueeTextW + scrollGap;
    int x1 = marginX - marqueeOffset;
    gfx->setClipRect(marginX, titleY, titleMaxW, clipH);
    gfx->setCursor(x1, titleY);
    gfx->print(title);
    gfx->setCursor(x1 + cycle, titleY);
    gfx->print(title);
    gfx->clearClipRect();
  }

  // artist line
  int lines = line2.length() ? 2 : 1;
  int artistY = titleY + (lines * titleLineH) + 6;
  setMediaFontBody(gfx);
  int artistLineH = gfx->fontHeight();
  if (artist.length() && (artistY + artistLineH) < barY - 6) {
    gfx->setTextColor(subText, bgScreen);
    gfx->setCursor(marginX, artistY);
    String artistEll = ellipsizeToWidth(gfx, artist, titleMaxW);
    gfx->print(artistEll);
  }

  // progress bar + time
  int barR = barH / 2;
  gfx->fillSmoothRoundRect(barX, barY, barW, barH, barR, barBg);
  float ratio = drawPos / dur;
  if (ratio < 0.0f) ratio = 0.0f;
  if (ratio > 1.0f) ratio = 1.0f;
  int fillW = (int)(barW * ratio);
  if (fillW > 0) {
    int fillR = barR;
    if (fillW < barH) fillR = fillW / 2;
    gfx->fillSmoothRoundRect(barX, barY, fillW, barH, fillR, barFill);
  }

  int curSec = (int)(drawPos + 0.5f);
  int curMin = curSec / 60;
  int curRem = curSec % 60;
  int totMin = (int)dur / 60;
  int totRem = (int)dur % 60;
  char buf[16];
  int timeY = barY + barH + 12;
  setMediaFontMeta(gfx);
  gfx->setTextColor(subText, bgScreen);

  gfx->fillRect(barX, timeY - 10, 48, 16, bgScreen);
  gfx->setCursor(barX, timeY);
  snprintf(buf, sizeof(buf), "%d:%02d", curMin, curRem);
  gfx->print(buf);

  int rightX = barX + barW - 48;
  gfx->fillRect(rightX, timeY - 10, 48, 16, bgScreen);
  gfx->setCursor(rightX, timeY);
  snprintf(buf, sizeof(buf), "%d:%02d", totMin, totRem);
  gfx->print(buf);

  // controls
  // PREVIOUS
  {
    int cx = prevX + btnSize / 2;
    int cy = controlsY + btnSize / 2;
    int barWprev = 5;
    int barHprev = 22;
    gfx->fillRect(cx - 14, cy - barHprev/2, barWprev, barHprev, iconCol);
    gfx->fillTriangle(cx - 8, cy,
                      cx + 2, cy - 10,
                      cx + 2, cy + 10,
                      iconCol);
    gfx->fillTriangle(cx + 4, cy,
                      cx + 14, cy - 10,
                      cx + 14, cy + 10,
                      iconCol);
  }

  // PLAY / PAUSE
  if (playing) {
    int cx = playX + btnSize / 2;
    int cy = controlsY + btnSize / 2;
    int barWpp = 7;
    int barHpp = 26;
    gfx->fillRect(cx - 9, cy - barHpp/2, barWpp, barHpp, iconCol);
    gfx->fillRect(cx + 3, cy - barHpp/2, barWpp, barHpp, iconCol);
  } else {
    int cx = playX + btnSize / 2;
    int cy = controlsY + btnSize / 2;
    int h  = 26;
    gfx->fillTriangle(cx - 8, cy - h/2,
                      cx - 8, cy + h/2,
                      cx + 12, cy,
                      iconCol);
  }

  // NEXT
  {
    int cx = nextX + btnSize / 2;
    int cy = controlsY + btnSize / 2;
    gfx->fillTriangle(cx - 14, cy - 10,
                      cx - 14, cy + 10,
                      cx - 4,  cy,
                      iconCol);
    gfx->fillTriangle(cx - 2, cy - 10,
                      cx - 2, cy + 10,
                      cx + 8, cy,
                      iconCol);
    int barWn = 5;
    int barHn = 22;
    gfx->fillRect(cx + 10, cy - barHn/2, barWn, barHn, iconCol);
  }

  pushIfSprite();

  lastSource  = g_media.source;
  lastTrack   = g_media.track;
  lastPlaying = g_media.isPlaying;
  lastDur     = (int)dur;
  lastDrawPos = drawPos;
  g_media.dirty = false;
  firstDraw = false;
}

// --- MIXER UI ---
void drawMixerColumn(lgfx::LovyanGFX* gfx, int col, const char* title, int vol, bool muted, bool showMute, bool active, uint16_t accent) {
  const uint16_t textHi = gfx->color565(235, 240, 248);
  const uint16_t textDim = gfx->color565(160, 170, 186);

  int colW = SCREEN_WIDTH / 4;
  int x0 = col * colW;
  int cardPad = 8;
  int cardX = x0 + cardPad;
  int cardY = 8;
  int cardW = colW - (cardPad * 2);
  int cardH = SCREEN_HEIGHT - (cardPad * 2);

  uint16_t cardBg = gfx->color565(17, 21, 30);
  uint16_t cardBorder = gfx->color565(54, 63, 78);
  gfx->fillRoundRect(cardX, cardY, cardW, cardH, 12, cardBg);
  gfx->drawRoundRect(cardX, cardY, cardW, cardH, 12, cardBorder);

  int headerH = 36;
  int bottomH = showMute ? 54 : 44;
  int sliderX = cardX + 14;
  int sliderW = cardW - 28;
  int sliderTop = cardY + headerH;
  int sliderH = cardH - headerH - bottomH;

  uint16_t titleCol = active ? accent : textDim;
  setMediaFontBody(gfx);
  gfx->setTextDatum(textdatum_t::top_left);
  gfx->setTextColor(titleCol, cardBg);
  int dotX = cardX + 16;
  int dotY = cardY + 16;
  gfx->fillCircle(dotX, dotY, 5, titleCol);
  String titleStr = ellipsizeToWidth(gfx, String(title), cardW - 36);
  gfx->setCursor(cardX + 28, cardY + 8);
  gfx->print(titleStr);

  uint16_t trackBg = gfx->color565(22, 28, 40);
  uint16_t trackBorder = gfx->color565(55, 64, 80);
  gfx->fillRoundRect(sliderX, sliderTop, sliderW, sliderH, 12, trackBg);
  gfx->drawRoundRect(sliderX, sliderTop, sliderW, sliderH, 12, trackBorder);

  uint16_t dotCol = gfx->color565(90, 98, 116);
  int dotLineX = sliderX + sliderW / 2;
  for (int y = sliderTop + 10; y < sliderTop + sliderH - 10; y += 12) {
    gfx->fillCircle(dotLineX, y, 2, dotCol);
  }

  vol = max(0, min(100, vol));
  if (active) {
    int fillH = (sliderH * vol) / 100;
    if (fillH > 2) {
      int fillY = sliderTop + sliderH - fillH;
      int fillR = min(10, fillH / 2);
      uint16_t fillBase = mixDark(gfx, accent, 0.35f);
      gfx->fillRoundRect(sliderX + 2, fillY + 2, sliderW - 4, fillH - 2, fillR, fillBase);
      int bandH = min(24, fillH);
      uint16_t fillTop = mixLight(gfx, accent, 0.28f);
      gfx->fillRoundRect(sliderX + 2, fillY + 2, sliderW - 4, bandH, fillR, fillTop);
      gfx->drawRoundRect(sliderX + 2, fillY + 2, sliderW - 4, fillH - 2, fillR, mixLight(gfx, accent, 0.42f));
    }
  } else {
    uint16_t dashCol = gfx->color565(96, 108, 128);
    drawDashedRect(gfx, sliderX + 6, sliderTop + 6, sliderW - 12, sliderH - 12, 6, 6, dashCol);
  }

  char buf[8];
  gfx->setTextDatum(textdatum_t::top_center);
  setMediaFontMeta(gfx);
  if (active) {
    snprintf(buf, sizeof(buf), "%d%%", vol);
    gfx->setTextColor(textHi, cardBg);
    gfx->drawString(buf, x0 + colW / 2, sliderTop + sliderH + 6);
  } else {
    gfx->setTextColor(textDim, cardBg);
    gfx->drawString("0%", x0 + colW / 2, sliderTop + sliderH + 6);
  }

  if (showMute) {
    int muteH = 22;
    int muteY = cardY + cardH - 30;
    uint16_t muteBg = muted ? gfx->color565(200, 45, 45) : gfx->color565(40, 46, 58);
    gfx->fillRoundRect(cardX + 12, muteY, cardW - 24, muteH, 8, muteBg);
    gfx->setTextDatum(textdatum_t::middle_center);
    gfx->setTextColor(gfx->color565(248, 250, 252), muteBg);
    gfx->drawString(muted ? "Muted" : "Mute", x0 + colW / 2, muteY + (muteH / 2));
  } else if (!active) {
    int assignH = 22;
    int assignY = cardY + cardH - 30;
    uint16_t assignBg = gfx->color565(34, 40, 52);
    gfx->fillRoundRect(cardX + 12, assignY, cardW - 24, assignH, 8, assignBg);
    gfx->setTextDatum(textdatum_t::middle_center);
    gfx->setTextColor(textDim, assignBg);
    gfx->drawString("Empty", x0 + colW / 2, assignY + (assignH / 2));
  } else {
    int btnH = 20;
    int btnY = cardY + cardH - 28;
    uint16_t btnBg = gfx->color565(34, 40, 52);
    gfx->fillRoundRect(cardX + 16, btnY, cardW - 32, btnH, 8, btnBg);
    drawSpeakerIcon(gfx, x0 + colW / 2, btnY + (btnH / 2), textDim);
  }
}

void drawMixerVolumeOnly(lgfx::LovyanGFX* gfx, int col, int vol, bool active, uint16_t accent) {
  int colW = SCREEN_WIDTH / 4;
  int x0 = col * colW;
  int cardPad = 8;
  int cardX = x0 + cardPad;
  int cardY = 8;
  int cardW = colW - (cardPad * 2);
  int cardH = SCREEN_HEIGHT - (cardPad * 2);

  int headerH = 36;
  int bottomH = 54;
  int sliderX = cardX + 14;
  int sliderW = cardW - 28;
  int sliderTop = cardY + headerH;
  int sliderH = cardH - headerH - bottomH;

  uint16_t cardBg = gfx->color565(17, 21, 30);
  uint16_t trackBg = gfx->color565(22, 28, 40);
  uint16_t trackBorder = gfx->color565(55, 64, 80);

  // clear slider interior
  gfx->fillRoundRect(sliderX + 2, sliderTop + 2, sliderW - 4, sliderH - 4, 10, trackBg);
  gfx->drawRoundRect(sliderX, sliderTop, sliderW, sliderH, 12, trackBorder);

  uint16_t dotCol = gfx->color565(90, 98, 116);
  int dotLineX = sliderX + sliderW / 2;
  for (int y = sliderTop + 10; y < sliderTop + sliderH - 10; y += 12) {
    gfx->fillCircle(dotLineX, y, 2, dotCol);
  }

  vol = max(0, min(100, vol));
  if (active) {
    int fillH = (sliderH * vol) / 100;
    if (fillH > 2) {
      int fillY = sliderTop + sliderH - fillH;
      int fillR = min(10, fillH / 2);
      uint16_t fillBase = mixDark(gfx, accent, 0.35f);
      gfx->fillRoundRect(sliderX + 2, fillY + 2, sliderW - 4, fillH - 2, fillR, fillBase);
      int bandH = min(24, fillH);
      uint16_t fillTop = mixLight(gfx, accent, 0.28f);
      gfx->fillRoundRect(sliderX + 2, fillY + 2, sliderW - 4, bandH, fillR, fillTop);
      gfx->drawRoundRect(sliderX + 2, fillY + 2, sliderW - 4, fillH - 2, fillR, mixLight(gfx, accent, 0.42f));
    }
  } else {
    uint16_t dashCol = gfx->color565(96, 108, 128);
    drawDashedRect(gfx, sliderX + 6, sliderTop + 6, sliderW - 12, sliderH - 12, 6, 6, dashCol);
  }

  // percent text
  int pctY = sliderTop + sliderH + 6;
  gfx->fillRect(x0 + 6, pctY - 2, colW - 12, 18, cardBg);
  gfx->setTextDatum(textdatum_t::top_center);
  setMediaFontMeta(gfx);
  if (active) {
    char buf[8];
    snprintf(buf, sizeof(buf), "%d%%", vol);
    gfx->setTextColor(gfx->color565(235, 240, 248), cardBg);
    gfx->drawString(buf, x0 + colW / 2, pctY);
  } else {
    gfx->setTextColor(gfx->color565(160, 170, 186), cardBg);
    gfx->drawString("0%", x0 + colW / 2, pctY);
  }
}

void drawMixerScreen() {
  lgfx::LovyanGFX* gfx = drawTarget();
  const uint16_t bgTop = gfx->color565(14, 18, 26);
  gfx->fillRect(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT, bgTop);

  drawMixerColumn(gfx, 0, "Mic", g_mixer.mic, g_mixer.micMuted, true, true, gfx->color565(239, 68, 68));
  drawMixerColumn(gfx, 1, "Master", g_mixer.master, g_mixer.masterMuted, true, true, gfx->color565(34, 197, 94));

  const char* app1Title = (g_mixer.apps[0].active && g_mixer.apps[0].name[0]) ? g_mixer.apps[0].name : "Empty";
  drawMixerColumn(gfx, 2, app1Title, g_mixer.apps[0].volume, g_mixer.apps[0].muted, g_mixer.apps[0].active, g_mixer.apps[0].active, gfx->color565(22, 163, 74));

  const char* app2Title = (g_mixer.apps[1].active && g_mixer.apps[1].name[0]) ? g_mixer.apps[1].name : "Empty";
  drawMixerColumn(gfx, 3, app2Title, g_mixer.apps[1].volume, g_mixer.apps[1].muted, g_mixer.apps[1].active, g_mixer.apps[1].active, gfx->color565(100, 116, 139));
  pushIfSprite();
}

void clearMixerColumn(lgfx::LovyanGFX* gfx, int col, uint16_t bg) {
  int colW = SCREEN_WIDTH / 4;
  int x0 = col * colW;
  (void)bg;
  const uint16_t bgTop = gfx->color565(14, 18, 26);
  gfx->fillRect(x0, 0, colW, SCREEN_HEIGHT, bgTop);
}

void drawMixerIfNeeded() {
  if (!g_allow_draw) return;
  static bool firstDraw = true;
  static int lastMaster = -1;
  static bool lastMasterMuted = false;
  static int lastMic = -1;
  static bool lastMicMuted = false;
  static int lastApp1Vol = -1;
  static bool lastApp1Muted = false;
  static bool lastApp1Active = false;
  static char lastApp1Name[16] = "";
  static int lastApp2Vol = -1;
  static bool lastApp2Muted = false;
  static bool lastApp2Active = false;
  static char lastApp2Name[16] = "";
  static float dispMaster = -1.0f;
  static float dispMic = -1.0f;
  static float dispApp1 = -1.0f;
  static float dispApp2 = -1.0f;
  static float lastDispMaster = -1.0f;
  static float lastDispMic = -1.0f;
  static float lastDispApp1 = -1.0f;
  static float lastDispApp2 = -1.0f;
  static uint32_t lastAnimMs = 0;

  lgfx::LovyanGFX* gfx = drawTarget();
  const uint16_t bg = gfx->color565(10, 12, 18);

  uint32_t nowMs = millis();
  bool animTick = (nowMs - lastAnimMs) >= 33;
  if (animTick) lastAnimMs = nowMs;

  auto smooth = [](float cur, float target) {
    if (cur < 0.0f) return target;
    float diff = target - cur;
    float adiff = diff < 0.0f ? -diff : diff;
    if (adiff < 0.3f) return target;
    cur += diff * 0.28f;
    float ndiff = target - cur;
    float andiff = ndiff < 0.0f ? -ndiff : ndiff;
    if (andiff < 0.9f) cur = target;
    return cur;
  };

  const char* app1Title = (g_mixer.apps[0].active && g_mixer.apps[0].name[0]) ? g_mixer.apps[0].name : "Empty";
  const char* app2Title = (g_mixer.apps[1].active && g_mixer.apps[1].name[0]) ? g_mixer.apps[1].name : "Empty";

  int tMaster = g_mixer.master;
  int tMic = g_mixer.mic;
  int tApp1 = g_mixer.apps[0].active ? g_mixer.apps[0].volume : 0;
  int tApp2 = g_mixer.apps[1].active ? g_mixer.apps[1].volume : 0;

  if (firstDraw) {
    dispMaster = tMaster;
    dispMic = tMic;
    dispApp1 = tApp1;
    dispApp2 = tApp2;
  } else if (animTick) {
    dispMaster = smooth(dispMaster, (float)tMaster);
    dispMic = smooth(dispMic, (float)tMic);
    dispApp1 = smooth(dispApp1, (float)tApp1);
    dispApp2 = smooth(dispApp2, (float)tApp2);
  }

  bool animating =
      (dispMaster >= 0.0f && (int)dispMaster != tMaster) ||
      (dispMic >= 0.0f && (int)dispMic != tMic) ||
      (dispApp1 >= 0.0f && (int)dispApp1 != tApp1) ||
      (dispApp2 >= 0.0f && (int)dispApp2 != tApp2);

  if (!g_mixer.dirty && !g_mixerFullRedraw && !firstDraw && !animTick && !animating) return;

  bool col0Changed = false;
  bool col1Changed = false;
  bool app1Changed = false;
  bool app2Changed = false;

  bool full = g_mixerFullRedraw || firstDraw;
  if (full) {
    const uint16_t bgTop = gfx->color565(10, 13, 20);
    const uint16_t bgBot = gfx->color565(20, 26, 38);
    gfx->fillGradientRect(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT, bgTop, bgBot, lgfx::v1::VLINEAR);
    drawMixerColumn(gfx, 0, "Mic", (int)dispMic, g_mixer.micMuted, true, true, gfx->color565(239, 68, 68));
    drawMixerColumn(gfx, 1, "Master", (int)dispMaster, g_mixer.masterMuted, true, true, gfx->color565(34, 197, 94));
    drawMixerColumn(gfx, 2, app1Title, (int)dispApp1, g_mixer.apps[0].muted, g_mixer.apps[0].active, g_mixer.apps[0].active, gfx->color565(22, 163, 74));
    drawMixerColumn(gfx, 3, app2Title, (int)dispApp2, g_mixer.apps[1].muted, g_mixer.apps[1].active, g_mixer.apps[1].active, gfx->color565(100, 116, 139));
  } else {
    col0Changed =
        ((int)dispMic != (int)lastDispMic) ||
        (g_mixer.micMuted != lastMicMuted);
    if (col0Changed) {
      bool volOnly = (g_mixer.micMuted == lastMicMuted);
      if (volOnly) {
        drawMixerVolumeOnly(gfx, 0, (int)dispMic, true, gfx->color565(239, 68, 68));
      } else {
        clearMixerColumn(gfx, 0, bg);
        drawMixerColumn(gfx, 0, "Mic", (int)dispMic, g_mixer.micMuted, true, true, gfx->color565(239, 68, 68));
      }
    }

    col1Changed =
        ((int)dispMaster != (int)lastDispMaster) ||
        (g_mixer.masterMuted != lastMasterMuted);
    if (col1Changed) {
      bool volOnly = (g_mixer.masterMuted == lastMasterMuted);
      if (volOnly) {
        drawMixerVolumeOnly(gfx, 1, (int)dispMaster, true, gfx->color565(34, 197, 94));
      } else {
        clearMixerColumn(gfx, 1, bg);
        drawMixerColumn(gfx, 1, "Master", (int)dispMaster, g_mixer.masterMuted, true, true, gfx->color565(34, 197, 94));
      }
    }

    app1Changed =
        (g_mixer.apps[0].active != lastApp1Active) ||
        ((int)dispApp1 != (int)lastDispApp1) ||
        (g_mixer.apps[0].muted != lastApp1Muted) ||
        (strncmp(g_mixer.apps[0].name, lastApp1Name, sizeof(lastApp1Name)) != 0);
    if (app1Changed) {
      bool volOnly =
          (g_mixer.apps[0].active == lastApp1Active) &&
          (g_mixer.apps[0].muted == lastApp1Muted) &&
          (strncmp(g_mixer.apps[0].name, lastApp1Name, sizeof(lastApp1Name)) == 0);
      if (volOnly) {
        drawMixerVolumeOnly(gfx, 2, (int)dispApp1, g_mixer.apps[0].active, gfx->color565(22, 163, 74));
      } else {
        clearMixerColumn(gfx, 2, bg);
        drawMixerColumn(gfx, 2, app1Title, (int)dispApp1, g_mixer.apps[0].muted, g_mixer.apps[0].active, g_mixer.apps[0].active, gfx->color565(22, 163, 74));
      }
    }

    app2Changed =
        (g_mixer.apps[1].active != lastApp2Active) ||
        ((int)dispApp2 != (int)lastDispApp2) ||
        (g_mixer.apps[1].muted != lastApp2Muted) ||
        (strncmp(g_mixer.apps[1].name, lastApp2Name, sizeof(lastApp2Name)) != 0);
    if (app2Changed) {
      bool volOnly =
          (g_mixer.apps[1].active == lastApp2Active) &&
          (g_mixer.apps[1].muted == lastApp2Muted) &&
          (strncmp(g_mixer.apps[1].name, lastApp2Name, sizeof(lastApp2Name)) == 0);
      if (volOnly) {
        drawMixerVolumeOnly(gfx, 3, (int)dispApp2, g_mixer.apps[1].active, gfx->color565(100, 116, 139));
      } else {
        clearMixerColumn(gfx, 3, bg);
        drawMixerColumn(gfx, 3, app2Title, (int)dispApp2, g_mixer.apps[1].muted, g_mixer.apps[1].active, g_mixer.apps[1].active, gfx->color565(100, 116, 139));
      }
    }
  }

  if (g_spriteReady) {
    bool needsPush = full || col0Changed || col1Changed || app1Changed || app2Changed;
    if (needsPush) {
      g_sprite.pushSprite(0, 0);
    }
  }

  lastMaster = g_mixer.master;
  lastMasterMuted = g_mixer.masterMuted;
  lastMic = g_mixer.mic;
  lastMicMuted = g_mixer.micMuted;
  lastApp1Vol = g_mixer.apps[0].volume;
  lastApp1Muted = g_mixer.apps[0].muted;
  lastApp1Active = g_mixer.apps[0].active;
  strncpy(lastApp1Name, g_mixer.apps[0].name, sizeof(lastApp1Name) - 1);
  lastApp1Name[sizeof(lastApp1Name) - 1] = '\0';
  lastApp2Vol = g_mixer.apps[1].volume;
  lastApp2Muted = g_mixer.apps[1].muted;
  lastApp2Active = g_mixer.apps[1].active;
  strncpy(lastApp2Name, g_mixer.apps[1].name, sizeof(lastApp2Name) - 1);
  lastApp2Name[sizeof(lastApp2Name) - 1] = '\0';
  lastDispMaster = dispMaster;
  lastDispMic = dispMic;
  lastDispApp1 = dispApp1;
  lastDispApp2 = dispApp2;

  g_mixerFullRedraw = false;
  g_mixer.dirty = false;
  firstDraw = false;
}

void handleMixerTouch() {
  static bool touching = false;
  static bool swipeHandled = false;
  static bool moved = false;
  static uint16_t startX = 0;
  static uint16_t startY = 0;
  static uint16_t lastX = 0;
  static uint16_t lastY = 0;
  static uint32_t lastSendMs = 0;
  const uint32_t COOLDOWN_MS = 80;

  uint16_t tx, ty;
  bool pressed = readTouchPoint(tx, ty);

  if (pressed) {
    if (!touching) {
      noteUserActivity();
      touching = true;
      swipeHandled = false;
      moved = false;
      startX = tx;
      startY = ty;
    }
    lastX = tx;
    lastY = ty;
    int dx = (int)lastX - (int)startX;
    int dy = (int)lastY - (int)startY;
    if (!swipeHandled && isHorizontalSwipe(dx, dy)) {
      if (dx < 0) {
        switchProfileRelative(1);
      } else {
        switchProfileRelative(-1);
      }
      swipeHandled = true;
    }
    if (abs(dx) > tapSlopPx() || abs(dy) > tapSlopPx()) {
      moved = true;
    }
  } else {
    if (touching && !swipeHandled) {
      int dx = (int)lastX - (int)startX;
      int dy = (int)lastY - (int)startY;
      if (isHorizontalSwipe(dx, dy)) {
        if (dx < 0) {
          switchProfileRelative(1);
        } else {
          switchProfileRelative(-1);
        }
        swipeHandled = true;
      }
    }
    touching = false;
    swipeHandled = false;
    moved = false;
    return;
  }

  if (swipeHandled) return;

  int dx = (int)lastX - (int)startX;
  int dy = (int)lastY - (int)startY;
  bool horizontalIntent = (abs(dx) > (abs(dy) * 12) / 10) && (abs(dx) > tapSlopPx());
  if (horizontalIntent) {
    // horizontal swipe intent -> don't change volume
    return;
  }

  uint32_t now = millis();
  if (now - lastSendMs < COOLDOWN_MS) return;

  int colW = SCREEN_WIDTH / 4;
  int col = tx / colW;
  if (col < 0 || col > 3) return;

  int cardY = 8;
  int cardH = SCREEN_HEIGHT - 16;
  int headerH = 36;
  int bottomH = 54;
  int sliderTop = cardY + headerH;
  int sliderH = cardH - headerH - bottomH;
  int muteY = cardY + cardH - 30;
  int muteH = 22;

  bool app1Active = g_mixer.apps[0].active;
  bool app2Active = g_mixer.apps[1].active;
  bool muteAllowed =
      (col == 0) ||
      (col == 1) ||
      (col == 2 && app1Active) ||
      (col == 3 && app2Active);

  if (muteAllowed && ty >= muteY && ty <= (muteY + muteH)) {
    if (col == 0) {
      g_mixer.micMuted = !g_mixer.micMuted;
      Serial.print("MIX:MICM="); Serial.println(g_mixer.micMuted ? 1 : 0);
    } else if (col == 1) {
      g_mixer.masterMuted = !g_mixer.masterMuted;
      Serial.print("MIX:MM="); Serial.println(g_mixer.masterMuted ? 1 : 0);
    } else if (col == 2) {
      g_mixer.apps[0].muted = !g_mixer.apps[0].muted;
      Serial.print("MIX:APP1M="); Serial.println(g_mixer.apps[0].muted ? 1 : 0);
    } else if (col == 3) {
      g_mixer.apps[1].muted = !g_mixer.apps[1].muted;
      Serial.print("MIX:APP2M="); Serial.println(g_mixer.apps[1].muted ? 1 : 0);
    }
    g_mixer.dirty = true;
    lastSendMs = now;
    return;
  }

  if (ty < sliderTop || ty > (sliderTop + sliderH)) return;
  int pct = 100 - ((ty - sliderTop) * 100) / sliderH;
  pct = max(0, min(100, pct));

  if (col == 0) {
    g_mixer.mic = pct;
    Serial.print("MIX:MIC="); Serial.println(pct);
  } else if (col == 1) {
    g_mixer.master = pct;
    Serial.print("MIX:MASTER="); Serial.println(pct);
  } else if (col == 2 && g_mixer.apps[0].active) {
    g_mixer.apps[0].volume = pct;
    Serial.print("MIX:APP1="); Serial.println(pct);
  } else if (col == 3 && g_mixer.apps[1].active) {
    g_mixer.apps[1].volume = pct;
    Serial.print("MIX:APP2="); Serial.println(pct);
  }

  g_mixer.dirty = true;
  lastSendMs = now;
}


// touch oblasti musia sedieť s tým, čo sme hore kreslili
void handleMediaTouch() {
  static uint32_t lastSendMs = 0;
  const uint32_t COOLDOWN_MS = 90;

  uint16_t tx, ty;
  bool pressed = readTouchPoint(tx, ty);
  if (handleEdgeSwipe(pressed, tx, ty)) return;

  static bool touching = false;
  static bool swipeHandled = false;
  static bool moved = false;
  static bool startedOnControl = false;
  static int pressedControl = 0;
  static uint16_t startX = 0;
  static uint16_t startY = 0;
  static uint16_t lastX = 0;
  static uint16_t lastY = 0;

  int W = tft.width();
  int H = tft.height();
  int btnSize   = 54;
  int controlsY = H - btnSize - 10;
  int centerX   = W / 2;
  int prevX     = centerX - btnSize - 46;
  int playX     = centerX - (btnSize / 2);
  int nextX     = centerX + 46;

  auto inRect = [](int x, int y, int rx, int ry, int rw, int rh) {
    return (x >= rx && x < rx + rw && y >= ry && y < ry + rh);
  };

  auto isControlHit = [&](int x, int y) {
    return inRect(x, y, prevX, controlsY, btnSize, btnSize) ||
           inRect(x, y, playX, controlsY, btnSize, btnSize) ||
           inRect(x, y, nextX, controlsY, btnSize, btnSize);
  };

  if (pressed) {
    if (!touching) {
      noteUserActivity();
      touching = true;
      swipeHandled = false;
      moved = false;
      startX = tx;
      startY = ty;
      startedOnControl = isControlHit(tx, ty);
      pressedControl = 0;
      if (startedOnControl) {
        int hx = -1;
        if (inRect(tx, ty, prevX, controlsY, btnSize, btnSize)) { pressedControl = 1; hx = prevX; }
        else if (inRect(tx, ty, playX, controlsY, btnSize, btnSize)) { pressedControl = 2; hx = playX; }
        else if (inRect(tx, ty, nextX, controlsY, btnSize, btnSize)) { pressedControl = 3; hx = nextX; }
        if (hx >= 0) {
          uint16_t outline = tft.color565(220, 220, 220);
          drawTarget()->drawRoundRect(hx, controlsY, btnSize, btnSize, 10, outline);
          pushIfSprite();
        }
      }
    }
    lastX = tx;
    lastY = ty;

    int dx = (int)lastX - (int)startX;
    int dy = (int)lastY - (int)startY;
    if (!moved && (abs(dx) > tapSlopPx() || abs(dy) > tapSlopPx())) {
      moved = true;
    }
    if (!swipeHandled && !startedOnControl && isHorizontalSwipe(dx, dy)) {
      if (dx < 0) {
        switchProfileRelative(1);
      } else {
        switchProfileRelative(-1);
      }
      swipeHandled = true;
    }
    return;
  }

  if (!touching) return;
  touching = false;

  if (!swipeHandled && !startedOnControl) {
    int dx = (int)lastX - (int)startX;
    int dy = (int)lastY - (int)startY;
    if (isHorizontalSwipe(dx, dy)) {
      if (dx < 0) {
        switchProfileRelative(1);
      } else {
        switchProfileRelative(-1);
      }
      swipeHandled = true;
    }
  }

  if (swipeHandled || moved) {
    swipeHandled = false;
    moved = false;
    if (pressedControl != 0) {
      g_media.dirty = true;
      pressedControl = 0;
    }
    startedOnControl = false;
    return;
  }

  if (!isControlHit(lastX, lastY)) {
    if (pressedControl != 0) {
      g_media.dirty = true;
      pressedControl = 0;
    }
    startedOnControl = false;
    return;
  }

  uint32_t now = millis();
  if (now - lastSendMs < COOLDOWN_MS) {
    if (pressedControl != 0) {
      g_media.dirty = true;
      pressedControl = 0;
    }
    startedOnControl = false;
    return;
  }

  if (inRect(lastX, lastY, prevX, controlsY, btnSize, btnSize)) {
    Serial.println("Previous");
  } else if (inRect(lastX, lastY, playX, controlsY, btnSize, btnSize)) {
    Serial.println("PlayMusic");
  } else if (inRect(lastX, lastY, nextX, controlsY, btnSize, btnSize)) {
    Serial.println("Next");
  } else {
    if (pressedControl != 0) {
      g_media.dirty = true;
      pressedControl = 0;
    }
    startedOnControl = false;
    return;
  }

  lastSendMs = now;
  if (pressedControl != 0) {
    g_media.dirty = true;
    pressedControl = 0;
  }
  startedOnControl = false;
}

// --- setup / loop ---
void setup(){
  delay(300);
  Serial.begin(115200);

  tft.init(); 
  tft.setRotation(1); 
  tft.setSwapBytes(true); 
  tft.fillScreen(TFT_BLACK);
  g_last_input_ms = millis();
  g_icon_anim_last_ms = g_last_input_ms;

#if defined(ARDUINO_ARCH_ESP32)
  bool psram_ok = psramFound();
  if (psram_ok) {
    g_sprite.setPsram(true);
    g_sprite.setColorDepth(16);
    g_spriteReady = (g_sprite.createSprite(SCREEN_WIDTH, SCREEN_HEIGHT) != nullptr);
    if (g_spriteReady) {
      g_sprite.setSwapBytes(true);
      g_sprite.fillScreen(TFT_BLACK);
      g_sprite.pushSprite(0, 0);
      g_transOld.setPsram(true);
      g_transOld.setColorDepth(16);
      g_transNew.setPsram(true);
      g_transNew.setColorDepth(16);
      g_transSpritesReady =
        (g_transOld.createSprite(SCREEN_WIDTH, SCREEN_HEIGHT) != nullptr) &&
        (g_transNew.createSprite(SCREEN_WIDTH, SCREEN_HEIGHT) != nullptr);
      if (g_transSpritesReady) {
        g_transOld.setSwapBytes(true);
        g_transNew.setSwapBytes(true);
      }
    }
  }
  Serial.printf("LOG:PSRAM: %s\n", psram_ok ? "yes" : "no");
  Serial.printf("LOG:Sprite: %s\n", g_spriteReady ? "on" : "off");
  Serial.printf("LOG:TransSprites: %s\n", g_transSpritesReady ? "on" : "off");
#endif

  bool fonts_ok = initMediaFonts();
  Serial.printf("LOG:Fonts: %s\n", fonts_ok ? "ok" : "fail");

  pinMode(ENC_A,INPUT_PULLUP); 
  pinMode(ENC_B,INPUT_PULLUP); 
  pinMode(ENC_SW,INPUT_PULLUP);
  pinMode(BTN_A,INPUT_PULLUP); 
  pinMode(BTN_B,INPUT_PULLUP);

#if defined(ARDUINO_ARCH_ESP32)
  analogReadResolution(12);
#endif

  _clearMixerSlot(g_mixer.apps[0]);
  _clearMixerSlot(g_mixer.apps[1]);
  strncpy(g_weather.label, "Weather", sizeof(g_weather.label) - 1);
  g_weather.label[sizeof(g_weather.label) - 1] = '\0';
  strncpy(g_weather.desc, "Waiting for update", sizeof(g_weather.desc) - 1);
  g_weather.desc[sizeof(g_weather.desc) - 1] = '\0';
  g_weather.animMs = millis();
  g_weather.animPhase = 0;
  g_weather.dirty = true;
  g_metric.cpu = -1.0f;
  g_metric.ram = -1.0f;
  g_metric.gpu = -1.0f;
  g_metric.gpuTemp = -1.0f;
  g_metric.fps = -1.0f;
  g_metric.net = -1.0f;
  g_metric.disk = -1.0f;
  g_metric.cpuGhz = -1.0f;
  g_metric.dirty = true;

  if (MONITOR_PROFILE_INDEX >= 0 && current_profile == MONITOR_PROFILE_INDEX) {
    g_monitorDirty = true;
    g_monitorFullRedraw = true;
  } else if (MEDIA_PROFILE_INDEX >= 0 && current_profile == MEDIA_PROFILE_INDEX) {
    g_media.dirty = true;
  } else if (MIXER_PROFILE_INDEX >= 0 && current_profile == MIXER_PROFILE_INDEX) {
    g_mixer.dirty = true;
    g_mixerFullRedraw = true;
  } else {
    redrawCurrentProfile();
  }
}

volatile int32_t _enc_edges = 0;
volatile uint8_t _enc_state = 0;
static inline uint8_t _readAB(){ return (digitalRead(ENC_A)<<1) | digitalRead(ENC_B); }
void IRAM_ATTR _enc_isr(){
  uint8_t s = _readAB(); uint8_t prev = _enc_state & 0x03; int8_t delta = 0;
  switch ((prev<<2)|s){
    case 0b0001: case 0b0111: case 0b1110: case 0b1000: delta=+1; break;
    case 0b0010: case 0b0100: case 0b1101: case 0b1011: delta=-1; break;
    default: break;
  }
  _enc_state = (s & 0x03);
  _enc_edges += delta;
}
  
       
void loop(){
  pumpSerialRx();
  unsigned long now=millis();
  updateClock(now);

  if (g_bench.active) {
    drawEspRenderBenchFrame(now);
    delay(1);
    return;
  }

  bool weatherVisible = profileHasWeatherWidget(current_profile);
  bool metricVisible = profileHasMetricWidget(current_profile);
  bool forceDraw =
      g_monitorDirty || g_monitorFullRedraw ||
      g_media.dirty ||
      g_mixer.dirty || g_mixerFullRedraw ||
      (weatherVisible && g_weather.dirty) ||
      (metricVisible && g_metric.dirty);
  g_allow_draw = forceDraw || (now - g_last_ui_ms >= UI_FRAME_MS);
  if (g_allow_draw) g_last_ui_ms = now;

  if (SCREENSAVER_ENABLED) {
    if (!g_screensaver_active && (now - g_last_input_ms) > SCREENSAVER_IDLE_MS) {
      g_screensaver_active = true;
      g_screensaver_last_draw = 0;
      drawScreensaver();
    }

    if (g_screensaver_active) {
      checkScreensaverWake();
      updateScreensaver(now);
      return;
    }
  }

  if (updateProfileTransition(now)) {
    return;
  }

  bool isMonitor = (MONITOR_PROFILE_INDEX >= 0 && current_profile == MONITOR_PROFILE_INDEX);
  bool isMedia   = (MEDIA_PROFILE_INDEX   >= 0 && current_profile == MEDIA_PROFILE_INDEX);
  bool isMixer   = (MIXER_PROFILE_INDEX   >= 0 && current_profile == MIXER_PROFILE_INDEX);

  // Encoder + tlačidlá vždy
  static bool enc_init=false;
  if(!enc_init){
    _enc_state=_readAB();
    attachInterrupt(digitalPinToInterrupt(ENC_A),_enc_isr,CHANGE);
    attachInterrupt(digitalPinToInterrupt(ENC_B),_enc_isr,CHANGE);
    enc_init=true;
  }
  static int32_t acc=0;
  noInterrupts();
  int32_t e=_enc_edges; _enc_edges=0;
  interrupts();
  if (e!=0){
    noteUserActivity();
    acc+=e;
    while(acc>=DETENT_STEPS){Serial.println("ENC:+1"); acc-=DETENT_STEPS;}
    while(acc<=-DETENT_STEPS){Serial.println("ENC:-1"); acc+=DETENT_STEPS;}
  }

  const unsigned long dMs=180;
  static bool lastEncSw=false;
  static bool lastBtnA=false;
  static bool lastBtnB=false;
  static unsigned long lastEncSwMs=0;
  static unsigned long lastBtnAMs=0;
  static unsigned long lastBtnBMs=0;
  bool curEncSw = (digitalRead(ENC_SW)==LOW);
  bool curBtnA  = (digitalRead(BTN_A)==LOW);
  bool curBtnB  = (digitalRead(BTN_B)==LOW);
  if (curEncSw && !lastEncSw && now-lastEncSwMs>dMs){
    noteUserActivity();
    Serial.println("{\"event\":\"BTN\",\"id\":\"ENC_SW\",\"state\":\"DOWN\"}");
    lastEncSwMs=now;
  }
  if (curBtnA && !lastBtnA && now-lastBtnAMs>dMs){
    noteUserActivity();
    Serial.println("{\"event\":\"BTN\",\"id\":\"A\",\"state\":\"DOWN\"}");
    lastBtnAMs=now;
  }
  if (curBtnB && !lastBtnB && now-lastBtnBMs>dMs){
    noteUserActivity();
    Serial.println("{\"event\":\"BTN\",\"id\":\"B\",\"state\":\"DOWN\"}");
    lastBtnBMs=now;
  }
  lastEncSw = curEncSw;
  lastBtnA = curBtnA;
  lastBtnB = curBtnB;

  if (isMonitor) {
    handleSwipeAnyOnly();
    static uint32_t lastRedraw = 0;
    if (g_allow_draw && g_monitorDirty && (now - lastRedraw) > 200) {
      drawMonitorScreen(g_monitorFullRedraw);
      g_monitorDirty = false;
      g_monitorFullRedraw = false;
      lastRedraw = now;
    }
  } else if (isMedia) {
    // nové sprite + diff kreslenie
    drawMediaIfNeeded(); 
    // dotyky stále riešime lokálne (Prev/Play/Next → serial)
    handleMediaTouch();
  } else if (isMixer) {
    drawMixerIfNeeded();
    handleMixerTouch();
  } else {
    bool widgetDirty = false;
    if (PROFILE_HAS_ANIM_ICONS[current_profile]) {
      uint16_t animStep = PROFILE_ICON_MIN_INTERVAL[current_profile];
      if (animStep < 40) animStep = 40;
      if ((now - g_icon_anim_last_ms) >= animStep) {
        g_icon_anim_last_ms = now;
        widgetDirty = true;
      }
    }
    if (weatherVisible) {
      if (g_weather.valid && (now - g_weather.animMs) >= 420) {
        g_weather.animMs = now;
        g_weather.animPhase = (uint8_t)((g_weather.animPhase + 1) % 3);
        g_weather.dirty = true;
      }
      if (g_weather.dirty) widgetDirty = true;
    }
    if (metricVisible && g_metric.dirty) {
      widgetDirty = true;
    }
    if (g_allow_draw && widgetDirty) {
      redrawCurrentProfile();
      if (weatherVisible) g_weather.dirty = false;
      if (metricVisible) g_metric.dirty = false;
    }
    handleTouch();        // klasický grid režim
  }

  delay(8);
}

''')

    # --- zápis súboru ---
    _write_text_if_changed(Path(export_path), "".join(lines), encoding="utf-8")
