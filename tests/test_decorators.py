"""Tests for MQTT decorators and topic_matches (§4.7 $ rule)."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mqttd.decorators import topic_matches, parse_shared_subscription


def test_topic_matches_dollar_rule():
    """§4.7: Filters starting with # or + must NOT match topic names beginning with $."""
    # $ topic must not match wildcard filters
    assert topic_matches('#', '$SYS/foo') is False
    assert topic_matches('+', '$SYS') is False
    assert topic_matches('+/foo', '$SYS/foo') is False
    assert topic_matches('#', '$foo') is False
    # Non-wildcard filter can still match $ topic (exact or with + in middle)
    assert topic_matches('$SYS/foo', '$SYS/foo') is True
    assert topic_matches('$SYS/+', '$SYS/foo') is True


def test_parse_shared_subscription():
    """§4.8.2: $share/{ShareName}/{filter} parsing."""
    assert parse_shared_subscription("sensors/+") is None
    assert parse_shared_subscription("$share/grp1/sensors/+") == ("grp1", "sensors/+")
    assert parse_shared_subscription("$share/mygroup/a/b/c") == ("mygroup", "a/b/c")
    assert parse_shared_subscription("$share/") is None
    assert parse_shared_subscription("$share/x") is None  # no inner filter


def test_topic_matches_basic():
    """Basic wildcard matching unchanged."""
    assert topic_matches('sensor/temp', 'sensor/temp') is True
    assert topic_matches('sensor/+', 'sensor/temp') is True
    assert topic_matches('sensor/#', 'sensor/temp/humidity') is True
    assert topic_matches('sensor/+/humidity', 'sensor/room1/humidity') is True
    assert topic_matches('sensor/temp', 'sensor/humidity') is False
