"""检查环境差异"""
import sys
import asyncio
print(f"Python: {sys.version}")
print(f"Executable: {sys.executable}")
try:
    import aiohttp
    print(f"aiohttp: {aiohttp.__version__}")
except ImportError:
    print("aiohttp: NOT INSTALLED")
try:
    import brotli
    print("brotli: OK")
except ImportError:
    print("brotli: NOT INSTALLED")

# 检查默认 event loop policy
policy = asyncio.get_event_loop_policy()
print(f"asyncio policy: {type(policy).__name__}")

# 尝试创建 SelectorEventLoop
if hasattr(asyncio, "SelectorEventLoop"):
    loop = asyncio.SelectorEventLoop()
    print(f"SelectorEventLoop: OK ({type(loop).__name__})")
    loop.close()
