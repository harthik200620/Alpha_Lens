"""Test suite for Alpha_Lens backend modules.

Inserts the backend/ directory on sys.path so tests can import the sibling
modules (market_calendar, ticker_utils, news_rules, ...) regardless of the
working directory they're run from.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
