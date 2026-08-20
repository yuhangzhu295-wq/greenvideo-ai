# TEST_MATERIALS

## 本地视频素材

优先使用 Windows 本地素材：

```text
C:\Users\zyu33\Desktop\f553635d2972ffa3bca4b94d7ad10b42.mp4
```

如果文件不存在，验证脚本会生成带模拟水印的 10 秒 demo mp4；如果 FFmpeg 不可用，则生成 fallback 文件并在报告里明确标记。

## 去水印位置

- 左上角：适合抖音 Logo、抖音号 `85547992731`、搜索文字区域，默认比例 `x=0% y=0% w=42% h=18%`。
- 右下角：适合右下角抖音 Logo、抖音号区域，默认比例 `x=60% y=78% w=40% h=18%`。
- 双角：同时处理左上和右下，作为本次本地素材验证默认值。
- 字幕区域默认不处理，只有 `remove_subtitle=true` 时才加入 mask。

## 分享链接验证

抖音分享文案：

```text
4.17 复制打开抖音，看看【田宇轩的作品】事实证明是58还是能有爱情的吗？# 158身高 ... https://v.douyin.com/qu2PVcn74Lw/ E@H.vf 02/06 uSL:/ :6pm
```

B 站链接：

```text
【我给Codex做了一个分镜台，Codex秒变视频分镜脚本工作台】https://b23.tv/UlH6LvD
```

快手链接：

```text
https://v.kuaishou.com/KDINf2L9 用最平静的声音说出最绝望的话 “缅甸诈骗电话 ” 毁掉 该作品在快手被播放过1,811万次，点击链接，打开【快手】直接观看！
```

验证判定：

- `success`：平台解析成功。
- `expected_blocked`：返回登录、私密、DRM、平台限制、反爬或频率限制等合规错误。
- `failed_bug`：接口 500、连接失败、未提取 URL、JSON 结构错误或非预期错误。
