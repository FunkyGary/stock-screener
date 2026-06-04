from datetime import datetime

from screener import cnyes


ARTICLE_RAISE = """
<html><body>
<h1>鉅亨速報 - Factset 最新調查：華邦電(2344-TW)目標價調升至158元，幅度約8.97%</h1>
<p>鉅亨網新聞中心 2026-05-27 20:10</p>
<p>根據FactSet最新調查，共11位分析師，對華邦電(2344-TW)提出目標價估值：
中位數由145元上修至158元，調升幅度8.97%。其中最高估值200元，最低估值100元。</p>
</body></html>
"""

ARTICLE_UPDATE = """
<html><body>
<h1>鉅亨速報 - Factset 最新調查：華通(2313-TW)EPS預估上修至8.04元，預估目標價為300元</h1>
<p>鉅亨網新聞中心 2026-05-26 08:10</p>
<p>根據FactSet最新調查，共7位分析師，對華通(2313-TW)做出2026年EPS預估：
中位數由7.88元上修至8.04元，其中最高估值8.78元，最低估值6.2元，預估目標價為300元。</p>
</body></html>
"""


class FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self, responses: dict[str, str]):
        self.responses = responses

    def get(self, url: str, **kwargs) -> FakeResponse:
        return FakeResponse(self.responses[url])


def test_parse_tw_valuation_target_raise_article():
    event = cnyes.parse_tw_valuation_article(
        ARTICLE_RAISE, "https://news.cnyes.com/news/id/6471601"
    )

    assert event is not None
    assert event["symbol"] == "2344.TW"
    assert event["action"] == "raise"
    assert event["previous_target"] == 145.0
    assert event["target_price"] == 158.0
    assert event["raise_pct"] == 158.0 / 145.0 - 1.0
    assert event["published_at"] == "2026-05-27T20:10:00+08:00"
    assert event["analyst_count"] == 11
    assert event["target_high"] == 200.0
    assert event["target_low"] == 100.0


def test_parse_tw_valuation_eps_article_as_update_not_raise():
    event = cnyes.parse_tw_valuation_article(
        ARTICLE_UPDATE, "https://news.cnyes.com/news/id/6451730"
    )

    assert event is not None
    assert event["symbol"] == "2313.TW"
    assert event["action"] == "update"
    assert event["previous_target"] is None
    assert event["target_price"] == 300.0


def test_fetch_recent_tw_valuation_events_filters_to_last_two_days():
    category = """
    <a href="https://news.cnyes.com/news/id/6471601">new</a>
    <a href="/news/id/6451730">old</a>
    <a href="/news/id/6451730">duplicate</a>
    """
    session = FakeSession(
        {
            "https://news.cnyes.com/news/cat/report": category,
            "https://news.cnyes.com/news/cat/tw_stock_valuation": "",
            "https://news.cnyes.com/news/id/6471601": ARTICLE_RAISE,
            "https://news.cnyes.com/news/id/6451730": ARTICLE_UPDATE,
        }
    )

    events = cnyes.fetch_recent_tw_valuation_events(
        days=2,
        session=session,
        now=datetime.fromisoformat("2026-05-28T12:00:00+00:00"),
    )

    assert [event["symbol"] for event in events] == ["2344.TW"]
