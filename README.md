# wechat-ilink

腾讯微信 **iLink** 机器人渠道的通用纯协议层封装,零业务/数据库耦合。
覆盖:扫码登录、长轮询收消息(`getupdates`)、发送文本/图片/普通文件
(`sendmessage` + CDN 加密上传)、"正在输入"状态、CDN 媒体下载与解密。

- 不依赖任何业务代码,不含数据库、不含渠道绑定模型——所有参数显式传入。
- 依赖:`httpx`、`cryptography`、`certifi`。
  `aiohttp` 为可选依赖,用于应对个别拒绝 httpx TLS 握手的 CDN 节点
  (`pip install wechat-ilink[aiohttp]`);最终回退到系统 `curl`。
- 要求 Python >= 3.11。

## 安装

```bash
# 在新项目中直接以本地路径安装
pip install /path/to/wechat-ilink

# 或以可编辑模式安装(调试用)
pip install -e /path/to/wechat-ilink

# 可选:启用 aiohttp 下载回退
pip install "/path/to/wechat-ilink[aiohttp]"
```

## 快速上手:扫码登录 → 长轮询收消息 → 回复

```python
import time

from wechat_ilink import (
    SESSION_EXPIRED_ERRCODE,   # 会话过期错误码(-14)
    WeChatClient,
    normalize_wechat_message,
    split_wechat_text,
)

BASE_URL = "https://ilinkai.weixin.qq.com"

# 第 1 步:申请登录二维码(此阶段还没有 token,无需传)
# 把返回的 qrcode_img_content 展示给用户,用微信扫码
client = WeChatClient(BASE_URL)
qr = client.get_bot_qrcode()
print("请用微信扫码:", qr["qrcode_img_content"])

# 第 2 步:轮询扫码状态,直到用户在手机上确认
bot_token = ""
ilink_bot_id = ""
while True:
    status = client.get_qrcode_status(qr["qrcode"])
    if status.get("status") == "confirmed":
        bot_token = status["bot_token"]              # 业务凭证,需妥善保存(见 crypto 章节)
        ilink_bot_id = status.get("ilink_bot_id", "")
        # 确认后服务端可能下发区域化的 "baseurl",
        # 使用前务必用 sanitize_wechat_baseurl 校验,不可直接信任(防凭证泄露到恶意域名)
        break
    time.sleep(2)

# 第 3 步:带 token 长轮询收消息,并用 context_token 作为锚点回复
client = WeChatClient(BASE_URL, bot_token)
cursor = ""  # 游标:服务端下发的 get_updates_buf,需自行持久化,重启后从上次位置继续
while True:
    resp = client.get_updates(cursor, timeout_seconds=40.0)
    errcode = resp.get("errcode") or resp.get("ret") or 0
    if errcode == SESSION_EXPIRED_ERRCODE:
        # -14:会话过期,需要重新走扫码流程获取新 token
        break
    for raw in resp.get("msgs") or []:
        msg = normalize_wechat_message(raw, ilink_bot_id=ilink_bot_id)
        if msg is None:
            continue  # 自发消息、空消息、缺 context_token 的帧会被过滤掉
        # 回复超过 2000 字需分片发送(微信单条消息上限)
        for chunk in split_wechat_text(f"echo: {msg.text}"):
            client.send_message(msg.from_user_id, msg.context_token, chunk)
    cursor = str(resp.get("get_updates_buf") or cursor)  # 整批处理完才推进游标
```

> 注意:上面的 `while True` 只是最小示例,生产环境不能直接照搬。
> 具体需要自己实现什么,见下一节。

## 发送图片与普通文件

```python
from io import BytesIO

with WeChatClient(BASE_URL, bot_token) as client:
    client.send_image(to_user_id, context_token, image_bytes)
    client.send_file(
        to_user_id,
        context_token,
        BytesIO(report_bytes),
        "report.pdf",
        client_id="delivery-42",
    )
```

行为承诺与边界:

- `data` 接受 bytes-like 对象(`bytes`/`bytearray`/`memoryview`)或二进制流;
  流从当前位置读取,不 seek、不 rewind、不 close(不关闭调用方的流)。
- 库绝不把 `filename` 解释为路径:不展开 `~`、不读同名本地文件,它仅是
  协议展示字段;`filename` 去除首尾空白后必须非空。
- 空媒体无效;明文超过 25 MiB 会在发出任何 HTTP 请求前失败
  (`WeChatMediaError`,code 为 `media_empty` / `media_too_large`)。
- 出错时抛出 `WeChatMediaError`,其 `.code`(`WeChatErrorCode`)与
  `.stage` 是稳定的机器可读字段;iLink 业务 API 返回的非零 `ret/errcode`
  仍抛 `WeChatApiError`,通过 `.errcode` 读取。
- 出站流程为 `getuploadurl` → AES-128-ECB 加密 → CDN 上传 → `sendmessage`;
  `media.aes_key` 按 iLink 线上格式传递:16 字节 key 先转为 32 字符
  小写十六进制文本,再对该文本做标准 Base64;
  CDN 上传前校验 URL 为 HTTPS 且域名通过 `validate_wechat_host`,
  且不向 CDN 携带 bot token。CDN 上传不自动重试,是否整体重投由调用方决定。
- v0.2.0 不支持视频、原生语音、缩略图、caption 或批量附件。

出站媒体协议适配自腾讯 MIT 开源项目 Tencent/openclaw-weixin,归属见 `NOTICE`。

## 本包的边界:使用方需要自己决定的事

本包只负责"怎么跟微信说话"(协议层)。以下问题没有标准答案,
每个接入项目必须根据自己的业务自行实现:

1. **token 存哪**
   扫码确认后拿到的 `bot_token` 是长期凭证:存本地文件、数据库还是密钥管理服务?
   是否加密落盘(本包 `crypto` 模块提供了现成的 Fernet 工具)?
   密钥(`derive_fernet_key` 的入参)又从哪来?这些由项目自己的安全策略决定。

2. **收到消息之后怎么办**
   - **存哪**:消息是否落库?表结构怎么设计?要不要留存原始帧?
   - **给谁**:消息路由规则——这条消息交给哪个用户、哪个会话、哪个 agent?
     群聊和私聊是否分开处理?
   - **怎么回**:回复内容从哪来(echo、规则、调用 LLM……)是业务核心,
     本包只提供 `send_message` 这个出口,不替你产生回复。

3. **轮询循环怎么管理**
   轮询跑在哪个线程/进程?多账号(多个绑定)如何并行?
   断线如何退避重连(建议指数退避)?`-14` 会话过期后如何重新扫码并通知到人?
   进程如何优雅退出?

4. **游标持久化**
   `get_updates_buf` 游标存哪、何时推进?建议**整批消息处理完才推进**:
   批内中途崩溃时重启重拉,配合第 5 点去重即可不丢不重。

5. **幂等去重**
   用 `InboundMessage.event_id` 对消息去重。游标崩溃重拉、网络重试都会造成
   同一条消息重复到达,不去重就会重复回复。

6. **出站可靠性**
   `send_message` 失败要不要重试?要求"回复必达"的项目,建议引入出站队列
   (outbox):先把回复落库,再由后台任务投递并重试,避免进程崩溃丢回复。

以上各点都是有意不做的——它们正是不同项目之间真正不同的地方。

## 凭证安全存储

`bot_token` 是长期凭证,落库前建议加密。本包提供显式传 key 的 Fernet 工具:

```python
from wechat_ilink import derive_fernet_key, encrypt_secret, decrypt_secret

# 密钥派生算法为 b64(sha256(secret)):
# 同一个 secret 串在任何项目中派生出同一把 key,加密结果可跨项目互通
key = derive_fernet_key("my-channel-secret")

enc = encrypt_secret(bot_token, key)   # 加密后存数据库
assert decrypt_secret(enc, key) == bot_token  # 使用时解密
```

## API 摘要

### `wechat_ilink.client`

- `WeChatClient(base_url, bot_token="", *, transport=None, cdn_transport=None, random_bytes=os.urandom)` — iLink 端点的
  同步 httpx 客户端;`transport` 服务业务 API,`cdn_transport` 服务 CDN 上传
  (缺省复用 `transport`),两者都可传 `httpx.MockTransport` 用于离线测试;
  `random_bytes` 为出站媒体随机值(filekey/AES key)提供者。
  支持 `close()` 和 `with` 上下文管理。方法:
  - `get_bot_qrcode(local_token_list=None)` — 申请登录二维码
    (`bot_type=3`,最多携带 10 个本地 token)。
  - `get_qrcode_status(qrcode, *, verify_code=None, timeout_seconds=35.0)` —
    轮询扫码/确认状态。
  - `get_updates(get_updates_buf, *, timeout_seconds=40.0)` — 长轮询;
    返回 `msgs` 和下一个 `get_updates_buf`。注意检查 `errcode == -14`
    (`SESSION_EXPIRED_ERRCODE`,会话过期)。
  - `send_message(to_user_id, context_token, text, client_id="")` — 发送文本;
    `client_id` 为空时自动生成唯一值,用于服务端幂等去重。
    响应体带非零 errcode 时抛 `WeChatApiError`。
  - `send_image(to_user_id, context_token, data, *, client_id="")` — 发送图片
    (明文上限 25 MiB);成功返回 `None`,失败抛 `WeChatMediaError` /
    `WeChatApiError`(见"发送图片与普通文件"一节)。
  - `send_file(to_user_id, context_token, data, filename, *, client_id="")` —
    发送普通文件;`filename` 仅为展示字段,不会被当作路径读取。
  - `get_config(ilink_user_id, context_token="")` — 获取配置,如 `typing_ticket`。
  - `send_typing(ilink_user_id, typing_ticket, status=1)` — 1 = 正在输入,2 = 取消。
  - `download_media(context_token, media_id)` — 通过业务 API 下载二进制媒体
    (上限 25 MiB)。
  - `download_media_url(full_url, *, aes_key="", expected_size=0)` — CDN 下载,
    带域名校验 + AES-ECB 解密(见 `media` 模块)。
- `random_wechat_uin()` — 生成随机 `X-WECHAT-UIN` 请求头(每个请求重新生成)。
- 常量:`CHANNEL_VERSION`、`GETUPDATES_TIMEOUT_SECONDS`(40s)、
  `SESSION_EXPIRED_ERRCODE`(-14)。

### `wechat_ilink.normalize`

- `normalize_wechat_message(msg, *, ilink_bot_id="") -> InboundMessage | None` —
  归一化单条 `getupdates` 帧;对自发消息、空内容、缺 `context_token`/`event_id`
  的帧返回 `None`。
- `InboundMessage` — dataclass,字段:`event_id`、`from_user_id`、`to_user_id`、
  `session_id`、`group_id`、`context_token`、`text`、`is_group`、`raw`、
  `attachments`;属性:`conv_key` / `external_conv_id`(会话外部键)。
- `InboundAttachment` — `media_id`、`kind`(`"image"`/`"file"`)、`filename`、
  `content_type`、`download_params`(后续下载所需的 context_token / full_url /
  aes_key / declared_size / expected_size)。
- `is_self_message`、`extract_message_text`(文本 + 语音转写)、
  `extract_message_attachments`。

### `wechat_ilink.media`

- `decrypt_wechat_media(data, aes_key, *, expected_size=0)` — CDN 密文的
  AES-ECB/PKCS#7 解密(iLink 协议规定的线上格式,非通用加密)。
- `encrypt_wechat_media(data, key)` — 出站媒体的 AES-128-ECB/PKCS#7 加密
  (同为 iLink 线上格式,key 必须恰好 16 字节)。
- `aes_ecb_padded_size(plaintext_size)` — PKCS#7 补位后的密文大小
  (整块明文也会追加一个完整补位块)。
- `download_media_url(...)` — httpx 主路径,可选 aiohttp,最后回退系统 curl;
  下载前先用 `validate_wechat_host` 校验域名。
- `ensure_channel_media_size`、`MAX_CHANNEL_MEDIA_BYTES`(25 MiB)、
  `MAX_ENCRYPTED_CHANNEL_MEDIA_BYTES`。

### `wechat_ilink.security`

- `validate_wechat_host(host)` — 仅允许官方域名或 `*.weixin.qq.com` 子域
  (可拦截 `weixin.qq.com.evil.com` 这类伪装域名)。
- `sanitize_wechat_baseurl(url, *, default)` — 把服务端下发的 baseurl 归一化为
  `https://{host}`;域名不受信任时回退到 `default`,防止扫码重定向泄露凭证。
- `WECHAT_ALLOWED_HOSTS` — `("ilinkai.weixin.qq.com",)`。

### `wechat_ilink.text`

- `split_wechat_text(text, limit=2000)` — 按微信 2000 字上限切分出站文本,
  优先在 `\n\n` / `\n` / 空格处断句,实在找不到再硬切。
  `split_channel_text` 为通用别名。

### `wechat_ilink.crypto`

显式传 key 的 Fernet 工具,用于 `bot_token` 的落地加密:`derive_fernet_key`、
`encrypt_secret`、`decrypt_secret`(用法见上文"凭证安全存储")。

### `wechat_ilink.errors`

- `WeChatApiError(errcode, message)` — iLink 返回非零 errcode 或协议级媒体
  失败时抛出;`.errcode` 为数值错误码。
- `WeChatMediaError(code, stage, message, *, status_code=None)` — 出站媒体
  本地/协议错误;`.code`(`WeChatErrorCode`,稳定机器可读)与 `.stage`
  (`"read" | "encrypt" | "getuploadurl" | "cdn_upload"`)为稳定字段,
  `.status_code` 在 HTTP 层错误时给出状态码。人类可读消息不构成兼容承诺。

## 运行测试

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider
```

全部测试离线运行:纯函数 + `httpx.MockTransport` 模拟 HTTP,不发真实网络请求,
也不需要扫码登录、微信账号或 bot token。真实微信联调是独立的可选人工活动,
不属于自动化测试范围。
