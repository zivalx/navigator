"""Parser tests for the CNN Fear & Greed and alternative.me Crypto Fear &
Greed providers, against canned JSON fixtures (no network).
"""
from app.providers.alternative_me import AlternativeMeFearGreedProvider
from app.providers.cnn import CNNFearGreedProvider


CNN_FIXTURE = {
    "fear_and_greed": {
        "score": 38.0,
        "rating": "fear",
        "timestamp": "2026-07-23T15:00:00+00:00",
        "previous_close": 42.0,
        "previous_1_week": 41.2,
        "previous_1_month": 28.5,
        "previous_1_year": 76.4,
    },
    "fear_and_greed_historical": {"data": []},
}


def test_cnn_parse_extracts_score_rating_and_change():
    result = CNNFearGreedProvider.parse(CNN_FIXTURE)

    assert result["value"] == 38.0
    assert result["rating"] == "fear"
    assert result["change"] == -4.0


def test_cnn_parse_normalizes_multiword_rating():
    fixture = {
        "fear_and_greed": {
            "score": 92.0,
            "rating": "extreme greed",
            "previous_close": 90.0,
        }
    }
    result = CNNFearGreedProvider.parse(fixture)

    assert result["rating"] == "extreme_greed"
    assert result["change"] == 2.0


def test_cnn_parse_missing_block_returns_none():
    result = CNNFearGreedProvider.parse({})

    assert result["value"] is None
    assert result["rating"] is None
    assert result["change"] is None


ALT_ME_FIXTURE = {
    "name": "Fear and Greed Index",
    "data": [
        {"value": "31", "value_classification": "Fear", "timestamp": "1753300000"},
        {"value": "33", "value_classification": "Fear", "timestamp": "1753213600"},
    ],
}


def test_alternative_me_parse_extracts_value_rating_and_change():
    result = AlternativeMeFearGreedProvider.parse(ALT_ME_FIXTURE)

    assert result["value"] == 31.0
    assert result["rating"] == "fear"
    assert result["change"] == -2.0


def test_alternative_me_parse_normalizes_multiword_classification():
    fixture = {
        "data": [
            {"value": "10", "value_classification": "Extreme Fear"},
            {"value": "15", "value_classification": "Fear"},
        ]
    }
    result = AlternativeMeFearGreedProvider.parse(fixture)

    assert result["rating"] == "extreme_fear"
    assert result["change"] == -5.0


def test_alternative_me_parse_single_entry_has_no_change():
    fixture = {"data": [{"value": "50", "value_classification": "Neutral"}]}
    result = AlternativeMeFearGreedProvider.parse(fixture)

    assert result["value"] == 50.0
    assert result["rating"] == "neutral"
    assert result["change"] is None


def test_alternative_me_parse_empty_data_returns_none():
    result = AlternativeMeFearGreedProvider.parse({"data": []})

    assert result["value"] is None
    assert result["rating"] is None
    assert result["change"] is None
