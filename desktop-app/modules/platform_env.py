# core/platform_env.py
import platform

SYSTEM = platform.system()

IS_WINDOWS = SYSTEM == "Windows"
IS_LINUX = SYSTEM == "Linux"
IS_MAC = SYSTEM == "Darwin"
