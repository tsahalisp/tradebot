"""Lazy config + persisted settings (~/.tradebot/settings.json). Mirrors EdgeBot."""
import json, pathlib


class Config:
    def __init__(self):
        self.settings_path = pathlib.Path.home() / ".tradebot" / "settings.json"
        s = self._load()
        self.bankroll = float(s.get("bankroll", 10000))
        self.fee_bps = float(s.get("fee_bps", 10))
        self.products = s.get("products", ["SPY", "AAPL", "MSFT", "NVDA"])

    def _load(self):
        try:
            return json.loads(self.settings_path.read_text())
        except Exception:
            return {}

    def settings(self):
        return {"bankroll": self.bankroll, "fee_bps": self.fee_bps, "products": self.products}


config = Config()
