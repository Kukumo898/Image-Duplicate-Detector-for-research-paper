"""Create deterministic, explicitly synthetic examples; never a validation set.

Usage: python scripts/create_demo.py [--output PATH]
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


def font(size: int):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def microscopy(seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    width, height = 720, 520
    noise = rng.normal(0, 5, (height, width))
    yy, xx = np.mgrid[0:height, 0:width]
    field = np.full((height, width), 18.0) + noise
    for _ in range(135):
        x, y = rng.uniform(18, width - 18), rng.uniform(18, height - 18)
        rx, ry = rng.uniform(9, 25), rng.uniform(6, 17)
        angle = rng.uniform(0, np.pi)
        dx, dy = xx - x, yy - y
        u = (dx * np.cos(angle) + dy * np.sin(angle)) / rx
        v = (-dx * np.sin(angle) + dy * np.cos(angle)) / ry
        radius = u * u + v * v
        field += rng.uniform(25, 90) * np.exp(-radius * 2.0)
        field += rng.uniform(20, 45) * np.exp(-((radius - 0.85) ** 2) / 0.07)
        field += rng.uniform(25, 65) * np.exp(-((u - 0.1) ** 2 + (v + 0.15) ** 2) * 12)
    field = np.clip(field, 0, 255)
    rgb = np.stack([field * 0.32, field * 0.97, field * 0.68], axis=-1).astype(np.uint8)
    image = Image.fromarray(rgb).filter(ImageFilter.GaussianBlur(0.45))
    draw = ImageDraw.Draw(image)
    draw.line((590, 480, 680, 480), fill=(235, 245, 240), width=5)
    draw.text((602, 487), "20 um", font=font(15), fill=(235, 245, 240))
    return image


def blot(seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    width, height = 720, 360
    yy, xx = np.mgrid[0:height, 0:width]
    gray = 234.0 + rng.normal(0, 3.8, (height, width))
    gray -= 9 * np.exp(-((xx - 350) / 350) ** 2 - ((yy - 190) / 180) ** 2)
    for lane in range(8):
        x = 72 + lane * 80
        for row, base_y in enumerate((110, 188, 270)):
            y = base_y + rng.uniform(-5, 5)
            strength = rng.uniform(60, 165) if row != 2 else rng.uniform(105, 150)
            width_scale = rng.uniform(16, 24)
            height_scale = rng.uniform(4, 8)
            band = np.exp(-((xx - x) / width_scale) ** 6 - ((yy - y) / height_scale) ** 2)
            gray -= strength * band
            gray += rng.normal(0, 3, (height, width)) * band
    image = Image.fromarray(np.clip(gray, 0, 255).astype(np.uint8)).convert("RGB")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 43), fill="white")
    for lane in range(8):
        draw.text((63 + lane * 80, 14), f"L{lane + 1}", font=font(17), fill=(45, 53, 63))
    draw.text((22, 321), "SYNTHETIC IMMUNOBLOT / arbitrary signal", font=font(15), fill=(70, 78, 88))
    return image


def plot(seed: int, color=(24, 111, 177), bars=False) -> Image.Image:
    rng = np.random.default_rng(seed)
    image = Image.new("RGB", (720, 520), "white")
    draw = ImageDraw.Draw(image)
    draw.text((65, 28), "Synthetic response assay", font=font(24), fill=(30, 41, 59))
    left, top, right, bottom = 90, 90, 665, 430
    for tick in range(6):
        y = bottom - tick * (bottom - top) / 5
        draw.line((left, y, right, y), fill=(227, 232, 238), width=1)
        draw.text((43, y - 8), str(tick * 20), font=font(16), fill=(85, 95, 108))
    draw.line((left, top, left, bottom, right, bottom), fill=(60, 70, 84), width=2)
    values = np.array([12, 26, 39, 61, 69, 85], dtype=float) + rng.normal(0, 4, 6)
    if bars:
        values = np.array([45, 22, 74, 37, 58, 27], dtype=float) + rng.normal(0, 3, 6)
    points = []
    for index, value in enumerate(values):
        x = left + 40 + index * 98
        y = bottom - value * (bottom - top) / 100
        points.append((x, y))
        error = float(rng.uniform(12, 22))
        if bars:
            draw.rectangle((x - 27, y, x + 27, bottom - 1), fill=color)
        draw.line((x, y - error, x, y + error), fill=color, width=2)
        draw.line((x - 6, y - error, x + 6, y - error), fill=color, width=2)
        draw.line((x - 6, y + error, x + 6, y + error), fill=color, width=2)
        if not bars:
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=color)
        draw.text((x - 5, bottom + 13), str(index + 1), font=font(17), fill=(65, 75, 88))
    if not bars:
        draw.line(points, fill=color, width=3)
    draw.text((270, 480), "Condition (arbitrary units)", font=font(17), fill=(65, 75, 88))
    draw.text((96, 63), "Response (%)", font=font(16), fill=(65, 75, 88))
    return image


def create_demo(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    examples = []

    def save(filename: str, image: Image.Image, category: str, note: str):
        image.save(output / filename, format="PNG")
        examples.append({"file": filename, "category": category, "note": note})

    cells = microscopy(20261021)
    save("01_microscopy_original.png", cells, "microscopy", "合成显微纹理原图")
    save("02_microscopy_crop_rotated.png", cells.crop((110, 80, 610, 430)).rotate(90, expand=True),
         "microscopy", "01 的区域裁剪后旋转 90 度")
    save("03_microscopy_mirrored.png", ImageOps.mirror(cells), "microscopy", "01 的水平镜像")
    copied = cells.copy()
    copied.paste(cells.crop((70, 80, 260, 240)), (415, 275))
    save("04_microscopy_copy_move.png", copied, "microscopy", "图内区域 (70,80)-(260,240) 被复制到 (415,275)")
    save("05_microscopy_independent.png", microscopy(20261022), "microscopy", "独立随机生成，非实验真实性阴性验证")

    bands = blot(20261023)
    save("06_blot_original.png", bands, "blot", "合成条带原图，各 lane 独立")
    reused = bands.copy()
    reused.paste(bands.crop((120, 65, 184, 300)), (440, 65))
    save("07_blot_reused_lane.png", reused, "blot", "L2 区域完整复制到 L6，含图内重复")
    save("08_blot_independent.png", blot(20261024), "blot", "独立随机生成的条带对照")

    save("09_plot_original.png", plot(20261025), "plot", "合成统计折线图，不代表真实实验")
    save("10_plot_recolored.png", plot(20261025, color=(174, 66, 111)), "plot", "09 同一组数据，仅更改绘图颜色")
    save("11_plot_independent.png", plot(20261026, color=(55, 137, 110), bars=True), "plot", "独立合成数据柱状图；共享坐标框架不表示数据重复")

    manifest = {
        "synthetic": True,
        "purpose": "功能演示，不是准确率、灵敏度或科研真实性验证集。",
        "examples": examples,
        "known_relationships": [
            {"a": "01_microscopy_original.png", "b": "02_microscopy_crop_rotated.png", "type": "crop_rotate"},
            {"a": "01_microscopy_original.png", "b": "03_microscopy_mirrored.png", "type": "mirror"},
            {"a": "04_microscopy_copy_move.png", "type": "internal_copy", "source_box": [70, 80, 260, 240], "destination_box": [415, 275, 605, 435]},
            {"a": "07_blot_reused_lane.png", "type": "internal_copy", "source_box": [120, 65, 184, 300], "destination_box": [440, 65, 504, 300]},
            {"a": "06_blot_original.png", "b": "07_blot_reused_lane.png", "type": "edited_copy"},
            {"a": "09_plot_original.png", "b": "10_plot_recolored.png", "type": "recolor"},
        ],
        "caution": "相同坐标轴、lane 标签、比例尺等公共结构可能相似；检测线索需要人工结合原始数据复核。",
    }
    positive_groups = [
        {"01_microscopy_original.png", "02_microscopy_crop_rotated.png", "03_microscopy_mirrored.png", "04_microscopy_copy_move.png"},
        {"06_blot_original.png", "07_blot_reused_lane.png"},
        {"09_plot_original.png", "10_plot_recolored.png"},
    ]
    pairs = []
    for first, second in itertools.combinations(examples, 2):
        positive = any(first["file"] in group and second["file"] in group for group in positive_groups)
        category = {"microscopy": "microscope", "blot": "blot", "plot": "chart"}.get(first["category"], "mixed") if first["category"] == second["category"] else "mixed"
        pairs.append({"image_a": first["file"], "image_b": second["file"], "duplicate": int(positive), "category": category,
                      "relation": "shared_synthetic_data" if positive else "independent_synthetic_data"})
    manifest["pairwise_ground_truth"] = pairs
    manifest["internal_ground_truth"] = [
        {"file": item["file"], "label": int(item["file"] in {"04_microscopy_copy_move.png", "07_blot_reused_lane.png"})}
        for item in examples
    ]
    with (output / "ground_truth.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["image_a", "image_b", "duplicate", "category", "relation"])
        writer.writeheader()
        writer.writerows(pairs)
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "README.md").write_text(
        "# 合成科研图像演示集\n\n"
        "所有图像均由程序生成，不来自真实实验，不可用于声称检测准确率。\n\n"
        "包含显微纹理、条带和统计图三类；已知变换及复制区域记录于 manifest.json。\n"
        "ground_truth.csv 包含由生成过程定义的全部 55 个跨图对（8 个正例、47 个负例），非检测输出生成。\n"
        "可同时上传多张做批次查重，或先建立本地图像库后检索。\n"
        "04、07 中的图内复制区域为后续内部检测评估预留；当前版本仅进行图与图之间比对，尚不支持单图内部复制检测。\n\n"
        "05、08、11 为独立生成的对照，可能共享通用坐标轴/标签等版式元素；这些公共结构不应被当作数据重复结论。\n",
        encoding="utf-8",
    )
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="生成明确标注为合成数据的科研图片查重演示集")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "demo")
    args = parser.parse_args()
    result = create_demo(args.output)
    print(f"Generated {len(result['examples'])} synthetic demonstration images in {args.output.resolve()}")
