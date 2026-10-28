# Face recognition OOAD / UML

`FaceEmbeddingService` owns the model lifecycle and image-to-embedding use
case. Flask routes and the startup script retain the existing module-level
functions, which delegate to a shared service instance.

```mermaid
classDiagram
    class FaceEmbeddingService {
        -Path _model_root
        -str _model_name
        -list~str~ _providers
        -tuple _detection_size
        -FaceAnalysis _app
        -Lock _app_lock
        +create_embedding(image_path) list~float~ | None
        +warm_up(static_folder) None
        -_get_app() FaceAnalysis
        -_face_area(face) float
    }
    class FaceAnalysis {
        +prepare(ctx_id, det_size)
        +get(image) Face[]
    }
    class Face {
        +bbox
        +normed_embedding
    }
    class CompatibilityFacade {
        +create_face_embedding(image_path)
        +warm_up_face_model(static_folder)
    }
    FaceEmbeddingService --> FaceAnalysis : creates / owns
    FaceAnalysis --> Face : detects
    CompatibilityFacade --> FaceEmbeddingService : delegates
```

```mermaid
sequenceDiagram
    participant Caller as Route / Job
    participant Facade as Compatibility facade
    participant Service as FaceEmbeddingService
    participant Model as InsightFace
    Caller->>Facade: create_face_embedding(path)
    Facade->>Service: create_embedding(path)
    Service->>Service: _get_app() (lazy, lock-protected)
    Service->>Model: get(image)
    Model-->>Service: detected faces
    Service-->>Facade: largest face embedding / None
    Facade-->>Caller: embedding / None
```

The service owns mutable model state, accepts injectable dependencies for
tests, and the facade isolates existing callers from the OOP refactor.
