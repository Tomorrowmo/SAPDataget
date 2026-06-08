from app.query_limits import parse_requested_top


def test_parse_requested_top_default():
    assert parse_requested_top("", default_top=200, max_top=1000) == 200


def test_parse_requested_top_all():
    assert parse_requested_top("报告清单全部", default_top=200, max_top=1000) == 1000
    assert parse_requested_top("show all reports", default_top=200, max_top=1000) == 1000


def test_parse_requested_top_number_patterns():
    assert parse_requested_top("报告清单前1条", default_top=200, max_top=1000) == 1
    assert parse_requested_top("report list top 50", default_top=200, max_top=1000) == 50
    assert parse_requested_top("我要100条", default_top=200, max_top=1000) == 100


def test_parse_requested_top_clamped():
    assert parse_requested_top("前99999条", default_top=200, max_top=1000) == 1000
    assert parse_requested_top("前0条", default_top=200, max_top=1000) == 200
