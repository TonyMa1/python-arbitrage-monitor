import os
import time
import asyncio
import traceback
import itertools
from typing import Dict, Tuple, List, Set
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import ccxt.pro as ccxtpro


def get_proxy_settings():
    """Return proxy configuration for exchange connections."""
    return {
        "enableRateLimit": True,
        "proxies": {
            "http": os.getenv("http_proxy"),
            "https": os.getenv("https_proxy"),
        },
        "aiohttp_proxy": os.getenv("http_proxy"),
        "ws_proxy": os.getenv("http_proxy"),
    }


def create_exchange(name):
    """Create and configure exchange instance with proxy settings."""
    return getattr(ccxtpro, name)(get_proxy_settings())


class SpreadMonitorBase:
    """Base class for arbitrage spread monitoring between exchanges."""
    
    def __init__(
        self, 
        market_a, 
        market_b, 
        symbols: Set[str] = None, 
        quote_currency: str = "USDT"
    ):
        self.exchange_a_name, self.type_a, self.subtype_a = self.parse_market(market_a)
        self.exchange_b_name, self.type_b, self.subtype_b = self.parse_market(market_b)

        self.exchange_a: ccxtpro.Exchange = create_exchange(self.exchange_a_name)
        self.exchange_b: ccxtpro.Exchange = create_exchange(self.exchange_b_name)

        self.symbols: Set[str] = symbols if symbols is not None else set()
        self.quote_currency: str = quote_currency

        self.symbol_map: Dict[str, Dict] = defaultdict(dict)
        self.pair_data: Dict[str, Dict] = {}
        self.monitor_tasks: List[asyncio.Task] = []
        self.running: bool = False

        self.latencies: Dict[str, Dict] = defaultdict(dict)
        self._executor = ThreadPoolExecutor(max_workers=4)

    async def sync_time(self, exchange: ccxtpro.Exchange):
        """Synchronize time with exchange and calculate latency."""
        while self.running:
            try:
                start_time = time.time() * 1000
                server_time = await exchange.fetch_time()
                end_time = time.time() * 1000

                rtt = end_time - start_time
                latency = rtt / 2
                time_diff = end_time - (server_time + latency)

                self.latencies[exchange.name.lower()] = {
                    "latency": latency,
                    "time_diff": time_diff,
                }
            except (asyncio.CancelledError, asyncio.exceptions.CancelledError):
                break
            except Exception as e:
                print(f"Error in sync_time ({exchange.name}): {e}")
                await asyncio.sleep(5)

    def parse_market(self, market: str) -> Tuple[str, str, str]:
        """Parse market string into exchange, type and subtype components."""
        market_params = market.split(".")
        if len(market_params) == 2:
            exchange_name, type_ = market_params
            return exchange_name, type_, None
        elif len(market_params) == 3:
            exchange_name, type_, subtype = market_params
            return exchange_name, type_, subtype
        else:
            raise ValueError(
                "Market parameter must match format: "
                "<exchange>.<type> or <exchange>.<type>.<subtype>"
            )

    async def load_markets(self):
        """Load market data from both exchanges and build symbol mapping."""
        await asyncio.gather(
            self.exchange_a.load_markets(),
            self.exchange_b.load_markets()
        )

        def format_markets(markets: Dict, type_: str, subtype: str) -> Dict[Tuple[str, str], List[str]]:
            """Filter markets by type, subtype and quote currency."""
            new_markets = defaultdict(list)
            for m in markets.values():
                if (
                    m["type"] == type_
                    and (subtype is None or m[subtype])
                    and (
                        m["quote"] == self.quote_currency
                        or (self.quote_currency is None and 
                            f"{m['base']}-{m['quote']}" in self.symbols)
                    )
                ):
                    new_markets[(m["base"], m["quote"])].append(m["symbol"])
            return new_markets

        markets_a = format_markets(self.exchange_a.markets, self.type_a, self.subtype_a)
        markets_b = format_markets(self.exchange_b.markets, self.type_b, self.subtype_b)

        keys = set(markets_a.keys()) & set(markets_b.keys())
        pairs = [
            {
                "base": base,
                "quote": quote,
                "symbols_a": markets_a[(base, quote)],
                "symbols_b": markets_b[(base, quote)],
            }
            for base, quote in keys
        ]
        self.symbol_map = await self._build_symbol_map_async(pairs)

    async def _build_symbol_map_async(self, pairs: List[Dict]) -> Dict[str, Dict]:
        """Build symbol mapping in a separate thread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._build_symbol_map, pairs)

    def _build_symbol_map(self, pairs: List[Dict]) -> Dict[str, Dict]:
        """Create mapping between exchange symbols and trading pairs."""
        symbol_map: Dict[str, Dict] = {"a": {}, "b": {}}
        for pair in pairs:
            for symbol_a, symbol_b in itertools.product(
                pair["symbols_a"], pair["symbols_b"]
            ):
                pair_name = f"{symbol_a}-{symbol_b}"
                symbol_map["a"].setdefault(
                    symbol_a, {"index": "a", "pair_names": []}
                )["pair_names"].append(pair_name)
                symbol_map["b"].setdefault(
                    symbol_b, {"index": "b", "pair_names": []}
                )["pair_names"].append(pair_name)
        return symbol_map

    async def monitor(self, exchange, index, symbols):
        """Abstract method to be implemented by subclasses."""
        raise NotImplementedError("Method is not implemented")

    def top(self, n: int) -> List[Dict]:
        """Return top n pairs sorted by spread percentage."""
        data = list(self.pair_data.values())
        return sorted(data, key=lambda x: x["spread_pct"], reverse=True)[:n]

    def start(self):
        """Start monitoring tasks for both exchanges."""
        self.running = True
        batch_size = 50
        a_symbols = list(self.symbol_map["a"].keys())
        b_symbols = list(self.symbol_map["b"].keys())

        self.monitor_tasks.extend([
            asyncio.create_task(self.sync_time(self.exchange_a)),
            asyncio.create_task(self.sync_time(self.exchange_b)),
        ])

        self.monitor_tasks.extend([
            asyncio.create_task(self.monitor(
                self.exchange_a, "a", a_symbols[i:i+batch_size]
            ))
            for i in range(0, len(a_symbols), batch_size)
        ])
        self.monitor_tasks.extend([
            asyncio.create_task(self.monitor(
                self.exchange_b, "b", b_symbols[i:i+batch_size]
            ))
            for i in range(0, len(b_symbols), batch_size)
        ])

    async def stop(self):
        """Stop all monitoring tasks and clean up resources."""
        self.running = False
        for task in self.monitor_tasks:
            if not task.done():
                task.cancel()
                
        try:
            await asyncio.gather(*self.monitor_tasks, return_exceptions=True)
        except (asyncio.CancelledError, Exception) as e:
            import logging
            logging.debug(f"Task cancellation during stop: {e}")
            
        try:
            if hasattr(self, 'exchange_a') and self.exchange_a:
                await self.exchange_a.close()
            if hasattr(self, 'exchange_b') and self.exchange_b:
                await self.exchange_b.close()
        except Exception as e:
            import logging
            logging.debug(f"Error closing exchanges: {e}")
            
        if hasattr(self, '_executor'):
            self._executor.shutdown(wait=False)


class TickerSpreadMonitor(SpreadMonitorBase):
    """Monitors price spreads between exchanges using ticker data."""
    
    async def monitor(self, exchange: ccxtpro.Exchange, index: str, symbols: List[str]):
        """Watch and process ticker updates for specified symbols."""
        exchange_name = exchange.name.lower()
        while self.running:
            try:
                tickers = await exchange.watch_tickers(symbols)
                await asyncio.gather(*[
                    self.process_ticker(
                        symbol, 
                        ticker, 
                        index, 
                        self.latencies[exchange_name].get("time_diff", 0)
                    )
                    for symbol, ticker in tickers.items()
                ])
            except asyncio.CancelledError:
                break
            except ccxtpro.NetworkError as e:
                print(f"Network error ({index}): {e}")
                await asyncio.sleep(5)
            except Exception as e:
                print(f"Exception ({index}): {traceback.format_exc()}")
                await asyncio.sleep(5)

    async def process_ticker(
        self, symbol: str, ticker: Dict, index: str, time_diff: float
    ):
        """Process a ticker update and update pair data."""
        if symbol not in self.symbol_map[index]:
            return

        pair_names = self.symbol_map[index][symbol]["pair_names"]

        for pair_name in pair_names:
            data = self.pair_data.setdefault(pair_name, {
                "pair_name": pair_name,
                "spread": 0.0,
                "spread_pct": 0.0,
                "price_a": 0.0,
                "price_b": 0.0,
                "elapsed_time_a": 0.0,
                "elapsed_time_b": 0.0,
            })
            data[f"price_{index}"] = ticker["last"]
            data[f"elapsed_time_{index}"] = time.time() * 1e3 - (ticker["timestamp"] + time_diff)
            await self.calculate_spread(pair_name)

    async def calculate_spread(self, pair_key: str):
        """Calculate price spread in a separate thread to avoid blocking."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._calculate_spread_sync, pair_key)

    def _calculate_spread_sync(self, pair_key: str):
        """Calculate absolute and percentage price spread between exchanges."""
        data = self.pair_data[pair_key]
        try:
            if data["price_a"] and data["price_b"]:
                min_price = min(data["price_a"], data["price_b"])
                spread = abs(data["price_a"] - data["price_b"])
                data["spread"] = spread
                data["spread_pct"] = spread / min_price if min_price != 0 else 0
        except TypeError as e:
            print(f"Calculate spread error for {pair_key}: {e}")
            data["spread_pct"] = 0