# Backend OOP architecture

Each Python module now has a focused object-oriented boundary while retaining
its previous public functions as compatibility facades where callers exist.

```mermaid
classDiagram
    class VCarCareApplication
    class DatabaseClient
    class AttendanceService
    class AuthorizationService
    class AccessControlPolicy
    class FaceEmbeddingService
    class LineMessagingService
    class LineLinkApplication
    class OrderApplication
    class OrderWorkflow
    class FinanceApplication
    class FinanceWorkflow
    class AttendanceBonusApplication
    VCarCareApplication --> OrderApplication
    VCarCareApplication --> FinanceApplication
    VCarCareApplication --> LineLinkApplication
    VCarCareApplication --> AttendanceBonusApplication
    OrderApplication --> OrderWorkflow
    FinanceApplication --> FinanceWorkflow
    AttendanceService --> DatabaseClient
    LineLinkApplication --> LineMessagingService
```

`config.py` remains a constants/configuration module intentionally: it has no
behaviour or mutable application workflow to encapsulate.
