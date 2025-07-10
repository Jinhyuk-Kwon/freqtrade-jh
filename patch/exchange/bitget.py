"""Bitget exchange subclass"""

import logging
from datetime import datetime, timedelta
from typing import Any

import ccxt

from freqtrade.constants import BuySell
from freqtrade.enums import MarginMode, PriceType, TradingMode
from freqtrade.exceptions import DDosProtection, ExchangeError, OperationalException, TemporaryError
from freqtrade.exchange import Exchange
from freqtrade.exchange.common import retrier
from freqtrade.exchange.exchange_types import CcxtOrder, FtHas

logger = logging.getLogger(__name__)

class Bitget(Exchange):
    """
    Bitget exchange class. Contains adjustments needed for Freqtrade to work
    with this exchange.
    """

    _ft_has: FtHas = {
        "ohlcv_has_history": True,
        "order_time_in_force": ["GTC", "FOK", "IOC"],
        "ws_enabled": True,
        "trades_has_history": False,
        "fetch_orders_limit_minutes": 7 * 1440,  # 7 days
    }
    _ft_has_futures: FtHas = {
        "ohlcv_has_history": True,
        "mark_ohlcv_timeframe": "4h",
        "funding_fee_timeframe": "8h",
        "funding_fee_candle_limit": 200,
        "stoploss_on_exchange": True,
        "stoploss_order_types": {"limit": "limit", "market": "market"},
        "stoploss_blocks_assets": False,
        "stop_price_prop": "stopPx",  # Bitget API의 stop price 필드명
        "stop_price_type_field": "triggerType",  # Bitget API의 트리거 타입 필드
        "stop_price_type_value_mapping": {
            PriceType.LAST: "latest_price",
            PriceType.MARK: "mark_price",
            PriceType.INDEX: "index_price",
        },
        "exchange_has_overrides": {
            "fetchOrder": True,
        },
    }

    _supported_trading_mode_margin_pairs: list[tuple[TradingMode, MarginMode]] = [
        (TradingMode.FUTURES, MarginMode.CROSS),
        (TradingMode.FUTURES, MarginMode.ISOLATED),
    ]

    @property
    def _ccxt_config(self) -> dict:
        config = {}
        if self.trading_mode == TradingMode.SPOT:
            config.update({"options": {"defaultType": "spot"}})
        elif self.trading_mode == TradingMode.FUTURES:
            config.update({"options": {"defaultType": "swap"}})
        config.update(super()._ccxt_config)
        return config

    @retrier
    def additional_exchange_init(self) -> None:
        try:
            if not self._config["dry_run"]:
                # Bitget은 특별한 포지션 모드 설정이 필요없음 (CCXT가 처리)
                pass
        except ccxt.DDoSProtection as e:
            raise DDosProtection(e) from e
        except (ccxt.OperationFailed, ccxt.ExchangeError) as e:
            raise TemporaryError(
                f"Error in additional_exchange_init due to {e.__class__.__name__}. Message: {e}"
            ) from e
        except ccxt.BaseError as e:
            raise OperationalException(e) from e

    def market_is_future(self, market: dict[str, Any]) -> bool:
        main = super().market_is_future(market)
        # Bitget은 settle이 USDT/USDC 등인 swap만 지원
        return main and market.get("swap", False)

    def _lev_prep(self, pair: str, leverage: float, side: BuySell, accept_fail: bool = False):
        if self.trading_mode != TradingMode.SPOT:
            params = {"leverage": leverage}
            self.set_margin_mode(pair, self.margin_mode, accept_fail=True, params=params)
            self._set_leverage(leverage, pair, accept_fail=True)

    def _get_params(
        self,
        side: BuySell,
        ordertype: str,
        leverage: float,
        reduceOnly: bool,
        time_in_force: str = "GTC",
    ) -> dict:
        params = super()._get_params(
            side=side,
            ordertype=ordertype,
            leverage=leverage,
            reduceOnly=reduceOnly,
            time_in_force=time_in_force,
        )
        if self.trading_mode == TradingMode.FUTURES and self.margin_mode:
            params["positionIdx"] = 1  # Bitget의 포지션 인덱스(1=One-way)
        return params

    def _get_stop_params(self, side: BuySell, ordertype: str, stop_price: float) -> dict:
        params = super()._get_stop_params(
            side=side,
            ordertype=ordertype,
            stop_price=stop_price,
        )
        # Bitget의 TP/SL, 트레일링 스탑로스 파라미터 추가 필요시 여기에 구현
        return params

    def _order_needs_price(self, side: BuySell, ordertype: str) -> bool:
        # Bitget은 시장가 주문에 price 필요 없음
        return ordertype != "market"

    def fetch_positions(self, pair: str | None = None) -> list:
        logger = logging.getLogger(__name__)
        logger.info(f"[Bitget] fetch_positions called with pair: {pair}")
        if not pair:
            logger.warning("[Bitget] fetch_positions: pair is None or empty, calling self._api.fetch_positions() without symbols.")
            return self._api.fetch_positions()
        return super().fetch_positions(pair)