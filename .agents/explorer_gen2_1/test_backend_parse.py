import re
import sys
import os

# Add services/api to path
sys.path.append(r"C:\Users\zyu33\Documents\Codex\2026-06-27\yi\outputs\greenvideo-ai\services\api")

from app.services.security import validate_public_http_url, SecurityError

paste_string = "4.17 复制打开抖音，看看【田宇轩的作品】事实证明158还是能有爱情的吗？ # 158身高 ... https://v.douyin.com/qu2PVcn74Lw/ E@H.vf 02/06 uSL:/ :6pm"

match = re.search(r'https?://[^\s/$.?#].[^\s]*', paste_string)
clean_url = match.group(0) if match else paste_string

print(f"Extracted URL: {clean_url}")
try:
    validated_url = validate_public_http_url(clean_url)
    print(f"Validated URL: {validated_url}")
except SecurityError as e:
    print(f"SecurityError raised: {e}")
except Exception as e:
    print(f"Other exception: {e}")
