from __future__ import annotations

from agent import extract_messages
from brain import extract_user_facts, keywords, search_catalog


def test_extract_messages() -> None:
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {"from": "254700000000", "text": {"body": "Hello"}}
                            ]
                        }
                    }
                ]
            }
        ]
    }
    assert extract_messages(payload) == [{"from": "254700000000", "text": "Hello"}]


def test_extract_user_facts() -> None:
    assert extract_user_facts("My location is Nairobi.") == ["My location is Nairobi"]


def test_keywords() -> None:
    assert "delivery" in keywords("What is your delivery price?")


def test_search_catalog(tmp_path) -> None:
    catalog = tmp_path / "catalog.md"
    catalog.write_text("Delivery is available in Nairobi.\n\nRefunds take 3 days.", encoding="utf-8")
    assert search_catalog(catalog, ["delivery", "nairobi"]) == ["Delivery is available in Nairobi."]

