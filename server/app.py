"""
server/app.py
--------------
안드로이드 앱(Flet APK)에서 촬영한 이미지를 받아
MediaPipe/OpenCV로 포즈 유사도와 코칭 피드백을 계산해 돌려주는 API 서버입니다.

무거운 AI 연산(mediapipe, opencv)은 전부 이 서버에서만 실행됩니다.
안드로이드 wheel이 없는 패키지들이기 때문에, 폰 앱(APK)에는 이 패키지들을
포함시키지 않고 이 서버만 그것들을 사용합니다.

실행:
    pip install -r requirements.txt
    uvicorn app:app --host 0.0.0.0 --port 8000

폰과 같은 Wi-Fi 네트워크에 있다면, 폰 앱 설정 화면에
"http://<이 컴퓨터의 사설 IP>:8000" 을 서버 주소로 입력하면 됩니다.
(같은 네트워크가 아니라면 ngrok 등으로 외부에 노출해야 합니다.)
"""

from __future__ import annotations

import base64
import io
import uuid
from typing import Dict, Optional

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from pose_engine import PoseEngine, ReferencePose

app = FastAPI(title="PoseFrame Analysis Server")
pose_engine = PoseEngine()

# 세션(사용자)별 레퍼런스 포즈를 메모리에 보관합니다.
# 운영 환경에서는 Redis 등 외부 저장소로 교체하는 것을 권장합니다.
_sessions: Dict[str, ReferencePose] = {}


def _decode_image(raw_bytes: bytes) -> Optional[np.ndarray]:
    """업로드된 바이트를 OpenCV BGR 이미지로 디코딩합니다."""
    arr = np.frombuffer(raw_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img


@app.get("/health")
def health() -> dict:
    """서버 상태 및 활성 세션 수 확인용 엔드포인트."""
    return {"status": "ok", "active_sessions": len(_sessions)}


@app.post("/reference")
async def upload_reference(file: UploadFile = File(...)) -> JSONResponse:
    """
    레퍼런스 포즈 이미지를 업로드받아 랜드마크/실루엣을 추출하고,
    이후 프레임 비교에 사용할 session_id를 발급합니다.
    """
    raw = await file.read()
    image_bgr = _decode_image(raw)
    if image_bgr is None:
        raise HTTPException(status_code=400, detail="이미지를 디코딩할 수 없습니다.")

    reference = pose_engine.extract_reference(image_bgr)
    if reference is None:
        raise HTTPException(status_code=422, detail="이미지에서 포즈를 인식하지 못했습니다.")

    session_id = uuid.uuid4().hex
    _sessions[session_id] = reference

    ok, buf = cv2.imencode(".png", cv2.cvtColor(reference.silhouette_rgba, cv2.COLOR_RGBA2BGRA))
    silhouette_b64 = base64.b64encode(buf.tobytes()).decode("utf-8") if ok else ""

    return JSONResponse(
        {
            "session_id": session_id,
            "silhouette_png_base64": silhouette_b64,
        }
    )


@app.post("/frame")
async def analyze_frame(
    session_id: str = Form(...),
    file: UploadFile = File(...),
) -> JSONResponse:
    """실시간(주기적) 프레임 1장을 받아 레퍼런스와의 유사도/피드백을 계산합니다."""
    reference = _sessions.get(session_id)
    if reference is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다. 레퍼런스를 다시 업로드해주세요.")

    raw = await file.read()
    frame_bgr = _decode_image(raw)
    if frame_bgr is None:
        raise HTTPException(status_code=400, detail="프레임 이미지를 디코딩할 수 없습니다.")

    result = pose_engine.process_live_frame(frame_bgr, reference)

    return JSONResponse(
        {
            "similarity": result.similarity,
            "feedback": result.feedback,
            "pose_detected": result.landmarks is not None,
        }
    )


@app.delete("/session/{session_id}")
def clear_session(session_id: str) -> dict:
    """세션(레퍼런스) 삭제."""
    _sessions.pop(session_id, None)
    return {"deleted": session_id}
