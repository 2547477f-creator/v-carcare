# LINE integration OOAD / UML

`LineMessagingService` is the integration boundary for LINE. It owns the
credentials, HTTP transport, token verification, webhook replies, and push
notifications. Flask handlers delegate to it rather than making HTTP calls.

```mermaid
classDiagram
    class LineMessagingService {
        +channel_secret
        +channel_access_token
        +login_channel_id
        -_opener
        +is_configured() bool
        +verify_access_token(access_token) dict | None
        +verify_id_token(id_token) dict | None
        +reply(reply_token, message) bool
        +notify(line_user_id, message) bool
        -_load_json(request) dict
        -_send(url, body, failure_log) bool
    }
    class LineLinkRoutes {
        +POST /api/liff/register
        +POST /api/line/link/unlink
        +GET /api/vehicles/:id/line-status
        +POST /callback
    }
    class OrderRoutes {
        +update order status
    }
    LineLinkRoutes --> LineMessagingService : verifies / replies
    OrderRoutes --> LineMessagingService : sends push notification
```

The service accepts an injectable HTTP opener, so LINE interactions can be
tested without contacting the real API. Existing module-level functions in
`line_bot.py` are left available for backwards compatibility.
