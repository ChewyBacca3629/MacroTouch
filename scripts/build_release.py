#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path


APP_NAME = "MacroTouch"
ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"
RELEASE_DIR = DIST_DIR / "release"
SPEC_FILE = ROOT / "MacroTouch.spec"
ISS_FILE = ROOT / "MacroTouch.iss"
LINUX_PACKAGING_DIR = ROOT / "packaging" / "linux"
DESKTOP_APP_DIR = ROOT / "desktop-app"


def current_platform() -> str:
    system = platform.system()
    if system == "Windows":
        return "windows"
    if system == "Linux":
        return "linux"
    raise SystemExit(f"Unsupported platform: {system}")


def normalized_arch() -> str:
    machine = platform.machine().lower()
    mapping = {
        "amd64": "x64",
        "x86_64": "x64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }
    return mapping.get(machine, machine or "unknown")


def built_executable_name(target_platform: str) -> str:
    return f"{APP_NAME}.exe" if target_platform == "windows" else APP_NAME


def slugify(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-z._-]+", "-", value.strip())
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "0.0.0-dev"


def normalize_version(raw: str | None) -> str:
    text = str(raw or "").strip()
    text = text.removeprefix("refs/tags/")
    if text.lower().startswith("v") and len(text) > 1:
        text = text[1:]
    return slugify(text)


def git_output(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def resolve_version(cli_version: str | None = None) -> str:
    candidates = [
        cli_version,
        os.environ.get("MACROTOUCH_VERSION"),
        os.environ.get("GITHUB_REF_NAME"),
        git_output(["describe", "--tags", "--abbrev=0"]),
    ]
    for candidate in candidates:
        version = normalize_version(candidate)
        if version and version != "0.0.0-dev":
            return version

    sha = slugify(git_output(["rev-parse", "--short", "HEAD"]) or "local")
    return f"0.0.0-dev-{sha}"


def clean_previous_outputs() -> None:
    for path in (
        DIST_DIR / APP_NAME,
        DIST_DIR / "installer",
        RELEASE_DIR,
        BUILD_DIR / APP_NAME,
        BUILD_DIR / "pyinstaller",
    ):
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)


def run_pyinstaller() -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(DIST_DIR),
            "--workpath",
            str(BUILD_DIR / "pyinstaller"),
            str(SPEC_FILE),
        ],
        cwd=ROOT,
        check=True,
    )


def app_dist_dir() -> Path:
    app_dir = DIST_DIR / APP_NAME
    if not app_dir.is_dir():
        raise SystemExit(f"Expected build output missing: {app_dir}")
    return app_dir


def ensure_expected_binary(target_platform: str) -> Path:
    binary = app_dist_dir() / built_executable_name(target_platform)
    if not binary.exists():
        raise SystemExit(
            f"Built application binary not found for {target_platform}: {binary}"
        )
    return binary


def ensure_release_dir() -> Path:
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    return RELEASE_DIR


def add_directory_to_zip(zip_path: Path, source_dir: Path) -> Path:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source_dir.rglob("*")):
            if path.is_dir():
                continue
            zf.write(path, arcname=str(path.relative_to(source_dir.parent)))
    return zip_path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksums(artifacts: list[Path], target_platform: str) -> Path:
    checksum_file = ensure_release_dir() / f"SHA256SUMS-{target_platform}.txt"
    lines = [f"{sha256_file(path)}  {path.name}" for path in sorted(artifacts)]
    checksum_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return checksum_file


def export_linux_icon(destination: Path) -> None:
    icon_source = DESKTOP_APP_DIR / "assets" / "MacroTouch.ico"
    if icon_source.exists():
        try:
            from PIL import Image

            with Image.open(icon_source) as image:
                image.save(destination)
                return
        except Exception:
            pass

    fallback = DESKTOP_APP_DIR / "assets" / "icons8-settings-100.png"
    if fallback.exists():
        shutil.copy2(fallback, destination)


def copy_license(stage_dir: Path) -> None:
    license_source = ROOT / "LICENSE"
    if license_source.exists():
        shutil.copy2(license_source, stage_dir / "LICENSE.txt")


def package_windows(version: str, arch: str) -> list[Path]:
    ensure_expected_binary("windows")
    release_dir = ensure_release_dir()
    artifacts: list[Path] = []

    iscc = shutil.which("iscc")
    if iscc and ISS_FILE.exists():
        subprocess.run(
            [
                iscc,
                f"/DMyAppName={APP_NAME}",
                f"/DMyAppVersion={version}",
                str(ISS_FILE),
            ],
            cwd=ROOT,
            check=True,
        )
        installer = DIST_DIR / "installer" / f"{APP_NAME}-Setup-{version}.exe"
        if installer.exists():
            shutil.copy2(installer, release_dir / installer.name)
            artifacts.append(release_dir / installer.name)
    else:
        portable_zip = release_dir / f"{APP_NAME}-{version}-windows-{arch}-portable.zip"
        add_directory_to_zip(portable_zip, app_dist_dir())
        artifacts.append(portable_zip)
        print("Inno Setup was not found; created portable ZIP only.", file=sys.stderr)

    return artifacts


def package_linux(version: str, arch: str) -> list[Path]:
    ensure_expected_binary("linux")
    release_dir = ensure_release_dir()
    artifacts: list[Path] = []

    bundle_name = f"{APP_NAME}-{version}-linux-{arch}"
    stage_dir = release_dir / bundle_name
    payload_dir = stage_dir / "payload"
    payload_app_dir = payload_dir / "app"

    if stage_dir.exists():
        shutil.rmtree(stage_dir, ignore_errors=True)

    payload_app_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(app_dist_dir(), payload_app_dir)
    shutil.copy2(LINUX_PACKAGING_DIR / "install.sh", stage_dir / "install.sh")
    shutil.copy2(LINUX_PACKAGING_DIR / "uninstall.sh", stage_dir / "uninstall.sh")
    shutil.copy2(
        LINUX_PACKAGING_DIR / "macrotouch.desktop.in",
        payload_dir / "macrotouch.desktop.in",
    )
    export_linux_icon(payload_dir / "macrotouch.png")
    copy_license(stage_dir)

    (stage_dir / "install.sh").chmod(0o755)
    (stage_dir / "uninstall.sh").chmod(0o755)

    tarball = release_dir / f"{bundle_name}.tar.gz"
    with tarfile.open(tarball, "w:gz") as tf:
        tf.add(stage_dir, arcname=bundle_name)
    shutil.rmtree(stage_dir, ignore_errors=True)
    artifacts.append(tarball)
    return artifacts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build release artifacts for the current platform."
    )
    parser.add_argument(
        "--platform",
        choices=["windows", "linux", "auto"],
        default="auto",
        help="Target platform. Defaults to the current host platform.",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Override release version. Defaults to tag or git describe output.",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Package the existing dist output without running PyInstaller.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target_platform = current_platform() if args.platform == "auto" else args.platform
    host_platform = current_platform()
    if target_platform != host_platform:
        raise SystemExit(
            f"Cross-platform packaging is not supported on this host "
            f"({host_platform} -> {target_platform})."
        )

    version = resolve_version(args.version)
    arch = normalized_arch()

    if not args.skip_build:
        clean_previous_outputs()
        run_pyinstaller()

    if target_platform == "windows":
        artifacts = package_windows(version, arch)
    elif target_platform == "linux":
        artifacts = package_linux(version, arch)
    else:
        raise SystemExit(f"Unsupported platform: {target_platform}")

    checksum_file = write_checksums(artifacts, target_platform)
    artifacts.append(checksum_file)

    print("Artifacts:")
    for artifact in artifacts:
        print(f" - {artifact.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
