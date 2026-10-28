"""บริการสร้าง embedding และอุ่นโมเดล InsightFace"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import cv2
from insightface.app import FaceAnalysis

from .config import ROOT_DIR


class FaceEmbeddingService:
    """Create face embeddings and lazily manage one InsightFace model."""

    def __init__(
        self,
        model_root: str | os.PathLike[str] | None = None,
        *,
        model_name: str = "buffalo_sc",
        providers: Sequence[str] = ("CPUExecutionProvider",),
        detection_size: tuple[int, int] = (320, 320),
        analysis_factory: Callable[..., Any] = FaceAnalysis,
        image_loader: Callable[[str], Any] = cv2.imread,
    ) -> None:
        self._model_root = Path(model_root or Path(ROOT_DIR) / ".models")
        self._model_name = model_name
        self._providers = list(providers)
        self._detection_size = detection_size
        self._analysis_factory = analysis_factory
        self._image_loader = image_loader
        self._app: Any | None = None
        self._app_lock = threading.Lock()

    def create_embedding(self, image_path: str | os.PathLike[str]) -> list[float] | None:
        """Return the largest detected face's normalized embedding, if any."""
        app = self._get_app()
        image = self._image_loader(os.fspath(image_path))
        if image is None:
            return None
        faces = app.get(image)
        if not faces:
            return None
        largest_face = max(faces, key=self._face_area)
        return largest_face.normed_embedding.tolist()

    def warm_up(self, static_folder: str | os.PathLike[str]) -> None:
        """Initialize the model without making application startup fail."""
        try:
            self.create_embedding(Path(static_folder) / "faces" / "warmup.jpg")
            print("[face] InsightFace model ready")
        except Exception as exc:
            print(f"[face] model warm-up failed: {exc}")

    def _get_app(self) -> Any:
        if self._app is None:
            with self._app_lock:
                if self._app is None:
                    app = self._analysis_factory(
                        name=self._model_name,
                        root=os.fspath(self._model_root),
                        providers=self._providers,
                    )
                    app.prepare(ctx_id=-1, det_size=self._detection_size)
                    self._app = app
        return self._app

    @staticmethod
    def _face_area(face: Any) -> float:
        bbox = face.bbox
        return (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])


face_embedding_service = FaceEmbeddingService()


def create_face_embedding(image_path: str | os.PathLike[str]) -> list[float] | None:
    """Compatibility facade for ``FaceEmbeddingService.create_embedding``."""
    return face_embedding_service.create_embedding(image_path)


def warm_up_face_model(static_folder: str | os.PathLike[str]) -> None:
    """Compatibility facade for ``FaceEmbeddingService.warm_up``."""
    face_embedding_service.warm_up(static_folder)
