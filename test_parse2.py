import urllib.request
import json
import ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

req = urllib.request.Request(
    'http://127.0.0.1:8000/api/parse',
    data=json.dumps({"url": "4.17 复制打开抖音，看看【田宇轩的作品】事实证明158还是能有爱情的吗？ # 158身高 ... https://v.douyin.com/qu2PVcn74Lw/ E@H.vf 02/06 uSL:/ :6pm"}).encode('utf-8'),
    headers={'Content-Type': 'application/json'}
)
try:
    with urllib.request.urlopen(req, context=ctx) as response:
        print(response.read().decode('utf-8'))
except urllib.error.HTTPError as e:
    print(f"HTTPError: {e.code} - {e.read().decode('utf-8')}")
except Exception as e:
    print(e)
