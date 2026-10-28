# Finance OOAD / UML

`FinanceApplication` is the composition root for finance routes. It validates
the collaborators provided by the main application and registers routes without
using mutable module globals. `FinanceWorkflow` contains request validation and
normalization rules shared by finance endpoints.

```mermaid
classDiagram
    class FinanceApplication {
        -Mapping _dependencies
        +register_routes(app)
    }
    class FinanceWorkflow {
        +TRANSACTION_TYPES
        +PAYMENT_METHODS
        +FUND_ACTIVITIES
        +normalize_transaction_type(value) str
        +normalize_payment_method(value) str
        +normalize_fund_activity(value) str
        +pagination(page, page_size) tuple
    }
    class FlaskFinanceRoutes {
        +GET /api/finance/summary
        +POST /api/finance/transactions
        +GET/PUT /api/central-fund
        +POST /api/central-fund/open
        +POST /api/central-fund/deposits
    }
    FinanceApplication --> FlaskFinanceRoutes : registers
    FlaskFinanceRoutes --> FinanceWorkflow : applies rules
    FlaskFinanceRoutes --> CentralFundServices : injected collaborators
```

The previous `register_finance_routes(app, dependencies)` function remains as
a compatibility facade around `FinanceApplication`.
