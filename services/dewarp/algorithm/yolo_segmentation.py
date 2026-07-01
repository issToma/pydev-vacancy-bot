import cv2
import logging
import numpy as np
from ultralytics import YOLO

from services.dewarp.algorithm.errors import NotFoundYOLOSegmentError

logger = logging.getLogger(__name__)


class YOLOSegmentation:
    def __init__(self, weight_path):
        self.model = YOLO(weight_path)

        self.RADIUS_RATIO = 0.01
        self.MIN_RADIUS = 3
        self.EPSILON_DIVIDER = 2
        self.MORPH_KERNEL_RATIO = 0.002

    def yolo_detect_segments(self, img, confidence: float):
        height, width, _ = img.shape

        results = self.model.predict(source=img, conf=confidence, save=False, save_txt=False)
        result = results[0]

        if result.masks is None:
            raise NotFoundYOLOSegmentError("Не найдена маска документа для заданной точности на изображении")

        segmentation_contours_idx = []
        for seg in result.masks.xyn:
            seg[:, 0] *= width
            seg[:, 1] *= height
            segment = np.array(seg, dtype=np.int32)
            segmentation_contours_idx.append(segment)

        scores = np.array(result.boxes.conf.cpu(), dtype="float").round(3)
        return segmentation_contours_idx, scores

    def build_masks_array(self, image_rgb, confidence: float) -> list:
        segments, scores = self.yolo_detect_segments(img=image_rgb, confidence=confidence)

        num_of_masks = len(segments)
        logger.info(f"Documents masks found: {num_of_masks}")

        yolo_output = [[seg, score] for seg, score in zip(segments, scores)]

        masks = []
        for seg, score in yolo_output:
            mask = self.create_and_filtered_masks(image_rgb, seg, score)
            masks.append(mask)
        return masks

    def create_and_filtered_masks(self, image_rgb, segment, score):
        """
        Создаёт уточнённую маску документа на основе сегмента из YOLO
        с помощью guided filter и бинаризации Оцу.
        """
        mask = np.zeros(image_rgb.shape[:2], np.uint8)
        cv2.drawContours(mask, [segment], -1, 255, cv2.FILLED, cv2.LINE_AA)

        radius = max(int(min(image_rgb.shape[:2]) * self.RADIUS_RATIO), self.MIN_RADIUS)
        epsilon = radius // self.EPSILON_DIVIDER

        fil_mask = cv2.ximgproc.guidedFilter(
            image_rgb.astype(np.float32),
            mask.astype(np.float32),
            radius, epsilon,
        )

        fil_mask_uint8 = np.clip(fil_mask, 0, 255).astype(np.uint8)
        _, bw = cv2.threshold(fil_mask_uint8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        kernel_size = int(min(image_rgb.shape[:2]) * self.MORPH_KERNEL_RATIO)
        if kernel_size % 2 == 0:
            kernel_size += 1
        kernel_size = max(3, min(kernel_size, 11))

        logger.info(f'Score: {score}. Radius: {radius}. Epsilon: {epsilon}. Kernel: {kernel_size}x{kernel_size}')

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
        bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kernel)
        bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel)

        return bw