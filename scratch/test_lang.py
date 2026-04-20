import locale
import sys

try:
    print(f"locale.getlocale(): {locale.getlocale()}")
except Exception as e:
    print(f"locale.getlocale() failed: {e}")

try:
    import ctypes
    windll = ctypes.windll.kernel32
    print(f"GetUserDefaultUILanguage: {windll.GetUserDefaultUILanguage()}")
except Exception as e:
    print(f"ctypes check failed: {e}")

print(f"sys.platform: {sys.platform}")
