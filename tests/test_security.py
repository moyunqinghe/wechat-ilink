from wechat_ilink import sanitize_wechat_baseurl, validate_wechat_host


def test_validate_wechat_host_rules() -> None:
    assert validate_wechat_host("ilinkai.weixin.qq.com") is True
    assert validate_wechat_host("szilinkai.weixin.qq.com") is True
    assert validate_wechat_host("ILINKAI.WEIXIN.QQ.COM") is True
    assert validate_wechat_host("ilinkai.weixin.qq.com:443") is True
    assert validate_wechat_host("evil.com") is False
    assert validate_wechat_host("weixin.qq.com.evil.com") is False
    assert validate_wechat_host("") is False


def test_sanitize_wechat_baseurl_normalizes_and_falls_back() -> None:
    default = "https://ilinkai.weixin.qq.com"
    # 合法域名:规范为 https://{host},丢弃 path/query
    assert sanitize_wechat_baseurl("https://szilinkai.weixin.qq.com/x/y?a=1", default=default) == (
        "https://szilinkai.weixin.qq.com"
    )
    assert sanitize_wechat_baseurl("http://ilinkai.weixin.qq.com", default=default) == default
    # 非法:回退默认
    assert sanitize_wechat_baseurl("https://evil.com", default=default) == default
    assert sanitize_wechat_baseurl("https://weixin.qq.com.evil.com", default=default) == default
    assert sanitize_wechat_baseurl("not-a-url", default=default) == default
