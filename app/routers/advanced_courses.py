"""高级课程内容受权访问路由"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.core.permissions import require_feature
from app.core.response import ok


router = APIRouter(
    prefix="/api/learning/advanced-courses",
    tags=["advanced-courses"],
    dependencies=[Depends(require_feature("advanced_courses"))]
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
# v3.0 课程以阶段子目录组织（docs/07-courses/v3.0/phase-XX-name/lesson-XX.md）；
# 兼容旧版平铺目录（docs/courses/advanced/expanded/lesson-XX.md，上游 Pro 版）。
COURSE_ROOTS = [
    PROJECT_ROOT / "docs" / "07-courses" / "v3.0",
    PROJECT_ROOT / "docs" / "courses" / "advanced" / "expanded",
]
IMAGE_ROOT = PROJECT_ROOT / "docs" / "07-courses" / "v3.0" / "images"

LESSON_FILE_PREFIX = "lesson-"
LESSON_FILE_SUFFIX = ".md"

SAMPLE_FILE_MAP = {
    "000001-research-report-2026-04-30": "000001_研究报告_2026-04-30.md",
}


def _resolve_within(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()
    resolved_root = root.resolve()

    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="资源不存在") from exc

    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="资源不存在")

    return candidate


def _find_lesson_file(filename: str) -> Path:
    """在所有课程根目录（含阶段子目录）中定位课程文件。"""
    for root in COURSE_ROOTS:
        # 平铺：root/lesson-XX.md
        direct = root / filename
        if direct.is_file():
            return direct
        # 阶段子目录：root/phase-XX-name/lesson-XX.md
        if root.is_dir():
            for phase_dir in sorted(root.iterdir()):
                if not phase_dir.is_dir():
                    continue
                candidate = phase_dir / filename
                if candidate.is_file():
                    return candidate
    raise HTTPException(status_code=404, detail="课程不存在")


def _resolve_lesson_file(filename: str) -> Path:
    # v3.0 课程配置中的 file 字段带阶段目录前缀（phase-XX-name/lesson-XX.md），
    # 旧版为平铺文件名（lesson-XX.md）——两种都放行，安全边界在 _find_lesson_file
    # 的目录内解析中保证。
    if not filename.endswith(LESSON_FILE_SUFFIX):
        raise HTTPException(status_code=404, detail="课程不存在")

    # 归一化：取路径的最后一段做前缀校验，防止借道子目录写其他文件
    leaf = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if not leaf.startswith(LESSON_FILE_PREFIX):
        raise HTTPException(status_code=404, detail="课程不存在")

    return _find_lesson_file(leaf)


def _resolve_sample_file(sample_id: str) -> Path:
    filename = SAMPLE_FILE_MAP.get(sample_id)
    if not filename:
        raise HTTPException(status_code=404, detail="课程示例不存在")

    for root in COURSE_ROOTS:
        direct = root / filename
        if direct.is_file():
            return direct

    raise HTTPException(status_code=404, detail="课程示例不存在")


@router.get("/lessons/content")
async def get_lesson_content(filename: str = Query(..., min_length=1)):
    lesson_path = _resolve_lesson_file(filename)
    return ok({
        "filename": lesson_path.name,
        "content": lesson_path.read_text(encoding="utf-8"),
    })


@router.get("/lessons/download")
async def download_lesson(filename: str = Query(..., min_length=1)):
    lesson_path = _resolve_lesson_file(filename)
    return FileResponse(
        lesson_path,
        media_type="text/markdown; charset=utf-8",
        filename=lesson_path.name,
    )


@router.get("/samples/{sample_id}")
async def get_sample_content(sample_id: str):
    sample_path = _resolve_sample_file(sample_id)
    return ok({
        "sample_id": sample_id,
        "filename": sample_path.name,
        "content": sample_path.read_text(encoding="utf-8"),
    })


@router.get("/samples/{sample_id}/download")
async def download_sample(sample_id: str):
    sample_path = _resolve_sample_file(sample_id)
    return FileResponse(
        sample_path,
        media_type="text/markdown; charset=utf-8",
        filename=sample_path.name,
    )


@router.get("/images/{image_path:path}")
async def get_course_image(image_path: str):
    image_file = _resolve_within(IMAGE_ROOT, image_path)
    return FileResponse(image_file)