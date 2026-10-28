# Orders OOAD / UML

The order module now separates composition and domain rules from Flask route
registration. `OrderApplication` makes the dependencies supplied by the main
application explicit, while `OrderWorkflow` centralizes valid state and payment
rules used by order handlers.

```mermaid
classDiagram
    class OrderApplication {
        -Mapping _dependencies
        +register_routes(app)
    }
    class OrderWorkflow {
        +VALID_STATUSES
        +PAYMENT_METHODS
        +NOTIFIABLE_STATUSES
        +is_valid_status(status) bool
        +is_valid_payment_method(payment_method) bool
        +status_timestamp_sql(status) str
    }
    class FlaskOrderRoutes {
        +GET/POST /api/orders
        +PUT /api/orders/:id/status
        +POST /api/orders/:id/payment
        +GET /api/track/*
        +GET/PUT /api/vehicles/*
    }
    OrderApplication --> FlaskOrderRoutes : registers
    FlaskOrderRoutes --> OrderWorkflow : applies rules
    FlaskOrderRoutes --> LineMessagingService : injected notification callback
```

The former `register_order_routes(app, dependencies)` API remains as a
compatibility facade around `OrderApplication`.
