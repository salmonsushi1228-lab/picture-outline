"""
pose_engine.py
--------------
Google MediaPipe를 사용하여
  1) 레퍼런스 이미지에서 포즈 랜드마크(33개 관절) 및 인체 실루엣(윤곽)을 추출하고
  2) 실시간 카메라 프레임에서 포즈를 추적하며 레퍼런스와의 유사도를 계산하고
  3) 코칭 피드백 문구를 생성하는
핵심 AI 로직을 담당합니다.

UI(Flet)와는 완전히 분리된 순수 로직 계층입니다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np
import mediapipe as mp

mp_pose = mp.solutions.pose
mp_selfie = mp.solutions.selfie_segmentation

# 유사도 계산에 사용할 주요 관절 인덱스 (얼굴 세부 랜드마크는 제외하고 몸통/팔다리 위주)
KEY_LANDMARK_IDS = [
    mp_pose.PoseLandmark.LEFT_SHOULDER,
    mp_pose.PoseLandmark.RIGHT_SHOULDER,
    mp_pose.PoseLandmark.LEFT_ELBOW,
    mp_pose.PoseLandmark.RIGHT_ELBOW,
    mp_pose.PoseLandmark.LEFT_WRIST,
    mp_pose.PoseLandmark.RIGHT_WRIST,
    mp_pose.PoseLandmark.LEFT_HIP,
    mp_pose.PoseLandmark.RIGHT_HIP,
    mp_pose.PoseLandmark.LEFT_KNEE,
    mp_pose.PoseLandmark.RIGHT_KNEE,
    mp_pose.PoseLandmark.LEFT_ANKLE,
    mp_pose.PoseLandmark.RIGHT_ANKLE,
]

# 피드백 문구 생성을 위한 관절 쌍(팔/다리) 매핑
FEEDBACK_JOINTS = {
    "right_arm": (mp_pose.PoseLandmark.RIGHT_SHOULDER, mp_pose.PoseLandmark.RIGHT_ELBOW, mp_pose.PoseLandmark.RIGHT_WRIST),
    "left_arm": (mp_pose.PoseLandmark.LEFT_SHOULDER, mp_pose.PoseLandmark.LEFT_ELBOW, mp_pose.PoseLandmark.LEFT_WRIST),
    "right_leg": (mp_pose.PoseLandmark.RIGHT_HIP, mp_pose.PoseLandmark.RIGHT_KNEE, mp_pose.PoseLandmark.RIGHT_ANKLE),
    "left_leg": (mp_pose.PoseLandmark.LEFT_HIP, mp_pose.PoseLandmark.LEFT_KNEE, mp_pose.PoseLandmark.LEFT_ANKLE),
}


@dataclass
class ReferencePose:
    """업로드된 레퍼런스 이미지에서 추출한 데이터."""
    landmarks: list  # normalized (x, y, z, visibility) 튜플 리스트, 33개
    silhouette_rgba: np.ndarray  # 실루엣만 살린 투명 배경 RGBA 이미지
    source_size: tuple  # (width, height) 원본 이미지 크기
    vector: np.ndarray = field(default_factory=lambda: np.array([]))  # 유사도 비교용 정규화 벡터


@dataclass
class LiveFrameResult:
    """실시간 프레임 1장을 처리한 결과."""
    annotated_bgr: np.ndarray  # 랜드마크가 그려진 BGR 프레임 (디버그/옵션용)
    landmarks: Optional[list]  # 감지된 33개 랜드마크, 감지 실패 시 None
    similarity: float  # 0~100 유사도 점수
    feedback: str  # 코칭 피드백 문구


class PoseEngine:
    """MediaPipe Pose + Selfie Segmentation을 캡슐화한 엔진 클래스."""

    def __init__(self) -> None:
        self._pose_static = mp_pose.Pose(
            static_image_mode=True,
            model_complexity=2,
            enable_segmentation=False,
            min_detection_confidence=0.5,
        )
        self._pose_stream = mp_pose.Pose(
            static_image_mode=False,
            model_complexity=1,
            smooth_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self._segmenter = mp_selfie.SelfieSegmentation(model_selection=1)

    # ------------------------------------------------------------------
    # 레퍼런스 이미지 처리
    # ------------------------------------------------------------------
    def extract_reference(self, image_bgr: np.ndarray) -> Optional[ReferencePose]:
        """레퍼런스 이미지에서 포즈 랜드마크 + 실루엣을 추출합니다."""
        h, w = image_bgr.shape[:2]
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)

        pose_result = self._pose_static.process(rgb)
        if not pose_result.pose_landmarks:
            return None

        landmarks = [
            (lm.x, lm.y, lm.z, lm.visibility) for lm in pose_result.pose_landmarks.landmark
        ]

        seg_result = self._segmenter.process(rgb)
        mask = seg_result.segmentation_mask  # 0~1 float mask
        silhouette_rgba = self._build_silhouette(rgb, mask)

        vector = self._landmarks_to_vector(landmarks)

        return ReferencePose(
            landmarks=landmarks,
            silhouette_rgba=silhouette_rgba,
            source_size=(w, h),
            vector=vector,
        )

    @staticmethod
    def _build_silhouette(rgb: np.ndarray, mask: np.ndarray, edge_color=(61, 220, 151)) -> np.ndarray:
        """세그멘테이션 마스크로부터 반투명 실루엣(윤곽선 강조) RGBA 이미지를 생성합니다."""
        h, w = mask.shape
        binary_mask = (mask > 0.5).astype(np.uint8) * 255

        # 윤곽선 검출
        contours, _ = cv2.findContours(binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        # 채움 영역: 살짝 반투명하게
        rgba[binary_mask > 0] = (*edge_color, 60)
        # 윤곽선: 진하게
        cv2.drawContours(rgba, contours, -1, (*edge_color, 255), thickness=4)

        return rgba

    # ------------------------------------------------------------------
    # 실시간 프레임 처리
    # ------------------------------------------------------------------
    def process_live_frame(
        self, frame_bgr: np.ndarray, reference: Optional[ReferencePose]
    ) -> LiveFrameResult:
        """실시간 카메라 프레임을 처리하고, 레퍼런스와 유사도를 계산합니다."""
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        result = self._pose_stream.process(rgb)

        if not result.pose_landmarks:
            return LiveFrameResult(
                annotated_bgr=frame_bgr,
                landmarks=None,
                similarity=0.0,
                feedback="카메라에 전신이 잘 보이도록 위치를 조정해주세요.",
            )

        landmarks = [
            (lm.x, lm.y, lm.z, lm.visibility) for lm in result.pose_landmarks.landmark
        ]

        similarity = 0.0
        feedback = "레퍼런스 포즈를 먼저 업로드해주세요."
        if reference is not None:
            live_vector = self._landmarks_to_vector(landmarks)
            similarity = self._cosine_similarity_score(reference.vector, live_vector)
            feedback = self._generate_feedback(reference.landmarks, landmarks, similarity)

        return LiveFrameResult(
            annotated_bgr=frame_bgr,
            landmarks=landmarks,
            similarity=similarity,
            feedback=feedback,
        )

    # ------------------------------------------------------------------
    # 유사도 / 피드백 로직
    # ------------------------------------------------------------------
    @staticmethod
    def _landmarks_to_vector(landmarks: list) -> np.ndarray:
        """
        주요 관절 좌표를 하나의 벡터로 변환합니다.
        - 어깨 중심을 원점으로 이동(위치 불변)
        - 몸통 길이(어깨~골반 평균 거리)로 스케일 정규화(크기/거리 불변)
        """
        pts = np.array([[landmarks[i][0], landmarks[i][1]] for i in
                         [lm.value for lm in KEY_LANDMARK_IDS]])

        l_shoulder = np.array(landmarks[mp_pose.PoseLandmark.LEFT_SHOULDER][:2])
        r_shoulder = np.array(landmarks[mp_pose.PoseLandmark.RIGHT_SHOULDER][:2])
        l_hip = np.array(landmarks[mp_pose.PoseLandmark.LEFT_HIP][:2])
        r_hip = np.array(landmarks[mp_pose.PoseLandmark.RIGHT_HIP][:2])

        center = (l_shoulder + r_shoulder) / 2.0
        torso_len = np.linalg.norm(((l_shoulder + r_shoulder) / 2.0) - ((l_hip + r_hip) / 2.0))
        torso_len = max(torso_len, 1e-6)

        normalized = (pts - center) / torso_len
        return normalized.flatten()

    @staticmethod
    def _cosine_similarity_score(ref_vec: np.ndarray, live_vec: np.ndarray) -> float:
        """코사인 유사도를 0~100 점수로 변환합니다."""
        if ref_vec.size == 0 or live_vec.size == 0:
            return 0.0
        dot = float(np.dot(ref_vec, live_vec))
        norm = float(np.linalg.norm(ref_vec) * np.linalg.norm(live_vec))
        if norm < 1e-9:
            return 0.0
        cosine = dot / norm
        # 코사인 유사도(-1~1)를 0~100으로 매핑, 음수는 0으로 클램프
        score = max(0.0, (cosine + 1) / 2 * 100)
        return round(score, 1)

    @classmethod
    def _generate_feedback(cls, ref_landmarks: list, live_landmarks: list, similarity: float) -> str:
        """관절 각도 차이를 비교하여 사람이 이해할 수 있는 코칭 문구를 생성합니다."""
        if similarity >= 90:
            return "완벽해요! 지금 그대로 촬영하세요 📸"

        worst_joint = None
        worst_diff = -1.0

        for name, (a_id, b_id, c_id) in FEEDBACK_JOINTS.items():
            ref_angle = cls._joint_angle(ref_landmarks, a_id, b_id, c_id)
            live_angle = cls._joint_angle(live_landmarks, a_id, b_id, c_id)
            if ref_angle is None or live_angle is None:
                continue
            diff = abs(ref_angle - live_angle)
            if diff > worst_diff:
                worst_diff = diff
                worst_joint = name

        if worst_joint is None or worst_diff < 15:
            return "좋아요! 자세를 거의 맞췄어요."

        messages = {
            "right_arm": "오른팔 각도를 레퍼런스에 맞게 조정해주세요.",
            "left_arm": "왼팔 각도를 레퍼런스에 맞게 조정해주세요.",
            "right_leg": "오른쪽 다리 자세를 레퍼런스와 비슷하게 맞춰주세요.",
            "left_leg": "왼쪽 다리 자세를 레퍼런스와 비슷하게 맞춰주세요.",
        }
        return messages.get(worst_joint, "자세를 조금 더 조정해주세요.")

    @staticmethod
    def _joint_angle(landmarks: list, a_id, b_id, c_id) -> Optional[float]:
        """세 점(a-b-c)으로 이루어진 관절 각도(도)를 계산합니다. b가 꼭짓점."""
        try:
            a = np.array(landmarks[a_id][:2])
            b = np.array(landmarks[b_id][:2])
            c = np.array(landmarks[c_id][:2])
        except (IndexError, TypeError):
            return None

        ba = a - b
        bc = c - b
        norm_ba = np.linalg.norm(ba)
        norm_bc = np.linalg.norm(bc)
        if norm_ba < 1e-6 or norm_bc < 1e-6:
            return None

        cosine = np.dot(ba, bc) / (norm_ba * norm_bc)
        cosine = np.clip(cosine, -1.0, 1.0)
        return math.degrees(math.acos(cosine))

    def close(self) -> None:
        """리소스 해제."""
        self._pose_static.close()
        self._pose_stream.close()
        self._segmenter.close()
