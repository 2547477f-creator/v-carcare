"""บริการสร้าง embedding และอุ่นโมเดล InsightFace"""

import os

import cv2
from insightface.app import FaceAnalysis

from .config import ROOT_DIR


_face_app = None


def create_face_embedding(image_path):
    global _face_app
    if _face_app is None:
        _face_app = FaceAnalysis(
            name='buffalo_sc', root=os.path.join(ROOT_DIR, '.models'),
            providers=['CPUExecutionProvider'],
        )
        _face_app.prepare(ctx_id=-1, det_size=(320, 320))
    image = cv2.imread(image_path)
    if image is None:
        return None
    faces = _face_app.get(image)
    if not faces:
        return None
    face = max(faces, key=lambda item: (item.bbox[2] - item.bbox[0]) * (item.bbox[3] - item.bbox[1]))
    return face.normed_embedding.tolist()


def warm_up_face_model(static_folder):
    try:
        create_face_embedding(os.path.join(static_folder, 'faces', 'warmup.jpg'))
        print('[face] InsightFace model ready')
    except Exception as exc:
        print(f'[face] model warm-up failed: {exc}')
