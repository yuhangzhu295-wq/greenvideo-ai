import re
from urllib.parse import urlparse

text = "4.17 复制打开抖音，看看【田宇轩的作品】事实证明158还是能有爱情的吗？ # 158身高 ... https://v.douyin.com/qu2PVcn74Lw/ E@H.vf 02/06 uSL:/ :6pm"

# Current regex
pattern = re.compile(r'https?://[^\s/$.?#].[^\s]*')
match = pattern.search(text)
if match:
    print(f"Match: {match.group(0)}")
    parsed = urlparse(match.group(0))
    print(f"Parsed hostname: {parsed.hostname}")
else:
    print("No match")
