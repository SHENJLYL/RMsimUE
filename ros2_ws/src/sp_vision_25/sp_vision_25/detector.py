"""Dual-engine sp_vision_25 armor detector: OpenVINO YOLOv8 + full-res 1280x720 HSV lightbar + tiny_resnet + outpost sticker."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

ARMOR_NAMES = ["one", "two", "three", "four", "five", "sentry", "outpost", "base", "not_armor"]
COLORS = ["blue", "red"]  # YOLOv8 2-class head: 0=blue, 1=red


@dataclass
class ArmorDetection:
    color: str                 # "blue" | "red"
    name: str                  # "sentry" | "outpost" | "base" | "one" | ...
    armor_type: str            # "small" | "big"
    confidence: float          # classifier / sticker confidence
    yolo_score: float
    box: tuple[int, int, int, int]
    keypoints: np.ndarray      # shape (4, 2) ordered [tl, tr, br, bl] in pixel coords
    center: tuple[float, float]
    bar_height_px: float = 0.0
    outpost_marker: bool = False


def _lamp_color(hsv: np.ndarray, top: np.ndarray, bottom: np.ndarray) -> str | None:
    """Read the colored edges of a UE lamp even if its center blooms white."""
    direction = bottom - top
    length = float(np.linalg.norm(direction))
    if not 2.0 <= length <= hsv.shape[0]:
        return None
    axis = direction / length
    width = min(18.0, max(2.0, 0.16 * length))
    x0, y0 = np.maximum(0, np.floor(np.minimum(top, bottom) - width)).astype(int)
    x1, y1 = np.minimum(hsv.shape[1::-1], np.ceil(np.maximum(top, bottom) + width)).astype(int)
    if x0 >= x1 or y0 >= y1:
        return None
    y, x = np.ogrid[y0:y1, x0:x1]
    dx, dy = x - top[0], y - top[1]
    along = dx * axis[0] + dy * axis[1]
    segment = (along >= 0.12 * length) & (along <= 0.88 * length) & (abs(dx * axis[1] - dy * axis[0]) <= width)
    h, s, v = cv2.split(hsv[y0:y1, x0:x1])
    lit = segment & (v >= 135)
    blue = int(np.count_nonzero(lit & (s >= 105) & (h >= 86) & (h <= 135)))
    red = int(np.count_nonzero(lit & (s >= 115) & ((h <= 24) | (h >= 165))))
    minimum = max(2, int(0.25 * length))
    if blue >= minimum and blue > 2 * red:
        return "blue"
    if red >= minimum and red > 2 * blue:
        return "red"
    return None


def sort_keypoints(kps: np.ndarray) -> np.ndarray:
    """Sort 4 keypoints into [top-left, top-right, bottom-right, bottom-left] matching yolov8.cpp."""
    pts = kps[np.argsort(kps[:, 1])]
    top = pts[:2]
    bot = pts[2:]
    top = top[np.argsort(top[:, 0])]
    bot = bot[np.argsort(bot[:, 0])]
    return np.array([top[0], top[1], bot[1], bot[0]], dtype=np.float32)


def crop_armor_pattern(bgr: np.ndarray, kps: np.ndarray) -> np.ndarray | None:
    """Extend lightbar keypoints by 1.125 (0.5 * 126mm / 56mm) matching yolov8.cpp::get_pattern."""
    h_img, w_img = bgr.shape[:2]
    tl = (kps[0] + kps[3]) * 0.5 - (kps[3] - kps[0]) * 1.125
    bl = (kps[0] + kps[3]) * 0.5 + (kps[3] - kps[0]) * 1.125
    tr = (kps[2] + kps[1]) * 0.5 - (kps[2] - kps[1]) * 1.125
    br = (kps[2] + kps[1]) * 0.5 + (kps[2] - kps[1]) * 1.125

    x0 = max(0, int(min(tl[0], bl[0])))
    y0 = max(0, int(min(tl[1], tr[1])))
    x1 = min(w_img, int(max(tr[0], br[0])))
    y1 = min(h_img, int(max(bl[1], br[1])))
    if x1 <= x0 + 2 or y1 <= y0 + 2:
        return None
    return bgr[y0:y1, x0:x1]


def _marker_mask(image: np.ndarray):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = np.uint8((gray >= 150) & (image.max(axis=2) - image.min(axis=2) <= 70))
    points = cv2.findNonZero(mask)
    if points is None:
        return None, 0, 0
    x, y, width, height = cv2.boundingRect(points)
    if min(width, height) < 6:
        return None, width, height
    normalized = cv2.resize(mask[y : y + height, x : x + width] * 255, (32, 44), interpolation=cv2.INTER_AREA) >= 110
    return normalized, width, height


def _outpost_marker_score(frame: np.ndarray, reference: np.ndarray, x1: float, x2: float, center_y: float, height: float) -> float:
    spacing = abs(x2 - x1)
    left = max(0, round(min(x1, x2) + 0.16 * spacing))
    right = min(frame.shape[1], round(max(x1, x2) - 0.16 * spacing))
    top = max(0, round(center_y - 0.92 * height))
    bottom = min(frame.shape[0], round(center_y + 0.92 * height))
    if left >= right or top >= bottom:
        return 0.0
    shape, width, glyph_height = _marker_mask(frame[top:bottom, left:right])
    if shape is None or not 0.32 <= width / max(1.0, spacing) <= 0.65 or not 0.9 <= glyph_height / max(1.0, height) <= 1.85:
        return 0.0
    return float((shape & reference).sum() / max(1, (shape | reference).sum()))


class SpVisionYoloV8Detector:
    def __init__(
        self,
        assets_dir: Path,
        score_threshold: float = 0.42,
        nms_threshold: float = 0.45,
        min_classify_conf: float = 0.60,
    ) -> None:
        xml = assets_dir / "yolov8.xml"
        bin_f = assets_dir / "yolov8.bin"
        cls_onnx = assets_dir / "tiny_resnet.onnx"
        if not xml.is_file() or not bin_f.is_file() or not cls_onnx.is_file():
            raise FileNotFoundError(f"Missing sp_vision_25 weights in {assets_dir}")

        cv2.setNumThreads(4)
        self.yolo_net = cv2.dnn.readNetFromModelOptimizer(str(xml), str(bin_f))
        self.cls_net = cv2.dnn.readNetFromONNX(str(cls_onnx))
        self.score_threshold = float(score_threshold)
        self.nms_threshold = float(nms_threshold)
        self.min_classify_conf = float(min_classify_conf)

        # Optional RMUC outpost sticker template for oblique / long-range rotating outpost armors
        self.outpost_reference = None
        for candidate in (
            assets_dir / "outpost_sticker.png",
            Path(__file__).resolve().parents[4] / "assets/ui/armor_stickers/outpost_sticker.png",
        ):
            if candidate.is_file():
                img = cv2.imdecode(np.frombuffer(candidate.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
                if img is not None:
                    self.outpost_reference = _marker_mask(img)[0]
                    break

    def _classify(self, pattern: np.ndarray) -> tuple[str, float]:
        gray = cv2.cvtColor(pattern, cv2.COLOR_BGR2GRAY)
        gh, gw = gray.shape[:2]
        scale = min(32.0 / gw, 32.0 / gh)
        nw, nh = max(1, int(gw * scale)), max(1, int(gh * scale))
        canvas = np.zeros((32, 32), dtype=np.uint8)
        canvas[:nh, :nw] = cv2.resize(gray, (nw, nh))
        blob = canvas.astype(np.float32)[None, None, :, :] / 255.0
        self.cls_net.setInput(blob)
        logits = self.cls_net.forward().reshape(-1)
        logits = logits - float(np.max(logits))
        probs = np.exp(logits)
        probs /= float(np.sum(probs)) + 1e-12
        idx = int(np.argmax(probs))
        return ARMOR_NAMES[idx], float(probs[idx])

    def _detect_hsv_lightbars(
        self, bgr: np.ndarray, hsv: np.ndarray, color: str, *, prefer_outpost: bool = False
    ) -> list[ArmorDetection]:
        """Full-resolution lightbar pairing fallback for long-range and rotating outpost."""
        if color == "red":
            m1 = cv2.inRange(hsv, (0, 115, 135), (24, 255, 255))
            m2 = cv2.inRange(hsv, (165, 115, 135), (180, 255, 255))
            mask = cv2.bitwise_or(m1, m2)
        else:
            mask = cv2.inRange(hsv, (86, 105, 135), (135, 255, 255))

        _, _, components, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        bars: list[tuple[float, float, float, float]] = []
        for x, y, w, h, pixels in components[1:]:
            if pixels < 4 or h < 3:
                continue
            aspect = h / float(w)
            if aspect < 1.4 or aspect > 22.0:
                continue
            bars.append((x + w * 0.5, y + h * 0.5, float(w), float(h)))

        if len(bars) < 2:
            return []

        candidates = []
        for i in range(len(bars)):
            x1, y1, w1, h1 = bars[i]
            for j in range(i + 1, len(bars)):
                x2, y2, w2, h2 = bars[j]
                if max(h1 / w1, h2 / w2) < 1.9:
                    continue
                avg_h = 0.5 * (h1 + h2)
                if abs(h1 - h2) / max(1.0, avg_h) > 0.50:
                    continue
                dx = abs(x1 - x2)
                dy = abs(y1 - y2)
                if dy > avg_h * 0.70:
                    continue
                ratio = dx / max(1.0, avg_h)
                if ratio < 1.15 or ratio > 5.4:
                    continue
                candidates.append((dx * avg_h, x1, y1, h1, x2, y2, h2))

        h_img, w_img = bgr.shape[:2]
        out: list[ArmorDetection] = []
        for _area, x1, y1, h1, x2, y2, h2 in sorted(candidates, reverse=True)[:12]:
            if x1 > x2:
                x1, y1, h1, x2, y2, h2 = x2, y2, h2, x1, y1, h1
            avg_h = 0.5 * (h1 + h2)
            cx_p, cy_p = 0.5 * (x1 + x2), 0.5 * (y1 + y2)
            kps = np.array(
                [
                    [x1, y1 - 0.5 * h1],
                    [x2, y2 - 0.5 * h2],
                    [x2, y2 + 0.5 * h2],
                    [x1, y1 + 0.5 * h1],
                ],
                dtype=np.float32,
            )
            box = (int(x1), int(min(y1 - 0.5 * h1, y2 - 0.5 * h2)), int(x2 - x1), int(avg_h))

            if self.outpost_reference is not None:
                score = _outpost_marker_score(bgr, self.outpost_reference, x1, x2, cy_p, avg_h)
                marker_thr = 0.48 if prefer_outpost else 0.56
                if score >= marker_thr:
                    out.append(
                        ArmorDetection(
                            color=color,
                            name="outpost",
                            armor_type="small",
                            confidence=score,
                            yolo_score=score,
                            box=box,
                            keypoints=kps,
                            center=(cx_p, cy_p),
                            bar_height_px=avg_h,
                            outpost_marker=True,
                        )
                    )
                    continue

            top = max(0, int(min(y1 - 1.125 * h1, y2 - 1.125 * h2)))
            bottom = min(h_img, int(max(y1 + 1.125 * h1, y2 + 1.125 * h2)))
            left, right = max(0, int(x1)), min(w_img, int(x2))
            if bottom <= top + 2 or right <= left + 2:
                continue
            name, cls_conf = self._classify(bgr[top:bottom, left:right])
            if name in ("one", "two", "three", "four", "five"):
                conf_thr = 0.85
            elif prefer_outpost and name == "outpost":
                conf_thr = 0.50
            else:
                conf_thr = 0.62
            if name == "not_armor" or cls_conf < conf_thr:
                continue
            armor_type = "big" if name in ("one", "base") else "small"
            out.append(
                ArmorDetection(
                    color=color,
                    name=name,
                    armor_type=armor_type,
                    confidence=cls_conf,
                    yolo_score=cls_conf,
                    box=box,
                    keypoints=kps,
                    center=(cx_p, cy_p),
                    bar_height_px=avg_h,
                )
            )
        return out

    def detect(
        self, bgr: np.ndarray, *, target_color: str | None = None, prefer_outpost: bool = False,
        verify_sim_lamp_color: bool = False,
    ) -> list[ArmorDetection]:
        if bgr is None or bgr.size == 0:
            return []
        h0, w0 = bgr.shape[:2]
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        scale = min(416.0 / h0, 416.0 / w0)
        nw, nh = int(w0 * scale), int(h0 * scale)

        letterbox = np.zeros((416, 416, 3), dtype=np.uint8)
        letterbox[:nh, :nw] = cv2.resize(bgr, (nw, nh))
        rgb = cv2.cvtColor(letterbox, cv2.COLOR_BGR2RGB)
        blob = np.transpose(rgb.astype(np.float32) / 255.0, (2, 0, 1))[None, ...]

        self.yolo_net.setInput(blob)
        out = self.yolo_net.forward()  # (1, 14, 3549)
        rows = out[0].T                # (3549, 14)

        scores = rows[:, 4:6]
        cls_ids = np.argmax(scores, axis=1)
        max_scores = scores[np.arange(len(rows)), cls_ids]
        keep = np.where(max_scores >= self.score_threshold)[0]

        results: list[ArmorDetection] = []
        if len(keep) > 0:
            boxes: list[list[int]] = []
            confs: list[float] = []
            kps_list: list[np.ndarray] = []
            ids_list: list[int] = []

            for r in keep:
                cx, cy, bw, bh = rows[r, 0:4]
                left = int((cx - 0.5 * bw) / scale)
                top = int((cy - 0.5 * bh) / scale)
                boxes.append([left, top, int(bw / scale), int(bh / scale)])
                confs.append(float(max_scores[r]))
                ids_list.append(int(cls_ids[r]))
                kps = rows[r, 6:14].reshape(4, 2) / scale
                kps_list.append(sort_keypoints(kps))

            idxs = cv2.dnn.NMSBoxes(boxes, confs, self.score_threshold, self.nms_threshold)
            if len(idxs) > 0:
                for idx in np.array(idxs).reshape(-1):
                    kps = kps_list[idx]
                    det_color = COLORS[ids_list[idx]]
                    if verify_sim_lamp_color:
                        det_color = _lamp_color(hsv, kps[0], kps[3])
                        if det_color is None or det_color != _lamp_color(hsv, kps[1], kps[2]):
                            continue
                    if target_color and det_color != target_color:
                        continue
                    pat = crop_armor_pattern(bgr, kps)
                    if pat is None:
                        continue
                    name, cls_conf = self._classify(pat)
                    min_c = 0.85 if name in ("one", "two", "three", "four", "five") else self.min_classify_conf
                    if name == "not_armor" or cls_conf < min_c:
                        continue
                    armor_type = "big" if name in ("one", "base") else "small"
                    center = (float(np.mean(kps[:, 0])), float(np.mean(kps[:, 1])))
                    h_left = float(np.linalg.norm(kps[3] - kps[0]))
                    h_right = float(np.linalg.norm(kps[2] - kps[1]))
                    results.append(
                        ArmorDetection(
                            color=det_color,
                            name=name,
                            armor_type=armor_type,
                            confidence=cls_conf,
                            yolo_score=confs[idx],
                            box=tuple(boxes[idx]),
                            keypoints=kps,
                            center=center,
                            bar_height_px=0.5 * (h_left + h_right),
                        )
                    )

        # Fuse full-res 1280x720 HSV lightbar + outpost sticker detections for long-range & oblique armors
        colors_to_scan = [target_color] if target_color in ("red", "blue") else ["red", "blue"]
        for col in colors_to_scan:
            for hdet in self._detect_hsv_lightbars(bgr, hsv, col, prefer_outpost=prefer_outpost):
                for i, existing in enumerate(results):
                    margin = (
                        max(3.0, 0.65 * min(existing.bar_height_px, hdet.bar_height_px))
                        if verify_sim_lamp_color else max(24.0, hdet.bar_height_px * 2.0)
                    )
                    if (
                        existing.color == hdet.color
                        and abs(existing.center[0] - hdet.center[0]) < margin
                        and abs(existing.center[1] - hdet.center[1]) < margin
                    ):
                        if verify_sim_lamp_color and hdet.outpost_marker and not existing.outpost_marker:
                            results[i] = hdet
                        break
                else:
                    results.append(hdet)
        return results
