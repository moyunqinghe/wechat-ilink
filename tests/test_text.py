from wechat_ilink import split_channel_text, split_wechat_text


def test_split_text_short() -> None:
    assert split_wechat_text("") == []
    assert split_wechat_text("短文本") == ["短文本"]
    assert split_wechat_text("x" * 2000) == ["x" * 2000]


def test_split_text_prefers_paragraph_break() -> None:
    head = "a" * 1500
    tail = "b" * 1200
    text = head + "\n\n" + tail
    chunks = split_wechat_text(text)
    assert chunks == [head, tail]


def test_split_text_falls_back_to_newline_then_space() -> None:
    head = "a" * 1500
    tail = "b" * 900
    chunks = split_wechat_text(head + "\n" + tail)
    assert chunks == [head, tail]

    head_space = "a" * 1500
    tail_space = "b" * 900
    chunks = split_wechat_text(head_space + " " + tail_space)
    assert chunks == [head_space, tail_space]


def test_split_text_hard_cut_without_boundaries() -> None:
    text = "x" * 4500
    chunks = split_wechat_text(text)
    assert [len(chunk) for chunk in chunks] == [2000, 2000, 500]
    assert "".join(chunks) == text


def test_split_channel_text_custom_limit() -> None:
    assert split_channel_text("x" * 4500, limit=2000) == split_wechat_text("x" * 4500)
