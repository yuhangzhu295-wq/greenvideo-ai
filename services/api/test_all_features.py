#!/usr/bin/env python
"""
GreenVideo AI 全功能测试脚本
"""

import os
import time

import httpx

BASE_URL = "http://127.0.0.1:8000"

test_results = []


def test_result(name, passed, details=""):
    status = "[PASS]" if passed else "[FAIL]"
    test_results.append({"name": name, "passed": passed, "details": details})
    print(f"{status} - {name}")
    if details:
        print(f"   {details}")
    print()


def print_header(title):
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)
    print()


# ============================================================
# TEST 1: 健康检查
# ============================================================
print_header("TEST 1: 服务健康检查")

try:
    response = httpx.get(f"{BASE_URL}/docs", timeout=5)
    test_result("API 服务运行", response.status_code == 200, f"Status: {response.status_code}")
except Exception as e:
    test_result("API 服务运行", False, str(e))


# ============================================================
# TEST 2: 视频解析 API
# ============================================================
print_header("TEST 2: 视频解析 API")

try:
    test_url = "https://www.bilibili.com/video/BV1xx411c7mD"
    response = httpx.post(f"{BASE_URL}/api/parse", json={"url": test_url}, timeout=30)

    if response.status_code == 200:
        data = response.json()
        title = data.get("title", "N/A")
        source = data.get("source", "N/A")
        duration = data.get("duration", 0)
        formats_count = len(data.get("formats", []))

        details = (
            f"标题: {title[:30]}... | 来源: {source} | 时长: {duration}s | 格式: {formats_count}种"
        )
        test_result("视频解析接口", True, details)
    else:
        err_msg = f"Status: {response.status_code} - {response.text[:100]}"
        test_result("视频解析接口", False, err_msg)
except Exception as e:
    test_result("视频解析接口", False, str(e))


# ============================================================
# TEST 3: 下载任务 API
# ============================================================
print_header("TEST 3: 下载任务 API")

try:
    response = httpx.post(
        f"{BASE_URL}/api/tasks/download",
        json={"parse_id": "test_parse_001", "format_id": "1080p"},
        timeout=10,
    )

    if response.status_code == 200:
        data = response.json()
        task_id = data.get("task_id", "N/A")
        status = data.get("status", "N/A")
        test_result("创建下载任务", True, f"任务ID: {task_id} | 状态: {status}")

        # 查询任务状态
        time.sleep(1)
        response2 = httpx.get(f"{BASE_URL}/api/tasks/{task_id}", timeout=10)
        if response2.status_code == 200:
            task_data = response2.json()
            progress = task_data.get("progress", 0)
            stage = task_data.get("stage", "N/A")
            test_result("查询任务状态", True, f"进度: {progress}% | 阶段: {stage}")
        else:
            test_result("查询任务状态", False, f"Status: {response2.status_code}")
    else:
        test_result("创建下载任务", False, f"Status: {response.status_code}")
except Exception as e:
    test_result("创建下载任务", False, str(e))


# ============================================================
# TEST 4: 图片去水印 - 标准模式
# ============================================================
print_header("TEST 4: 图片去水印（标准模式）")

try:
    test_image_path = "storage/test_input/test_watermark_1.jpg"

    if os.path.exists(test_image_path):
        # 测试直接调用 watermark 服务
        from app.schemas import MaskRegion
        from app.services.watermark import WatermarkProcessor

        processor = WatermarkProcessor()
        output_path = "storage/test_output/watermark_standard_result.jpg"

        # 定义水印区域（假设水印在右下角）
        mask_regions = [
            MaskRegion(x=10, y=450, w=200, h=40)  # 底部文字水印
        ]

        result = processor.process_image(
            input_path=test_image_path,
            mask_regions=mask_regions,
            output_path=output_path,
            mode="standard",
        )

        if os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            detail_str = f"输出文件: {output_path} | 大小: {file_size} bytes"
            test_result("图片去水印（标准模式）", True, detail_str)
        else:
            test_result("图片去水印（标准模式）", False, "输出文件不存在")
    else:
        test_result("图片去水印（标准模式）", False, f"测试图片不存在: {test_image_path}")
except Exception as e:
    test_result("图片去水印（标准模式）", False, str(e))


# ============================================================
# TEST 5: 去水印任务 API
# ============================================================
print_header("TEST 5: 去水印任务 API")

try:
    # 测试 JSON 方式创建任务
    response = httpx.post(
        f"{BASE_URL}/api/tasks/watermark",
        json={
            "source_task_id": "test_source_001",
            "mask_regions": [{"x": 10, "y": 10, "w": 100, "h": 50}],
            "mode": "standard",
        },
        timeout=10,
    )

    if response.status_code == 200:
        data = response.json()
        task_id = data.get("task_id", "N/A")
        test_result("创建去水印任务（JSON）", True, f"任务ID: {task_id}")
    else:
        err_msg = f"Status: {response.status_code} - {response.text[:100]}"
        test_result("创建去水印任务（JSON）", False, err_msg)
except Exception as e:
    test_result("创建去水印任务（JSON）", False, str(e))


# ============================================================
# TEST 6: 历史记录 API
# ============================================================
print_header("TEST 6: 历史记录 API")

try:
    response = httpx.get(f"{BASE_URL}/api/history", timeout=10)

    if response.status_code == 200:
        data = response.json()
        items = data.get("items", [])
        test_result("获取历史记录", True, f"记录数: {len(items)} 条")
    else:
        test_result("获取历史记录", False, f"Status: {response.status_code}")
except Exception as e:
    test_result("获取历史记录", False, str(e))


# ============================================================
# TEST 7: 反馈 API
# ============================================================
print_header("TEST 7: 反馈 API")

try:
    response = httpx.post(
        f"{BASE_URL}/api/feedback",
        json={
            "category": "功能建议",
            "description": "测试反馈内容，希望增加更多功能",
            "contact": "test@example.com",
        },
        timeout=10,
    )

    if response.status_code == 200:
        data = response.json()
        feedback_id = data.get("id", "N/A")
        ok = data.get("ok", False)
        test_result("提交反馈", ok, f"反馈ID: {feedback_id}")
    else:
        test_result("提交反馈", False, f"Status: {response.status_code}")
except Exception as e:
    test_result("提交反馈", False, str(e))


# ============================================================
# TEST 8: 文件上传 API
# ============================================================
print_header("TEST 8: 文件上传 API")

try:
    test_image_path = "storage/test_input/test_watermark_2.jpg"

    if os.path.exists(test_image_path):
        with open(test_image_path, "rb") as f:
            files = {"file": ("test_image.jpg", f, "image/jpeg")}
            response = httpx.post(f"{BASE_URL}/api/tasks/watermark", files=files, timeout=30)

        if response.status_code == 200:
            data = response.json()
            task_id = data.get("task_id", "N/A")
            test_result("文件上传创建去水印任务", True, f"任务ID: {task_id}")
        else:
            err_msg = f"Status: {response.status_code} - {response.text[:100]}"
            test_result("文件上传创建去水印任务", False, err_msg)
    else:
        test_result("文件上传创建去水印任务", False, f"测试文件不存在: {test_image_path}")
except Exception as e:
    test_result("文件上传创建去水印任务", False, str(e))


# ============================================================
# TEST 9: AI 模式检测
# ============================================================
print_header("TEST 9: AI 修复模式检测")

try:
    from app.services.watermark import try_iopaint_service

    iopaint_available = try_iopaint_service()
    status = "可用" if iopaint_available else "未启用（自动降级到标准模式）"
    test_result("IOPaint AI 服务检测", True, status)
except Exception as e:
    test_result("IOPaint AI 服务检测", False, str(e))


# ============================================================
# 总结
# ============================================================
print_header("测试总结")

passed = sum(1 for t in test_results if t["passed"])
total = len(test_results)

print(f"总测试数: {total}")
print(f"通过: {passed}")
print(f"失败: {total - passed}")
print(f"通过率: {passed / total * 100:.1f}%")
print()

print("详细结果:")
for i, result in enumerate(test_results, 1):
    status = "[OK]" if result["passed"] else "[XX]"
    print(f"{i:2d}. {status} {result['name']}")
    if result["details"]:
        print(f"    {result['details']}")

print()
print("=" * 60)
print("  测试完成！")
print("=" * 60)
