from dataclasses import dataclass

from app.exchange import ExchangeSession


@dataclass
class SharedRTOState:
    session: ExchangeSession | None = None
    connected: bool = False
