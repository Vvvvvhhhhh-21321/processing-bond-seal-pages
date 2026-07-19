from dataclasses import dataclass
import math

from pypdf import PdfReader

from .date_layout import Box, DateAnchor
from .pdf_ops import render_pdf_page


_MIN_GOOD_MATCHES = 12
_MIN_INLIERS = 10
_MIN_INLIER_RATIO = 0.35
_MAX_AGREEMENT_DISTANCE = 0.75


@dataclass(frozen=True)
class DateRegistrationResult:
    anchors: tuple[DateAnchor, ...]
    method: str
    score: float
    needs_review: bool
    reason: str | None = None


@dataclass(frozen=True)
class _RegistrationCandidate:
    method: str
    anchors: tuple[DateAnchor, ...]
    score: float


def _page_geometry(path, page_index):
    reader = PdfReader(str(path))
    if page_index < 0 or page_index >= len(reader.pages):
        raise IndexError(f"PDF 页码超出范围：{page_index}")
    page = reader.pages[page_index]
    rotation = int(page.get("/Rotate", 0) or 0) % 360
    if rotation:
        raise ValueError(f"暂不支持带 {rotation} 度旋转标记的日期模板或回章页")
    return float(page.mediabox.width), float(page.mediabox.height)


def _registration_dependencies():
    try:
        import cv2
        import numpy
    except Exception as error:
        raise RuntimeError(
            f"无法加载日期模板配准依赖 numpy 或 opencv-python-headless：{error}"
        ) from error
    return cv2, numpy


def _grayscale_page(path, page_index, scale, cv2, numpy):
    image = render_pdf_page(path, page_index, scale=scale).convert("RGB")
    return cv2.cvtColor(numpy.asarray(image), cv2.COLOR_RGB2GRAY)


def _feature_homography(source, target, cv2, numpy):
    detector = cv2.ORB_create(nfeatures=5000, fastThreshold=7)
    source_points, source_descriptors = detector.detectAndCompute(source, None)
    target_points, target_descriptors = detector.detectAndCompute(target, None)
    if source_descriptors is None or target_descriptors is None:
        raise ValueError("页面缺少足够的图像特征，无法完成模板配准")

    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(
        source_descriptors,
        target_descriptors,
        k=2,
    )
    good = [
        first
        for pair in pairs
        if len(pair) == 2
        for first, second in (pair,)
        if first.distance < second.distance * 0.75
    ]
    if len(good) < _MIN_GOOD_MATCHES:
        raise ValueError(
            f"页面图像特征匹配不足：{len(good)}，至少需要 {_MIN_GOOD_MATCHES}"
        )

    source_matches = numpy.float32(
        [source_points[match.queryIdx].pt for match in good]
    ).reshape(-1, 1, 2)
    target_matches = numpy.float32(
        [target_points[match.trainIdx].pt for match in good]
    ).reshape(-1, 1, 2)
    homography, inlier_mask = cv2.findHomography(
        source_matches,
        target_matches,
        cv2.RANSAC,
        4.0,
    )
    if homography is None or inlier_mask is None:
        raise ValueError("无法计算原始盖章页到回章扫描页的配准变换")
    inlier_count = int(inlier_mask.sum())
    inlier_ratio = inlier_count / len(good)
    if inlier_count < _MIN_INLIERS or inlier_ratio < _MIN_INLIER_RATIO:
        raise ValueError(
            "页面配准置信度不足："
            f"有效匹配 {inlier_count}/{len(good)}，比例 {inlier_ratio:.0%}"
        )
    if not numpy.isfinite(homography).all():
        raise ValueError("页面配准变换包含无效数值")
    evidence_score = inlier_ratio * 0.7 + min(inlier_count / 30, 1.0) * 0.3
    return homography, float(evidence_score)


def _ecc_homography(source, target, cv2, numpy):
    source_height, source_width = source.shape[:2]
    target_height, target_width = target.shape[:2]
    resized_target = cv2.resize(
        target,
        (source_width, source_height),
        interpolation=cv2.INTER_AREA,
    )
    source_float = cv2.GaussianBlur(source, (5, 5), 0).astype(numpy.float32) / 255
    target_float = (
        cv2.GaussianBlur(resized_target, (5, 5), 0).astype(numpy.float32) / 255
    )
    warp = numpy.eye(2, 3, dtype=numpy.float32)
    criteria = (
        cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
        200,
        1e-6,
    )
    try:
        score, warp = cv2.findTransformECC(
            source_float,
            target_float,
            warp,
            cv2.MOTION_AFFINE,
            criteria,
            None,
            5,
        )
    except cv2.error as error:
        raise ValueError("灰度相关性配准未收敛") from error
    if score < 0.25:
        raise ValueError(f"灰度相关性配准置信度不足：{score:.3f}")

    singular_values = numpy.linalg.svd(warp[:, :2], compute_uv=False)
    if singular_values.min() < 0.65 or singular_values.max() > 1.35:
        raise ValueError("灰度相关性配准的缩放或倾斜超出合理范围")
    homography = numpy.eye(3, dtype=numpy.float64)
    homography[:2, :] = warp
    target_scale = numpy.diag(
        [target_width / source_width, target_height / source_height, 1.0]
    )
    return target_scale @ homography, float(score)


def _pdf_to_image(point, page_size, image_shape):
    x, y = point
    page_width, page_height = page_size
    image_height, image_width = image_shape[:2]
    return (
        x / page_width * image_width,
        (page_height - y) / page_height * image_height,
    )


def _image_to_pdf(point, page_size, image_shape):
    x, y = point
    page_width, page_height = page_size
    image_height, image_width = image_shape[:2]
    return (
        x / image_width * page_width,
        page_height - y / image_height * page_height,
    )


def _transform_points(points, homography, cv2, numpy):
    source = numpy.float32(points).reshape(-1, 1, 2)
    transformed = cv2.perspectiveTransform(source, homography).reshape(-1, 2)
    if not numpy.isfinite(transformed).all():
        raise ValueError("日期坐标映射产生无效数值")
    return tuple((float(point[0]), float(point[1])) for point in transformed)


def _mapped_anchor(
    anchor,
    homography,
    source_page_size,
    target_page_size,
    source_shape,
    target_shape,
    cv2,
    numpy,
):
    source_corners = (
        (anchor.box.x0, anchor.box.y0),
        (anchor.box.x1, anchor.box.y0),
        (anchor.box.x1, anchor.box.y1),
        (anchor.box.x0, anchor.box.y1),
    )
    source_baseline = (
        ((anchor.box.x0 + anchor.box.x1) / 2, anchor.baseline),
    )
    image_points = tuple(
        _pdf_to_image(point, source_page_size, source_shape)
        for point in (*source_corners, *source_baseline)
    )
    mapped_image_points = _transform_points(
        image_points,
        homography,
        cv2,
        numpy,
    )
    mapped_pdf_points = tuple(
        _image_to_pdf(point, target_page_size, target_shape)
        for point in mapped_image_points
    )
    corners = mapped_pdf_points[:4]
    baseline = mapped_pdf_points[4][1]
    xs = [point[0] for point in corners]
    ys = [point[1] for point in corners]
    box = Box(min(xs), min(ys), max(xs), max(ys))
    font_size = box.y1 - box.y0
    if not 0 < font_size < target_page_size[1] * 0.1:
        raise ValueError("模板配准后的日期字号超出合理范围")
    return DateAnchor(anchor.component, box, baseline, font_size)


def _validate_mapped_anchors(anchors, target_page_size):
    page_width, page_height = target_page_size
    for anchor in anchors:
        values = (
            anchor.box.x0,
            anchor.box.y0,
            anchor.box.x1,
            anchor.box.y1,
            anchor.baseline,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("模板配准后的日期坐标包含无效数值")
        if (
            anchor.box.x0 < 0
            or anchor.box.y0 < 0
            or anchor.box.x1 > page_width
            or anchor.box.y1 > page_height
        ):
            raise ValueError("模板配准后的日期坐标超出回章页面范围")


def _source_date_mask(source_shape, source_page_size, anchors, cv2, numpy):
    points = []
    for anchor in anchors:
        points.extend(
            (
                _pdf_to_image(
                    (anchor.box.x0, anchor.box.y0),
                    source_page_size,
                    source_shape,
                ),
                _pdf_to_image(
                    (anchor.box.x1, anchor.box.y1),
                    source_page_size,
                    source_shape,
                ),
            )
        )
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    row_height = max(max(ys) - min(ys), 1.0)
    pad_x = max(row_height * 5, 30.0)
    pad_y = max(row_height * 6, 45.0)
    height, width = source_shape[:2]
    x0 = max(0, int(min(xs) - pad_x))
    x1 = min(width, int(max(xs) + pad_x + 1))
    y0 = max(0, int(min(ys) - pad_y))
    y1 = min(height, int(max(ys) + pad_y + 1))
    mask = numpy.zeros((height, width), dtype=numpy.uint8)
    cv2.rectangle(mask, (x0, y0), (x1, y1), 255, thickness=-1)
    return mask


def _edge_score(source_edges, target_distance, mask, numpy):
    selected = (source_edges > 0) & (mask > 0)
    distances = target_distance[selected]
    if distances.size < 20:
        return 0.0
    return float(numpy.exp(-distances / 3.0).mean())


def _alignment_score(
    source,
    target,
    homography,
    source_page_size,
    anchors,
    cv2,
    numpy,
):
    target_height, target_width = target.shape[:2]
    warped_source = cv2.warpPerspective(
        source,
        homography,
        (target_width, target_height),
        flags=cv2.INTER_LINEAR,
        borderValue=255,
    )
    target_edges = cv2.Canny(cv2.GaussianBlur(target, (3, 3), 0), 60, 160)
    source_edges = cv2.Canny(
        cv2.GaussianBlur(warped_source, (3, 3), 0),
        60,
        160,
    )
    target_distance = cv2.distanceTransform(
        (target_edges == 0).astype(numpy.uint8),
        cv2.DIST_L2,
        3,
    )
    global_mask = numpy.full(target.shape[:2], 255, dtype=numpy.uint8)
    local_source_mask = _source_date_mask(
        source.shape,
        source_page_size,
        anchors,
        cv2,
        numpy,
    )
    local_mask = cv2.warpPerspective(
        local_source_mask,
        homography,
        (target_width, target_height),
        flags=cv2.INTER_NEAREST,
        borderValue=0,
    )
    global_score = _edge_score(source_edges, target_distance, global_mask, numpy)
    local_score = _edge_score(source_edges, target_distance, local_mask, numpy)
    return local_score * 0.75 + global_score * 0.25


def _candidate_disagreement(first, second):
    second_by_component = {anchor.component: anchor for anchor in second.anchors}
    distances = []
    for anchor in first.anchors:
        other = second_by_component.get(anchor.component)
        if other is None:
            return math.inf
        first_center = (
            (anchor.box.x0 + anchor.box.x1) / 2,
            (anchor.box.y0 + anchor.box.y1) / 2,
        )
        second_center = (
            (other.box.x0 + other.box.x1) / 2,
            (other.box.y0 + other.box.y1) / 2,
        )
        scale = max((anchor.font_size + other.font_size) / 2, 1.0)
        distances.append(math.dist(first_center, second_center) / scale)
    return max(distances, default=math.inf)


def register_date_anchor_result(
    template_pdf,
    template_page_index,
    returned_pdf,
    returned_page_index,
    anchors,
    render_scale=1.5,
):
    """计算两套日期定位，采用较可靠结果，并显式标记是否需人工重点核对。"""
    anchors = tuple(anchors)
    if not anchors:
        raise ValueError("原始底稿末页没有可用的日期锚点")
    cv2, numpy = _registration_dependencies()
    source_page_size = _page_geometry(template_pdf, template_page_index)
    target_page_size = _page_geometry(returned_pdf, returned_page_index)
    source = _grayscale_page(
        template_pdf,
        template_page_index,
        render_scale,
        cv2,
        numpy,
    )
    target = _grayscale_page(
        returned_pdf,
        returned_page_index,
        render_scale,
        cv2,
        numpy,
    )
    failures = []
    candidates = []
    for label, registration in (
        ("特征", lambda: _feature_homography(source, target, cv2, numpy)),
        ("灰度相关性", lambda: _ecc_homography(source, target, cv2, numpy)),
    ):
        try:
            homography, evidence_score = registration()
            mapped = tuple(
                _mapped_anchor(
                    anchor,
                    homography,
                    source_page_size,
                    target_page_size,
                    source.shape,
                    target.shape,
                    cv2,
                    numpy,
                )
                for anchor in anchors
            )
            _validate_mapped_anchors(mapped, target_page_size)
            alignment_score = _alignment_score(
                source,
                target,
                homography,
                source_page_size,
                anchors,
                cv2,
                numpy,
            )
            score = alignment_score * 0.9 + evidence_score * 0.1
            candidates.append(_RegistrationCandidate(label, mapped, score))
        except Exception as error:
            failures.append(f"{label}配准失败：{str(error) or error.__class__.__name__}")

    if not candidates:
        raise ValueError("；".join(failures))
    selected = max(
        candidates,
        key=lambda candidate: (
            candidate.score + (0.03 if candidate.method == "灰度相关性" else 0.0),
            candidate.method == "灰度相关性",
        ),
    )
    if len(candidates) == 1:
        reason = (
            f"仅{selected.method}定位得到可用结果，已据此落日期，请重点核对"
        )
        return DateRegistrationResult(
            selected.anchors,
            selected.method,
            selected.score,
            True,
            reason,
        )

    disagreement = _candidate_disagreement(candidates[0], candidates[1])
    if disagreement > _MAX_AGREEMENT_DISTANCE:
        reason = (
            "两套定位结果存在分歧，"
            f"已采用较可靠的{selected.method}定位落日期，请重点核对"
        )
        return DateRegistrationResult(
            selected.anchors,
            selected.method,
            selected.score,
            True,
            reason,
        )
    return DateRegistrationResult(
        selected.anchors,
        selected.method,
        selected.score,
        False,
    )


def register_date_anchors(
    template_pdf,
    template_page_index,
    returned_pdf,
    returned_page_index,
    anchors,
    render_scale=1.5,
):
    """兼容旧调用：只返回选中的日期锚点。"""
    return register_date_anchor_result(
        template_pdf,
        template_page_index,
        returned_pdf,
        returned_page_index,
        anchors,
        render_scale=render_scale,
    ).anchors
