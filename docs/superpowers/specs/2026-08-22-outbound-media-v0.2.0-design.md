# wechat-ilink v0.2.0 出站媒体设计

## 目标

`wechat-ilink` v0.2.0 在不引入任何宿主业务依赖的前提下，增加微信 iLink 出站图片和普通文件发送能力。实现以腾讯 MIT 开源项目 `Tencent/openclaw-weixin` 的纯 iLink 协议逻辑为第一事实来源，并保持现有公开 API 向后兼容。

本版本不实现视频、原生语音、图片缩略图、caption、批量附件、文件路径解析、重试队列或业务幂等。

## 设计原则

- 仓库是唯一源码事实源，不依赖 FeiBot、StaffDeck、数据库、Web 框架或 OpenClaw。
- 媒体入口只接收内存字节或二进制流，不读取路径、`.env` 或其他宿主状态。
- 业务 API 和 CDN 上传均使用 `httpx`，且均可注入 `MockTransport`。
- 不向 CDN 携带 bot token，并在上传前验证服务端下发 URL 的 scheme 和微信域名。
- 新错误通过稳定机器可读错误码分支；人类可读消息不构成兼容承诺。
- 完整媒体发送对宿主不是原子操作：CDN 上传成功后 `sendmessage` 仍可能失败，重试和 outbox 属于宿主责任。

## 已核对的协议事实

腾讯官方实现定义了以下流程：

1. 对明文计算字节数和小写十六进制 MD5。
2. 生成 16 字节随机 `filekey` 和 16 字节随机 AES key；两者在 `getuploadurl` 请求中都以小写十六进制表示。
3. 使用 AES-128-ECB 和 PKCS#7 补位加密。即使明文长度恰好是 16 的倍数，也要添加一个完整补位块。
4. `getuploadurl` 的 `media_type` 图片为 `1`，文件为 `3`；第一版两者均传 `no_need_thumb=true`。
5. CDN 上传是 `POST application/octet-stream`，请求体为密文。
6. CDN 成功响应为 HTTP 200，下载参数位于大小写不敏感的 `x-encrypted-param` 响应头。
7. 消息中 `media.aes_key` 是 16 字节 AES key 的 32 字符小写十六进制文本再做标准 Base64，`encrypt_type=1`。它不是原始 key 字节的 Base64。
8. 图片 `image_item.mid_size` 是密文长度；文件 `file_item.len` 是明文长度的十进制字符串。
9. `sendmessage` 每次只携带一个媒体 item，`message_type=2`，`message_state=2`。

## 公开 API

### `WeChatClient` 构造器

现有参数保持有效，新增可选显式依赖：

```python
WeChatClient(
    base_url: str,
    bot_token: str = "",
    *,
    transport: httpx.BaseTransport | None = None,
    cdn_transport: httpx.BaseTransport | None = None,
    random_bytes: Callable[[int], bytes] = os.urandom,
)
```

- `transport` 继续服务 iLink 业务 API。
- `cdn_transport` 服务 CDN 上传；未指定时复用 `transport`，便于用单个 `MockTransport` 离线覆盖全流程。
- `random_bytes` 为 `filekey`、AES key 和现有 `X-WECHAT-UIN` 之外的新媒体随机值提供者；实现将校验它返回的字节数。
- `close()` 关闭由客户端创建的 HTTP client，同一 client 被复用时不重复关闭。

### 图片发送

```python
WeChatClient.send_image(
    to_user_id: str,
    context_token: str,
    data: bytes | bytearray | memoryview | BinaryIO,
    *,
    client_id: str = "",
) -> None
```

### 文件发送

```python
WeChatClient.send_file(
    to_user_id: str,
    context_token: str,
    data: bytes | bytearray | memoryview | BinaryIO,
    filename: str,
    *,
    client_id: str = "",
) -> None
```

两个方法的行为承诺：

- 流从当前位置开始读取，方法不 seek、不 rewind、不 close。
- 输入会被有界地读入内存；最多保留 25 MiB 明文，检测超限时可多读一个字节。
- 空媒体无效。`filename` 必须是去除首尾空白后的非空字符串；基座不把它解释为路径，不自动读文件。
- 调用方传入的非空 `client_id` 原样用于 `sendmessage`，便于调用方对失败投递使用同一幂等键。空值使用现有自动生成规则。
- 成功时与现有 `send_message` 一致返回 `None`。

### 纯函数

`wechat_ilink.media` 新增并从包顶层导出：

```python
aes_ecb_padded_size(plaintext_size: int) -> int
encrypt_wechat_media(data: bytes, key: bytes) -> bytes
```

这两个函数是稳定的纯协议工具 API。MD5、上传 URL 组装和 item payload builder 保持包内私有，避免扩大 v0.2.0 兼容面。

## 内部组件与数据流

### 有界输入读取

`media.py` 中的私有 helper 把 bytes-like 值规范为 `bytes`，或以有界分块方式读取具有 `read()` 的二进制流。它必须拒绝文本流、`read()` 返回非 bytes-like 值、空内容和超限内容。不对文件名、MIME 或图片格式做业务推断。

### 媒体准备

读取完成后：

1. `rawsize = len(plaintext)`。
2. `rawfilemd5 = md5(plaintext).hexdigest()`。
3. 分别请求 16 字节 `filekey_bytes` 和 16 字节 `aes_key`。
4. `filekey = filekey_bytes.hex()`，`aeskey = aes_key.hex()`。
5. `ciphertext = encrypt_wechat_media(plaintext, aes_key)`。
6. `filesize = len(ciphertext)`，并断言其等于 `aes_ecb_padded_size(rawsize)`。

明文、密文和密钥只存在于当前调用内存中，不写入文件系统或日志。

### `getuploadurl`

`WeChatClient` 使用现有业务 headers 发送：

```json
{
  "filekey": "<32 lowercase hex chars>",
  "media_type": 1,
  "to_user_id": "<target>",
  "rawsize": 12345,
  "rawfilemd5": "<lowercase hex md5>",
  "filesize": 12352,
  "no_need_thumb": true,
  "aeskey": "<32 lowercase hex chars>",
  "base_info": {"channel_version": "0.2.0"}
}
```

文件仅将 `media_type` 替换为 `3`。必须检查 HTTP 状态、JSON object 类型、非零 `ret/errcode` 及上传 URL 字段。

响应选择顺序：

1. 非空 `upload_full_url`；
2. 否则使用非空 `upload_param` 与内置官方 CDN base URL 、`filekey` 组装兼容 URL；
3. 两者均缺失则报协议响应错误。

兼容 URL 只用腾讯官方组装规则，不将 CDN base URL 暴露为业务配置。

### CDN 上传

上传前必须使用既有 `validate_wechat_host` 规则并额外要求 HTTPS。请求不携带 iLink Authorization headers：

```http
POST <validated CDN URL>
Content-Type: application/octet-stream
Content-Length: <ciphertext length>

<ciphertext>
```

v0.2.0 不在基座内自动重试 CDN 上传。上传是有副作用操作，官方协议没有提供可验证的上传幂等承诺；隐式重试可能生成孤立媒体。调用方可基于结构化错误决定是否重试整个投递。

HTTP 200 后必须读取非空 `x-encrypted-param`。其他状态或缺少响应头均失败，不尝试从空 body 或未定义 JSON 字段推测下载参数。

### `sendmessage` payload

图片 item：

```json
{
  "type": 2,
  "image_item": {
    "media": {
      "encrypt_query_param": "<x-encrypted-param>",
      "aes_key": "<base64 of the 32-character lowercase hex key text>",
      "encrypt_type": 1
    },
    "mid_size": 12352
  }
}
```

文件 item：

```json
{
  "type": 4,
  "file_item": {
    "media": {
      "encrypt_query_param": "<x-encrypted-param>",
      "aes_key": "<base64 of the 32-character lowercase hex key text>",
      "encrypt_type": 1
    },
    "file_name": "report.pdf",
    "len": "12345"
  }
}
```

`client.py` 将提取私有 `_send_item(...)`，让现有 `send_message` 和新媒体方法共享一致的 envelope、HTTP 检查和 API 错误处理。重构后现有文本 payload 和返回值不变。

## 错误模型

保留现有 `WeChatApiError(errcode, message)` 和 `.errcode`。iLink 业务 API 返回非零 `ret/errcode` 时继续抛出该类，包括 `getuploadurl` 和 `sendmessage`。

新增：

```python
class WeChatErrorCode(StrEnum):
    INVALID_MEDIA_INPUT = "invalid_media_input"
    MEDIA_EMPTY = "media_empty"
    MEDIA_TOO_LARGE = "media_too_large"
    MEDIA_READ_FAILED = "media_read_failed"
    MEDIA_ENCRYPTION_FAILED = "media_encryption_failed"
    UPLOAD_URL_HTTP_ERROR = "upload_url_http_error"
    UPLOAD_URL_REJECTED = "upload_url_rejected"
    UPLOAD_URL_INVALID_RESPONSE = "upload_url_invalid_response"
    CDN_UPLOAD_HTTP_ERROR = "cdn_upload_http_error"
    CDN_UPLOAD_INVALID_RESPONSE = "cdn_upload_invalid_response"

class WeChatMediaError(Exception):
    code: WeChatErrorCode
    stage: str
    status_code: int | None
```

`stage` 稳定取值为 `"read" | "encrypt" | "getuploadurl" | "cdn_upload"`。`getuploadurl` 的 HTTP/连接错误包装为 `UPLOAD_URL_HTTP_ERROR`；`sendmessage` 失败已由 `WeChatApiError` 或现有 `httpx.HTTPStatusError` 表达，不再增加会丢失服务端 `errcode` 的包装码。

本版本只保证媒体本地/协议错误的 `.code` 与 `.stage` 稳定。HTTP 传输层连接异常保留 `httpx` 原始异常链作为 `__cause__`。

## 大小限制与内存

- 图片和文件统一使用现有 `MAX_CHANNEL_MEDIA_BYTES = 25 * 1024 * 1024` 明文上限。
- 密文最大值按 PKCS#7 严格计算为 `MAX_CHANNEL_MEDIA_BYTES + 16`。现有入站兼容常量 `MAX_ENCRYPTED_CHANNEL_MEDIA_BYTES` 不在本次破坏性缩小，出站校验使用精确公式。
- 实现会同时持有最多约 25 MiB 明文和约 25 MiB 密文。这是 v0.2.0 为了保持简单、可测、与 AES-ECB 上传完整 body 相容而接受的上界。

## 安全性

- CDN URL 必须是 HTTPS，且 hostname 通过 `validate_wechat_host`。
- URL 校验发生在任何密文上传之前。
- bot token 只发往经验证的 iLink `base_url`；CDN 客户端只设置内容类型和长度。
- 不记录媒体字节、AES key、完整 CDN URL、`upload_param` 或 `x-encrypted-param`。
- AES-ECB 只是 iLink CDN 线上格式，API 文档明确警告不得用于通用存储加密。
- `filename` 仅作为协议展示字段。基座不展开 `~`、不解析相对/绝对路径、不读取同名本地文件。

## 测试驱动开发方案

实施时先添加失败测试，再写最小实现。覆盖：

1. 加密向量：固定 16 字节 key、非整块明文、整块明文的额外补位、密文大小公式、无效 key。
2. 输入：`bytes`、`bytearray`、`memoryview`、`BytesIO`、分块流、不关闭流、文本流、读取异常、空输入。
3. 大小：恰好 25 MiB 成功，25 MiB + 1 失败，超限时不发出任何 HTTP 请求。
4. `getuploadurl`：图片/文件 `media_type`、明文大小、MD5、密文大小、`filekey`、`aeskey`、`no_need_thumb`、`base_info`。
5. `getuploadurl` 错误：非零 `ret/errcode`、空响应、非 object JSON、无效 JSON、缺少上传 URL。
6. CDN：经验证的 URL、POST、content type/length、确定性密文 body、大小写不敏感响应头。
7. CDN 错误：HTTP 4xx/5xx、缺失/空 `x-encrypted-param`、非 HTTPS、非微信域名。
8. 消息 payload：图片 `mid_size`、文件 `file_name/len`、Base64 key、`encrypt_type=1`、`context_token`、显式与自动 `client_id`。
9. 回归：现有文本发送、入站媒体下载/解密、normalize、扫码和长轮询测试全部通过。

所有 HTTP 测试使用 `httpx.MockTransport`，禁止访问真实 iLink 或 CDN。

## 文件变更范围

- `wechat_ilink/media.py`：出站加密、大小计算和有界读取。
- `wechat_ilink/client.py`：HTTP 依赖、`getuploadurl`、CDN 上传、媒体发送和统一 item 发送。
- `wechat_ilink/errors.py`：稳定媒体错误码和异常。
- `wechat_ilink/__init__.py`：公开导出。
- `tests/test_media.py`：纯媒体单元测试。
- `tests/test_outbound_media.py`：端到端离线协议测试。
- `tests/test_client.py`：现有文本兼容回归和 client 生命周期。
- `README.md`：出站媒体 API、边界、限制、错误码、正确测试命令和参考声明。
- `pyproject.toml` 与 `wechat_ilink/client.py`：包版本和 `CHANNEL_VERSION` 统一修正为 `0.2.0`。
- `NOTICE`：记录对 Tencent/openclaw-weixin 的协议参考和 MIT 归属。若实现构成实质性移植，同时在相关源文件保留版权与许可声明。

## 版本与兼容性

- `pyproject.toml` 项目版本从初始提交中的 `1.0.0` 修正为 `0.2.0`。
- `CHANNEL_VERSION` 同步修正为 `0.2.0`。
- 不删除、改名或改变任何现有公开函数、类、常量的调用方式。
- `send_message` 仍返回 `None`，原有 payload 保持不变。
- 不发布 tag，不 push，不修改 FeiBot 或 StaffDeck。v0.2.0 tag 在实现完成并获得用户单独确认后再讨论。

## 完成标准

- 宿主可仅用 bytes 或二进制流发送图片和普通文件。
- 整个上传与发送流程可由 `httpx.MockTransport` 离线验证。
- 协议字段、AES 格式、CDN 请求和 `sendmessage` payload 与腾讯官方实现一致。
- 大小、输入、协议响应和 CDN 错误具有稳定结构化错误码。
- 新增测试与全部现有测试均通过，测试过程不访问网络。
- README 准确说明公开 API、基座边界、错误行为、限制和腾讯 MIT 参考来源。
