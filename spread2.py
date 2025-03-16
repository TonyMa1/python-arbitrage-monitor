import os
import time
import itertools
import traceback
import asyncio
import ccxt.pro as ccxtpro
from typing import Dict, Tuple, List, Set
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

# --- Constants and Configuration ---

def get_proxy_settings():
    return {
        "enableRateLimit": True,
        "proxies": {
            "http": os.getenv("http_proxy"),
            "https": os.getenv("https_proxy"),
        },
        "aiohttp_proxy": os.getenv("http_proxy"),  # Consider removing if not used with aiohttp directly
        "ws_proxy": os.getenv("http_proxy"),  # Consider removing if not used with websockets directly
    }

# --- Exchange Creation ---

def create_exchange(name):
    return getattr(ccxtpro, name)(get_proxy_settings())

# --- Base Class ---

class SpreadMonitorBase:
    def __init__(self, market_a, market_b, symbols: Set[str] = None, quote_currency: str = "USDT"):
        self.exchange_a_name, self.type_a, self.subtype_a = self.parse_market(market_a)
        self.exchange_b_name, self.type_b, self.subtype_b = self.parse_market(market_b)

        self.exchange_a: ccxtpro.Exchange = create_exchange(self.exchange_a_name)
        self.exchange_b: ccxtpro.Exchange = create_exchange(self.exchange_b_name)

        self.symbols: Set[str] = symbols if symbols is not None else set()
        self.quote_currency: str = quote_currency

        self.symbol_map: Dict[str, Dict] = defaultdict(dict)  # More precise type hint
        self.pair_data: Dict[str, Dict] = {}  # Use string keys, more efficient
        self.monitor_tasks: List[asyncio.Task] = []
        self.running: bool = False

        self.latencies: Dict[str, Dict] = defaultdict(dict)
        self._executor = ThreadPoolExecutor(max_workers=4)  # Thread pool for CPU-bound tasks


    async def sync_time(self, exchange: ccxtpro.Exchange):
        while self.running:
            try:
                start_time = time.time() * 1000
                # Use aiohttp_fetch for potentially faster fetching
                server_time = await exchange.fetch_time()
                end_time = time.time() * 1000

                rtt = end_time - start_time
                latency = rtt / 2
                time_diff = end_time - (server_time + latency)

                self.latencies[exchange.name.lower()] = {
                    "latency": latency,
                    "time_diff": time_diff,
                }
            except ccxtpro.NetworkError as e:
                print(f"Network error in sync_time ({exchange.name}): {e}")
                await asyncio.sleep(5) # Consider backoff
            except Exception as e:
                print(f"Exception in sync_time ({exchange.name}): {traceback.format_exc()}")
                await asyncio.sleep(10)

    def parse_market(self, market: str) -> Tuple[str, str, str]:  # More precise return type
        market_params = market.split(".")
        if len(market_params) == 2:
            exchange_name, type_ = market_params
            return exchange_name, type_, None
        elif len(market_params) == 3:
            exchange_name, type_, subtype = market_params
            return exchange_name, type_, subtype
        else:
            raise ValueError(
                "Market parameter must match format: <exchange>.<type> or <exchange>.<type>.<subtype>"
            )

    async def load_markets(self):
        await asyncio.gather(
            self.exchange_a.load_markets(),
            self.exchange_b.load_markets()
        )

        def format_markets(markets: Dict, type_: str, subtype: str) -> Dict[Tuple[str, str], List[str]]:
            new_markets = defaultdict(list)
            for m in markets.values():
                if (
                    m["type"] == type_
                    and (subtype is None or m[subtype])
                    and (
                        m["quote"] == self.quote_currency
                        or (self.quote_currency is None and f"{m['base']}-{m['quote']}" in self.symbols)
                    )
                ):
                    new_markets[(m["base"], m["quote"])].append(m["symbol"]) # type: ignore
            return new_markets

        markets_a = format_markets(self.exchange_a.markets, self.type_a, self.subtype_a)
        markets_b = format_markets(self.exchange_b.markets, self.type_b, self.subtype_b)

        # Use set operations for faster intersection
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
        self.symbol_map = await self._build_symbol_map_async(pairs) # build in async


    async def _build_symbol_map_async(self, pairs: List[Dict]) -> Dict[str, Dict]:
        # Use a thread pool for the CPU-bound symbol map building.
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._build_symbol_map, pairs)


    def _build_symbol_map(self, pairs: List[Dict]) -> Dict[str, Dict]:
        symbol_map: Dict[str, Dict] = {"a": {}, "b": {}}  # Pre-allocate dictionaries
        for pair in pairs:
            for symbol_a, symbol_b in itertools.product(pair["symbols_a"], pair["symbols_b"]):
                pair_name = f"{symbol_a}-{symbol_b}"
                # Use setdefault for concise dictionary updates
                symbol_map["a"].setdefault(symbol_a, {"index": "a", "pair_names": []})["pair_names"].append(pair_name)
                symbol_map["b"].setdefault(symbol_b, {"index": "b", "pair_names": []})["pair_names"].append(pair_name)
        return symbol_map

    async def monitor(self, exchange, index, symbols):
        raise NotImplementedError("Method is not implemented")

    def top(self, n: int) -> List[Dict]:
        # Use a list comprehension and pre-allocate if possible
        data = list(self.pair_data.values())  # No need for a generator if we iterate once
        return sorted(data, key=lambda x: x["spread_pct"], reverse=True)[:n]

    def start(self):
        self.running = True
        batch_size = 50
        a_symbols = list(self.symbol_map["a"].keys())
        b_symbols = list(self.symbol_map["b"].keys())

        self.monitor_tasks.extend([
            asyncio.create_task(self.sync_time(self.exchange_a)),
            asyncio.create_task(self.sync_time(self.exchange_b)),
        ])

        self.monitor_tasks.extend([
            asyncio.create_task(self.monitor(self.exchange_a, "a", a_symbols[i : i + batch_size]))
            for i in range(0, len(a_symbols), batch_size)
        ])
        self.monitor_tasks.extend([
            asyncio.create_task(self.monitor(self.exchange_b, "b", b_symbols[i : i + batch_size]))
            for i in range(0, len(b_symbols), batch_size)
        ])

    async def stop(self):
        self.running = False
        for task in self.monitor_tasks:
            if not task.done():
                task.cancel()
        try:
            await asyncio.gather(*self.monitor_tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass  # Expected on cancellation
        finally:
            await asyncio.gather(self.exchange_a.close(), self.exchange_b.close())
            self._executor.shutdown(wait=False)  # Shutdown the thread pool

# --- Ticker Spread Monitor ---

class TickerSpreadMonitor(SpreadMonitorBase):
    async def monitor(self, exchange: ccxtpro.Exchange, index: str, symbols: List[str]):
        exchange_name = exchange.name.lower()
        while self.running:
            try:
                tickers = await exchange.watch_tickers(symbols)
                await asyncio.gather(*[
                    self.process_ticker(symbol, ticker, index, self.latencies[exchange_name].get("time_diff", 0))
                    for symbol, ticker in tickers.items()
                ])
            except asyncio.CancelledError:
                break
            except ccxtpro.NetworkError as e:
                print(f"Network error ({index}): {e}")
                await asyncio.sleep(5)  # Consider backoff
            except Exception as e:
                print(f"Exception ({index}): {traceback.format_exc()}")
                await asyncio.sleep(5)

    async def process_ticker(self, symbol: str, ticker: Dict, index: str, time_diff: float):
        if symbol not in self.symbol_map[index]:
            return

        pair_names = self.symbol_map[index][symbol]["pair_names"]

        for pair_name in pair_names:
            # Use setdefault for atomic initialization and update
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
        # Perform calculations in a thread pool to avoid blocking the event loop.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._calculate_spread_sync, pair_key)

    def _calculate_spread_sync(self, pair_key:str):
        # This function now runs in a separate thread.
        data = self.pair_data[pair_key]
        try:
            if data["price_a"] and data["price_b"]:
                min_price = min(data["price_a"], data["price_b"])
                spread = abs(data["price_a"] - data["price_b"])
                data["spread"] = spread
                data["spread_pct"] = spread / min_price if min_price!=0 else 0 # avoid ZeroDivisionError
        except (TypeError) as e:  # Catch only relevant exceptions
            print(f"Calculate spread error for {pair_key}: {e}")
            data["spread_pct"] = 0  # Or some other default value